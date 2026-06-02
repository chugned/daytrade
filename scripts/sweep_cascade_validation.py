#!/usr/bin/env python3
"""Cross-asset OOS validation of the CASCADE_EXHAUSTION edge.

Reads cached 1m candles for a set of symbols (defaults to the major
crypto pairs the bot already has historical data for) and reports
per-symbol forward-return statistics at multiple horizons.

The overnight research found SOL: +9.7 bp at 30m on n=101 events.
This script re-runs that calculation across symbols + horizons so we
can see whether the edge is symbol-specific (just SOL), generalises to
other high-volatility tokens, or was sample-period luck.

Read-only. No orders, no live state touched.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List

from daytrade.models import OHLCV
from daytrade.research.cascade_validation import validate_cascade_edge
from daytrade.research.history import download_history


def _pull(symbol: str, days: int, interval: str = "1m") -> List[OHLCV]:
    """Pull from the cache (research.history caches Binance public klines)."""
    return download_history(symbol, interval=interval, days=days)


def _fmt_bps(v):
    return "—" if v is None else f"{v:+8.2f} bp"


def _fmt_pct(v):
    return "—" if v is None else f"{v * 100:5.1f}%"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols",
                   default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT,DOGEUSDT",
                   help="Comma-separated symbol list (default: 7 majors)")
    p.add_argument("--days", type=int, default=90,
                   help="Days of 1m history to validate over (default 90)")
    p.add_argument("--horizons", default="5,15,30,60",
                   help="Comma-separated horizons in minutes (default 5,15,30,60)")
    args = p.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]

    print(f"# Cascade exhaustion validation")
    print(f"# Symbols : {', '.join(symbols)}")
    print(f"# Days    : {args.days}")
    print(f"# Horizons: {horizons}m")
    print()

    print("Loading candles from cache...", file=sys.stderr)
    candles_by_symbol: Dict[str, List[OHLCV]] = {}
    for sym in symbols:
        try:
            candles_by_symbol[sym] = _pull(sym, days=args.days)
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN  {sym}: {exc}", file=sys.stderr)
    print(f"Loaded {len(candles_by_symbol)} symbol(s).", file=sys.stderr)

    if not candles_by_symbol:
        print("ERROR: no candles available; cache missing or empty.",
              file=sys.stderr)
        return 1

    # One table per horizon
    for horizon in horizons:
        print(f"## Horizon: {horizon}m")
        print()
        print(f"  {'symbol':<10}  {'bars':>6}  {'events':>7}  "
              f"{'mean ret':>11}  {'median':>11}  {'win%':>6}  "
              f"{'baseline':>11}  {'edge vs base':>13}")
        print("  " + "-" * 90)
        results = validate_cascade_edge(candles_by_symbol,
                                        horizon_minutes=horizon)
        for r in results:
            edge = None
            if (r.mean_forward_return_bps is not None
                    and r.baseline_mean_forward_return_bps is not None):
                edge = (r.mean_forward_return_bps
                        - r.baseline_mean_forward_return_bps)
            print(f"  {r.symbol:<10}  {r.n_bars:>6}  "
                  f"{r.exhaustion_count:>7}  "
                  f"{_fmt_bps(r.mean_forward_return_bps):>11}  "
                  f"{_fmt_bps(r.median_forward_return_bps):>11}  "
                  f"{_fmt_pct(r.win_rate):>6}  "
                  f"{_fmt_bps(r.baseline_mean_forward_return_bps):>11}  "
                  f"{_fmt_bps(edge):>13}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
