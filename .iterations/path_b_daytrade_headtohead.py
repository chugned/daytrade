"""Head-to-head: real daytrade Backtester vs Freqtrade community strategies.

Same window (2026-03-01 → 2026-06-01), same 6 pairs, same starting
capital ($1000-equivalent). Runs the FULL daytrade pipeline — features,
labels, ML, fusion, gates, paper broker — not the freqtrade port.
The freqtrade community-strategy results came from the previous run."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import pandas as pd

from daytrade.backtest import Backtester
from daytrade.config import load_config
from daytrade.research.history import download_history


PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"]
START = datetime(2026, 3, 1, tzinfo=timezone.utc)
END   = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _slice(candles, start, end):
    return [c for c in candles
            if start <= c.timestamp.replace(tzinfo=timezone.utc) < end]


def main() -> int:
    cfg = load_config(load_dotenv_file=False)

    # Try the actual native TF (1h is what last night's research showed
    # most signal at, and the Backtester is timeframe-agnostic).
    for tf in ("1h",):
        print(f"\n=== daytrade Backtester  {tf}  2026-03-01 → 2026-06-01 ===")
        print(f"{'Pair':<10} {'Bars':>6} {'TotalRet':>10} {'WinRate':>8} "
              f"{'Trades':>7} {'MaxDD':>7} {'Sharpe~':>8} {'Notes':<30}")
        agg = {"total_ret_sum": 0.0, "trades": 0, "wins": 0, "max_dd": 0.0,
               "n_pairs": 0}
        for sym in PAIRS:
            # Pull enough history to cover the window plus warmup.
            candles = download_history(sym, interval=tf, days=120)
            candles = _slice(candles, START, END)
            if len(candles) < 100:
                print(f"  {sym:<8} insufficient bars ({len(candles)})")
                continue
            bt = Backtester(cfg).run(candles).metrics
            notes = ""
            if any("OVERFIT" in w or "Sharpe" in w for w in bt.warnings):
                notes = "OVERFIT flag"
            print(f"  {sym:<8} {len(candles):>6} "
                  f"{bt.total_return_pct:>+8.2f}% {bt.win_rate*100:>6.1f}% "
                  f"{bt.total_trades:>7} {bt.max_drawdown_pct:>5.2f}% "
                  f"{bt.sharpe_like:>7.2f}  {notes:<30}")
            agg["total_ret_sum"] += bt.total_return_pct
            agg["trades"] += bt.total_trades
            agg["wins"] += bt.winning_trades
            agg["max_dd"] = max(agg["max_dd"], bt.max_drawdown_pct)
            agg["n_pairs"] += 1
        if agg["n_pairs"]:
            mean_ret = agg["total_ret_sum"] / agg["n_pairs"]
            win = agg["wins"] / agg["trades"] * 100 if agg["trades"] else 0
            print(f"  {'MEAN':<8} {'':>6} {mean_ret:>+8.2f}% {win:>6.1f}% "
                  f"{agg['trades']:>7} {agg['max_dd']:>5.2f}%  "
                  f"(portfolio = equal-weight 1/{agg['n_pairs']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
