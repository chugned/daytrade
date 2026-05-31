#!/usr/bin/env python3
"""Sweep: does a BTC/ETH or BTC/SOL pair-trade carry an out-of-sample edge?

Pulls aligned 1m close-price arrays from data-api.binance.vision and runs
the rolling-fit backtest from observatory.pairs. The whole point is to
sweep a couple of (lookback, entry_z) settings and observe whether the
pair signal even produces trades, and whether those trades win on average.

PAPER / RESEARCH ONLY.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Tuple

from daytrade.observatory.pairs import backtest_pair, fit_pair


def _fetch_1m_closes(symbol: str, limit: int) -> Tuple[List[int], List[float]]:
    url = ("https://data-api.binance.vision/api/v3/klines"
           f"?symbol={symbol}&interval=1m&limit={limit}")
    with urllib.request.urlopen(url, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    ts: List[int] = []
    closes: List[float] = []
    for k in payload:
        try:
            ts.append(int(k[0]))
            closes.append(float(k[4]))
        except (ValueError, TypeError, IndexError):
            continue
    return ts, closes


def _align(a_ts: List[int], a_px: List[float],
           b_ts: List[int], b_px: List[float]
           ) -> Tuple[List[float], List[float]]:
    bd: Dict[int, float] = dict(zip(b_ts, b_px))
    out_a: List[float] = []
    out_b: List[float] = []
    for t, p in zip(a_ts, a_px):
        if t in bd:
            out_a.append(p)
            out_b.append(bd[t])
    return out_a, out_b


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--y", default="ETHUSDT", help="Y leg (numerator)")
    p.add_argument("--x", default="BTCUSDT", help="X leg (denominator)")
    p.add_argument("--limit", type=int, default=1000)
    args = p.parse_args()

    try:
        y_ts, y_px = _fetch_1m_closes(args.y, args.limit)
        x_ts, x_px = _fetch_1m_closes(args.x, args.limit)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"ERROR: fetch failed: {exc}", file=sys.stderr)
        return 1

    y_al, x_al = _align(y_ts, y_px, x_ts, x_px)
    if len(y_al) < 400:
        print(f"ERROR: only {len(y_al)} aligned bars", file=sys.stderr)
        return 1
    print(f"Aligned bars: {len(y_al)} for {args.y} vs {args.x}")

    full_fit = fit_pair(y_al, x_al)
    print(f"\nFull-sample fit (diagnostic; not for trading):")
    print(f"  beta = {full_fit.beta:.4f}")
    print(f"  spread mean / std = {full_fit.spread_mean:+.4f} / "
          f"{full_fit.spread_std:.4f}")
    print(f"  ADF p-value = {full_fit.adf_pvalue:.4f}  "
          f"(cointegrated? {full_fit.is_cointegrated})")

    print("\nRolling-OOS backtest grid (paper, no orders):")
    print(f"  {'lookback':>9}  {'entry_z':>7}  {'exit_z':>6}  "
          f"{'trades':>6}  {'win%':>5}  {'totalPnL':>9}  {'maxDD':>7}")
    for lookback in (240, 360, 480):
        for entry_z, exit_z in ((1.5, 0.3), (2.0, 0.5), (2.5, 0.5)):
            res = backtest_pair(y_al, x_al, lookback=lookback,
                                entry_z=entry_z, exit_z=exit_z,
                                refit_every=60)
            print(f"  {lookback:>9}  {entry_z:>7.1f}  {exit_z:>6.1f}  "
                  f"{res.trades:>6}  "
                  f"{res.win_rate * 100:>4.1f}  "
                  f"{res.total_pnl:>+9.4f}  {res.max_drawdown:>7.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
