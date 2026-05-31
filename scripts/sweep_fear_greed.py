#!/usr/bin/env python3
"""Sweep: does the Fear & Greed Index actually carry an edge?

Pulls the daily F&G history from alternative.me (free, no key) and BTC
daily closes from data-api.binance.vision (paper-research read-only), then
computes the forward 7-day return at each F&G regime bucket.

Verdict: if extreme-fear days really do precede higher forward returns
(and/or extreme-greed days precede lower ones) by a margin that survives
sample sizes in the 100s, the gate is worth turning on. If the buckets
flatten out, the gate stays off — we don't gate on noise.

Paper / simulation only.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Tuple


def _fetch_fg_history(limit: int) -> List[Tuple[int, float]]:
    url = f"https://api.alternative.me/fng/?limit={limit}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows: List[Tuple[int, float]] = []
    for entry in payload.get("data", []):
        try:
            ts = int(entry["timestamp"])
            val = float(entry["value"])
        except (KeyError, ValueError, TypeError):
            continue
        rows.append((ts, val))
    rows.sort(key=lambda r: r[0])
    return rows


def _fetch_btc_daily(limit: int) -> List[Tuple[int, float]]:
    # Binance public read-only — same source as the rest of the project.
    url = ("https://data-api.binance.vision/api/v3/klines"
           f"?symbol=BTCUSDT&interval=1d&limit={limit}")
    with urllib.request.urlopen(url, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows: List[Tuple[int, float]] = []
    for k in payload:
        # kline schema: [open_time, o, h, l, c, ...]
        try:
            open_time_ms = int(k[0])
            close_px = float(k[4])
        except (IndexError, ValueError, TypeError):
            continue
        # F&G timestamps are UTC midnight; align by truncating to day.
        day_ts = int(datetime.fromtimestamp(open_time_ms / 1000,
                                            tz=timezone.utc)
                     .replace(hour=0, minute=0, second=0, microsecond=0)
                     .timestamp())
        rows.append((day_ts, close_px))
    rows.sort(key=lambda r: r[0])
    return rows


def _bucket(value: float) -> str:
    if value <= 20:
        return "EXTREME_FEAR (<=20)"
    if value <= 40:
        return "FEAR (21-40)"
    if value < 60:
        return "NEUTRAL (41-59)"
    if value < 80:
        return "GREED (60-79)"
    return "EXTREME_GREED (>=80)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon-days", type=int, default=7,
                        help="Forward-return horizon (default 7)")
    parser.add_argument("--limit", type=int, default=1500,
                        help="Number of daily F&G points (max 2000)")
    args = parser.parse_args()

    try:
        fg = _fetch_fg_history(args.limit)
        btc = _fetch_btc_daily(args.limit + args.horizon_days + 5)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"ERROR: network fetch failed: {exc}", file=sys.stderr)
        return 1

    btc_by_day: Dict[int, float] = {ts: px for ts, px in btc}
    by_bucket: Dict[str, List[float]] = {}

    for ts, value in fg:
        future_ts = ts + args.horizon_days * 86400
        px_now = btc_by_day.get(ts)
        px_then = btc_by_day.get(future_ts)
        if px_now is None or px_then is None or px_now <= 0:
            continue
        ret = (px_then - px_now) / px_now
        by_bucket.setdefault(_bucket(value), []).append(ret)

    bucket_order = ["EXTREME_FEAR (<=20)", "FEAR (21-40)",
                    "NEUTRAL (41-59)", "GREED (60-79)",
                    "EXTREME_GREED (>=80)"]
    width = max(len(b) for b in bucket_order)

    print(f"Fear & Greed → BTC forward {args.horizon_days}-day return")
    print(f"{'Bucket':<{width}}  {'n':>5}  {'mean':>8}  {'median':>8}  "
          f"{'win%':>6}")
    print("-" * (width + 36))

    overall: List[float] = []
    for name in bucket_order:
        rs = by_bucket.get(name, [])
        if not rs:
            print(f"{name:<{width}}  {'0':>5}  {'-':>8}  {'-':>8}  {'-':>6}")
            continue
        rs_sorted = sorted(rs)
        mean = sum(rs) / len(rs)
        median = rs_sorted[len(rs_sorted) // 2]
        wins = sum(1 for r in rs if r > 0)
        print(f"{name:<{width}}  {len(rs):>5}  {mean * 100:>7.2f}%  "
              f"{median * 100:>7.2f}%  {wins / len(rs) * 100:>5.1f}%")
        overall.extend(rs)

    if overall:
        mean_all = sum(overall) / len(overall)
        print("-" * (width + 36))
        print(f"{'(all)':<{width}}  {len(overall):>5}  {mean_all * 100:>7.2f}%")

    # Simple verdict heuristic: contrarian thesis holds if extreme-fear mean
    # is meaningfully above the all-bucket mean AND extreme-greed is below.
    fear_mean = (sum(by_bucket.get("EXTREME_FEAR (<=20)", []))
                 / max(1, len(by_bucket.get("EXTREME_FEAR (<=20)", []))))
    greed_mean = (sum(by_bucket.get("EXTREME_GREED (>=80)", []))
                  / max(1, len(by_bucket.get("EXTREME_GREED (>=80)", []))))
    all_mean = sum(overall) / max(1, len(overall))

    print()
    print("Contrarian-thesis check:")
    print(f"  extreme-fear mean   = {fear_mean * 100:+.2f}%")
    print(f"  baseline (all) mean = {all_mean * 100:+.2f}%")
    print(f"  extreme-greed mean  = {greed_mean * 100:+.2f}%")
    edge_fear = fear_mean - all_mean
    edge_greed = all_mean - greed_mean
    print(f"  fear edge over baseline   = {edge_fear * 100:+.2f}%")
    print(f"  greed underperf vs baseline = {edge_greed * 100:+.2f}%")
    if edge_fear > 0.005 and edge_greed > 0.005:
        print("  -> contrarian thesis SUPPORTED on this sample.")
    else:
        print("  -> contrarian thesis NOT supported by this sample; "
              "keep the gate OFF.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
