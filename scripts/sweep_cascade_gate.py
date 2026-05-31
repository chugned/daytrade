#!/usr/bin/env python3
"""Sweep: does the cascade detector carry an edge?

For each detected ``CASCADE_ACTIVE`` bar on BTCUSDT 1m data, measure the
forward N-minute return; same for ``CASCADE_EXHAUSTION``. Compare to the
baseline forward return over all bars. Verdict: the gates are only worth
turning on if cascade-active bars have meaningfully negative forward
returns (block-buy thesis) and exhaustion bars have meaningfully positive
forward returns (mean-revert thesis).

Paper / research only.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import List

from daytrade.models import OHLCV
from daytrade.observatory.liquidation_cascade import (
    CascadeState, detect_cascade,
)


def _fetch_1m(symbol: str, limit: int) -> List[OHLCV]:
    url = ("https://data-api.binance.vision/api/v3/klines"
           f"?symbol={symbol}&interval=1m&limit={limit}")
    with urllib.request.urlopen(url, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    out: List[OHLCV] = []
    for k in payload:
        try:
            ts = datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc)
            out.append(OHLCV(
                symbol=symbol, timestamp=ts,
                open=float(k[1]), high=float(k[2]),
                low=float(k[3]), close=float(k[4]),
                volume=float(k[5]),
            ))
        except (ValueError, TypeError, IndexError):
            continue
    return out


def _forward_return(candles: List[OHLCV], i: int, horizon: int) -> float | None:
    j = i + horizon
    if j >= len(candles):
        return None
    px_now = candles[i].close
    px_then = candles[j].close
    if px_now <= 0:
        return None
    return (px_then - px_now) / px_now


def _summarise(label: str, returns: List[float], total: int) -> None:
    if not returns:
        print(f"  {label:<22} n=0")
        return
    mean = sum(returns) / len(returns)
    wins = sum(1 for r in returns if r > 0)
    median = sorted(returns)[len(returns) // 2]
    pct = len(returns) / total * 100 if total else 0
    print(f"  {label:<22} n={len(returns):4d} ({pct:4.1f}%)  "
          f"mean={mean * 100:+6.3f}%  median={median * 100:+6.3f}%  "
          f"win%={wins / len(returns) * 100:4.1f}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--limit", type=int, default=1000,
                   help="Number of 1m bars to pull (Binance max 1000)")
    p.add_argument("--horizon", type=int, default=15,
                   help="Forward-return horizon in 1m bars")
    args = p.parse_args()

    try:
        candles = _fetch_1m(args.symbol, args.limit)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"ERROR: fetch failed: {exc}", file=sys.stderr)
        return 1
    if len(candles) < 100:
        print(f"ERROR: not enough candles ({len(candles)})", file=sys.stderr)
        return 1

    active_returns: List[float] = []
    exhaustion_returns: List[float] = []
    baseline_returns: List[float] = []

    # Walk forward; require enough history for the detector at each step.
    for i in range(30, len(candles) - args.horizon):
        window = candles[: i + 1]
        reading = detect_cascade(window)
        fwd = _forward_return(candles, i, args.horizon)
        if fwd is None:
            continue
        baseline_returns.append(fwd)
        if reading.state is CascadeState.CASCADE_ACTIVE:
            active_returns.append(fwd)
        elif reading.state is CascadeState.CASCADE_EXHAUSTION:
            exhaustion_returns.append(fwd)

    total = len(baseline_returns)
    print(f"{args.symbol} 1m × {len(candles)} bars  "
          f"(horizon {args.horizon}m, bars eval={total})")
    _summarise("baseline (all)", baseline_returns, total)
    _summarise("CASCADE_ACTIVE", active_returns, total)
    _summarise("CASCADE_EXHAUSTION", exhaustion_returns, total)

    print()
    print("Edge check:")
    if active_returns and baseline_returns:
        d = (sum(active_returns) / len(active_returns)
             - sum(baseline_returns) / len(baseline_returns))
        print(f"  active edge vs baseline (negative = blocks bad longs): "
              f"{d * 100:+.3f}%")
    if exhaustion_returns and baseline_returns:
        d = (sum(exhaustion_returns) / len(exhaustion_returns)
             - sum(baseline_returns) / len(baseline_returns))
        print(f"  exhaustion edge vs baseline (positive = good entries): "
              f"{d * 100:+.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
