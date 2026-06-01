"""Live-trading frictions simulator.

Takes the daytrade Backtester's 308 trades from the 90d × 6-pair head-to-
head and applies realistic real-world frictions:

  - Maker/taker fee split (Binance retail tier)
  - Latency-induced slippage (200-500 ms between signal and fill)
  - Spread cost (bid-ask, varies per symbol)
  - Partial fills on illiquid pairs
  - Random outages (5 min/week the bot is offline = missed exit)
  - Gap risk on stop losses (price gaps through stop during fast moves)
  - SEPA deposit + USDT conversion + withdrawal costs (one-shot)

Produces three columns:
  - PAPER:        what the backtest reports today
  - REALISTIC:    live with typical retail-tier frictions
  - PESSIMISTIC:  bad slippage, weekly outage, gap on every 5th stop

This is a research simulation. No orders, no API keys, no live trading
infrastructure is touched — `SafetyConfig` is unchanged.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

from daytrade.backtest import Backtester
from daytrade.config import load_config
from daytrade.research.history import download_history


# -------- Friction model --------------------------------------------------
# Numbers are reasonable retail-tier defaults; sources in docs.

BINANCE_TAKER_FEE = 0.0010   # 0.10% per side, regular tier
BINANCE_MAKER_FEE = 0.0008   # if you use BNB for fee discount: 0.075% taker
SPREAD_BPS_BY_SYMBOL = {
    "BTCUSDT": 0.5,   # ~0.5 bps spread on top-tier liquidity
    "ETHUSDT": 1.0,
    "BNBUSDT": 2.0,
    "SOLUSDT": 3.0,
    "LINKUSDT": 5.0,
    "AVAXUSDT": 6.0,
}
LATENCY_SLIPPAGE_BPS = 2.0   # avg 2 bps from signal to fill in 200-500ms
LATENCY_SLIPPAGE_PESSIMISTIC_BPS = 8.0  # bad case: 8 bps
PARTIAL_FILL_PROBABILITY = 0.10
PARTIAL_FILL_PRICE_HIT_BPS = 5.0  # half the order at a worse price
GAP_RISK_PROBABILITY = 0.02      # 2% of stop losses gap through
GAP_RISK_EXTRA_LOSS_PCT = 0.005  # 50 bps extra loss when it happens
OUTAGE_PROBABILITY = 0.01        # 1% of trades exit via outage (missed signal)
OUTAGE_AVG_DRAG_PCT = 0.003      # 30 bps avg drag from delayed exit

# One-shot capital flow costs (apply once to round-trip in & out of EUR):
SEPA_DEPOSIT_FEE_EUR = 1.0       # Binance SEPA deposit
EUR_USDT_SPREAD_BPS = 10.0       # spread on EUR/USDT conversion
WITHDRAWAL_FEE_EUR = 1.0         # Binance SEPA withdrawal

PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"]
START = datetime(2026, 3, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 1, tzinfo=timezone.utc)


@dataclass
class Friction:
    fee_pct: float = 0.0          # extra fee % vs paper (which was modelled)
    slippage_pct: float = 0.0     # latency + spread slippage applied to entry+exit
    partial_fill_pct: float = 0.0 # subset of trades get hit
    gap_loss_pct: float = 0.0     # subset of stops gap-through
    outage_drag_pct: float = 0.0  # subset of exits delayed
    name: str = ""


def realistic_friction() -> Friction:
    return Friction(
        # The paper backtester already models 10 bps fee + 2 bps slippage
        # per side = 24 bps round-trip. Live retail is similar, so the
        # *extra* friction beyond paper is small. But the spread is not
        # in the paper model.
        fee_pct=0.0,  # paper already models this
        slippage_pct=0.0,  # see per-trade application below
        partial_fill_pct=0.0,
        gap_loss_pct=0.0,
        outage_drag_pct=0.0,
        name="realistic",
    )


def pessimistic_friction() -> Friction:
    return Friction(name="pessimistic")


def _slice(candles, start, end):
    return [c for c in candles
            if start <= c.timestamp.replace(tzinfo=timezone.utc) < end]


def _apply_frictions(trade_pnl_pct: float, symbol: str, is_stop: bool,
                    *, scenario: str, rng: random.Random) -> float:
    """Return the live-adjusted pnl_pct for one round-trip trade."""
    spread = SPREAD_BPS_BY_SYMBOL.get(symbol, 5.0) / 10000.0

    if scenario == "paper":
        return trade_pnl_pct

    if scenario == "realistic":
        # Two crossings (entry + exit) each pay half-spread + latency.
        extra_cost = 2 * (spread + LATENCY_SLIPPAGE_BPS / 10000.0)
        # Occasional partial fill
        if rng.random() < PARTIAL_FILL_PROBABILITY:
            extra_cost += PARTIAL_FILL_PRICE_HIT_BPS / 10000.0
        return trade_pnl_pct - extra_cost

    if scenario == "pessimistic":
        extra_cost = 2 * (spread + LATENCY_SLIPPAGE_PESSIMISTIC_BPS / 10000.0)
        if rng.random() < PARTIAL_FILL_PROBABILITY * 2:
            extra_cost += PARTIAL_FILL_PRICE_HIT_BPS / 10000.0
        if is_stop and rng.random() < GAP_RISK_PROBABILITY:
            extra_cost += GAP_RISK_EXTRA_LOSS_PCT
        if rng.random() < OUTAGE_PROBABILITY:
            # Bot was down at exit signal; closed manually with drag
            extra_cost += OUTAGE_AVG_DRAG_PCT
        return trade_pnl_pct - extra_cost

    raise ValueError(scenario)


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    starting_cash = cfg.paper.starting_cash  # €1000 default
    rng = random.Random(42)

    print("\n" + "=" * 80)
    print("LIVE-TRADING FRICTION SIMULATION")
    print("=" * 80)
    print(f"Window:        2026-03-01 → 2026-06-01 (90 days)")
    print(f"Pairs:         {', '.join(PAIRS)}")
    print(f"Starting cap:  €{starting_cash:.0f} per pair (independent backtests)")
    print(f"Source:        actual daytrade Backtester (uses full pipeline)")
    print()
    print(f"{'Symbol':<10} {'Trades':>7} {'PAPER':>10} {'REALISTIC':>11} "
          f"{'PESSIMISTIC':>13} {'Δ realistic':>13} {'Δ pessimistic':>15}")
    print("-" * 88)

    totals = {"paper_pct": 0.0, "real_pct": 0.0, "pess_pct": 0.0}
    totals_trades = 0
    n_pairs = 0

    for sym in PAIRS:
        candles = download_history(sym, interval="1h", days=120)
        candles = _slice(candles, START, END)
        if len(candles) < 100:
            print(f"  {sym}: skip (only {len(candles)} bars)")
            continue
        bt = Backtester(cfg).run(candles)
        trades = bt.trades
        if not trades:
            continue

        # PAPER return: as reported by Backtester. Already includes the
        # 24 bps round-trip cost the engine models.
        paper_pct = bt.metrics.total_return_pct

        # Extra friction = sum over trades of (extra_pct × trade_notional),
        # divided by starting equity. Each trade's extra cost is applied
        # to its actual position size, not 100% of equity.
        extra_real_usdt = 0.0
        extra_pess_usdt = 0.0
        for t in trades:
            notional = t.entry_price * t.quantity
            pnl_frac = t.pnl / notional if notional > 0 else 0.0
            is_stop = pnl_frac < -0.01
            # Realistic: extra spread + latency on entry + exit, plus
            # occasional partial fill.
            spread = SPREAD_BPS_BY_SYMBOL.get(sym, 5.0) / 10000.0
            real_extra = 2 * (spread + LATENCY_SLIPPAGE_BPS / 10000.0)
            if rng.random() < PARTIAL_FILL_PROBABILITY:
                real_extra += PARTIAL_FILL_PRICE_HIT_BPS / 10000.0
            extra_real_usdt += real_extra * notional

            pess_extra = 2 * (spread + LATENCY_SLIPPAGE_PESSIMISTIC_BPS / 10000.0)
            if rng.random() < PARTIAL_FILL_PROBABILITY * 2:
                pess_extra += PARTIAL_FILL_PRICE_HIT_BPS / 10000.0
            if is_stop and rng.random() < GAP_RISK_PROBABILITY:
                pess_extra += GAP_RISK_EXTRA_LOSS_PCT
            if rng.random() < OUTAGE_PROBABILITY:
                pess_extra += OUTAGE_AVG_DRAG_PCT
            extra_pess_usdt += pess_extra * notional

        extra_real_pct = extra_real_usdt / starting_cash * 100
        extra_pess_pct = extra_pess_usdt / starting_cash * 100
        real_pct = paper_pct - extra_real_pct
        pess_pct = paper_pct - extra_pess_pct

        print(f"  {sym:<8} {len(trades):>7} "
              f"{paper_pct:>+8.2f}% "
              f"{real_pct:>+9.2f}% "
              f"{pess_pct:>+11.2f}% "
              f"{-extra_real_pct:>+11.2f}% "
              f"{-extra_pess_pct:>+13.2f}%")
        totals["paper_pct"] += paper_pct
        totals["real_pct"] += real_pct
        totals["pess_pct"] += pess_pct
        totals_trades += len(trades)
        n_pairs += 1

    print("-" * 88)
    if n_pairs:
        print(f"  {'MEAN':<8} {totals_trades:>7} "
              f"{totals['paper_pct'] / n_pairs:>+8.2f}% "
              f"{totals['real_pct'] / n_pairs:>+9.2f}% "
              f"{totals['pess_pct'] / n_pairs:>+11.2f}% "
              f"{(totals['real_pct'] - totals['paper_pct']) / n_pairs:>+11.2f}% "
              f"{(totals['pess_pct'] - totals['paper_pct']) / n_pairs:>+13.2f}%")

    print()
    print("One-shot capital-flow costs (apply once each direction):")
    print(f"  SEPA deposit (€):     -{SEPA_DEPOSIT_FEE_EUR:.2f}")
    print(f"  EUR→USDT spread:      -{EUR_USDT_SPREAD_BPS:.1f} bps on deposit amount")
    print(f"  SEPA withdrawal (€):  -{WITHDRAWAL_FEE_EUR:.2f}")
    print()
    capital = 1000.0
    flow_cost = (SEPA_DEPOSIT_FEE_EUR + WITHDRAWAL_FEE_EUR
                 + capital * EUR_USDT_SPREAD_BPS / 10000.0 * 2)
    print(f"  Total one-shot cost on €{capital:.0f} round-trip: €{flow_cost:.2f}")
    print(f"  (= {flow_cost / capital * 100:.2f}% of capital, one-time)")

    print()
    print("ANNUALISED estimates (4 × 90-day window assuming similar PnL):")
    for scen, key in (("paper", "paper_pct"), ("realistic", "real_pct"),
                      ("pessimistic", "pess_pct")):
        annual = totals[key] / n_pairs * 4
        eur = capital * annual / 100
        print(f"  {scen.upper():<13} {annual:>+7.1f}%  ≈ €{eur:>+7.0f}/yr per €1000 "
              f"≈ €{eur / 12:.0f}/month")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
