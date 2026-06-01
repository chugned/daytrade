"""Iteration 23: turn 'momentum > RF' into a tradeable PnL number.

Directional accuracy isn't PnL. A 53% momentum-follow could still
lose money to fees & slippage. Build a simple paper PnL simulator
that follows momentum: enter long if last bar was up, hold for the
label horizon, exit. Compare net PnL across symbols and windows."""

from __future__ import annotations

import statistics
from typing import List

import numpy as np
import pandas as pd

from daytrade.config import load_config
from daytrade.research.history import download_history


# Apply realistic costs in line with BacktestConfig: 10 bps fee per side,
# 2 bps slippage per side -> round-trip cost = 24 bps = 0.0024.
ROUND_TRIP_COST = 0.0024


def momentum_backtest(close: pd.Series, *, horizon: int) -> dict:
    """For every bar t where close[t] > close[t-1], hold from t to t+horizon
    and book the price change minus round-trip cost."""
    returns = close.pct_change()
    decisions = (returns > 0).astype(int)  # 1 = go long at bar t
    exits = close.shift(-horizon)  # price at exit
    entries = close
    gross_pnl = (exits - entries) / entries  # fractional return per trade
    # Only take trades where decision == 1
    trade_mask = (decisions == 1) & gross_pnl.notna()
    pnls = gross_pnl[trade_mask] - ROUND_TRIP_COST
    if len(pnls) == 0:
        return {"n_trades": 0}
    wins = (pnls > 0).sum()
    return {
        "n_trades": int(len(pnls)),
        "win_rate": float(wins / len(pnls)),
        "mean_pnl_bps": float(pnls.mean() * 10000),
        "total_pnl_pct": float(pnls.sum() * 100),
        "stdev_bps": float(pnls.std() * 10000),
        "sharpe_per_trade": (
            float(pnls.mean() / pnls.std()) if pnls.std() > 0 else 0.0
        ),
    }


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    print(f"Round-trip cost applied: {ROUND_TRIP_COST * 10000:.0f} bps")
    print(f"{'Symbol':<10} {'Days':>5} {'Bars':>6} {'Horiz':>6} "
          f"{'#trd':>5} {'win%':>5} {'meanPnL':>9} {'totalPnL':>10}")
    syms = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT",
            "LTCUSDT", "AVAXUSDT", "LINKUSDT")
    for sym in syms:
        for days in (365, 730):
            try:
                candles = download_history(sym, interval="1h", days=days)
            except Exception:
                continue
            close = pd.Series([c.close for c in candles])
            # Try a couple of holding horizons
            for h in (5, 20):
                r = momentum_backtest(close, horizon=h)
                if r.get("n_trades", 0) == 0:
                    continue
                print(f"  {sym:<8} {days:>5} {len(candles):>6} h={h:>2} "
                      f"{r['n_trades']:>5} {r['win_rate']*100:>4.1f}% "
                      f"{r['mean_pnl_bps']:>+7.2f}bp "
                      f"{r['total_pnl_pct']:>+9.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
