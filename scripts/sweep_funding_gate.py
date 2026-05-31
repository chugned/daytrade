#!/usr/bin/env python3
"""Empirically validate the funding-rate gate on real history.

Pulls historical 8-hour funding-rate records from Binance perp futures
for each symbol, then aligns them with the spot price movement over the
NEXT 24 hours. If extreme positive funding genuinely precedes pullbacks,
the forward-return mean conditional on high funding should be negative
(or at least worse than the unconditional mean).

This is the same discipline applied to every other knob in the system:
do not flip the gate on until the data confirms the signal.

Usage::

    PYTHONPATH=src python3 scripts/sweep_funding_gate.py \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT --history 500

Read-only — touches Binance public endpoints only, never the live bot.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

import numpy as np  # noqa: E402

from daytrade.observatory.funding import (  # noqa: E402
    fetch_funding_history,
)
from daytrade.research.history import download_history  # noqa: E402


def evaluate_symbol(symbol: str, history_limit: int, fwd_hours: int):
    """For one symbol, bucket forward 24h returns by funding-rate regime."""
    hist = fetch_funding_history(symbol, limit=history_limit)
    if len(hist) < 30:
        return None
    # We need spot price klines covering the same period to compute forward returns.
    span_days = max(1, int(history_limit / 3 + 1))   # 3 funding events / day
    klines = download_history(symbol, interval="1h", days=span_days)
    if not klines:
        return None
    closes_by_ts = {int(k.timestamp.timestamp() * 1000): k.close for k in klines}
    sorted_ts = sorted(closes_by_ts)

    def _close_at(ts_ms: int):
        """Nearest 1h close at-or-after ``ts_ms``."""
        from bisect import bisect_left
        i = bisect_left(sorted_ts, ts_ms)
        if i >= len(sorted_ts):
            return None
        return closes_by_ts[sorted_ts[i]]

    rates: List[float] = []
    fwd_returns: List[float] = []
    for snap in hist:
        spot_now = _close_at(snap.timestamp_ms)
        spot_fwd = _close_at(snap.timestamp_ms + fwd_hours * 3600_000)
        if spot_now is None or spot_fwd is None or spot_now <= 0:
            continue
        rates.append(snap.rate)
        fwd_returns.append((spot_fwd / spot_now - 1.0) * 100)
    if not rates:
        return None
    return np.array(rates), np.array(fwd_returns)


def bucket_report(rates: np.ndarray, fwd: np.ndarray,
                  thresholds_pos: List[float]) -> str:
    """Per-threshold table: how does forward return change with extreme funding?"""
    lines = [
        "| funding regime               | n   | mean fwd 24h return | "
        "lift vs baseline |",
        "|---|---:|---:|---:|",
    ]
    baseline = float(fwd.mean()) if len(fwd) else 0.0
    lines.append(
        f"| baseline (all funding values) | {len(fwd)} | "
        f"{baseline:+.3f}% | — |")
    for thr in thresholds_pos:
        mask = rates >= thr
        n = int(mask.sum())
        if n == 0:
            continue
        m = float(fwd[mask].mean())
        lines.append(
            f"| funding >= {thr*100:+.3f}% (extreme positive) | {n} | "
            f"{m:+.3f}% | {m - baseline:+.3f}% |")
    # Negative side
    for thr in (-0.0003, -0.0005, -0.001, -0.0015):
        mask = rates <= thr
        n = int(mask.sum())
        if n == 0:
            continue
        m = float(fwd[mask].mean())
        lines.append(
            f"| funding <= {thr*100:+.3f}% (extreme negative) | {n} | "
            f"{m:+.3f}% | {m - baseline:+.3f}% |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    ap.add_argument("--history", type=int, default=500,
                    help="Funding records per symbol (8h each; 500 = ~167d).")
    ap.add_argument("--fwd-hours", type=int, default=24)
    args = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    print(f"\n# Sweep — funding-rate gate")
    print(f"\nSymbols: {symbols}    History per symbol: {args.history} funding "
          f"records (~{args.history*8/24:.0f} days)    Forward window: "
          f"{args.fwd_hours}h\n")

    all_rates: List[float] = []
    all_fwd: List[float] = []
    for s in symbols:
        result = evaluate_symbol(s, args.history, args.fwd_hours)
        if result is None:
            print(f"  {s}: skipped (no perp / no data)", flush=True)
            continue
        rates, fwd = result
        print(f"  {s}: {len(rates)} aligned funding+price points "
              f"(funding range {rates.min()*100:+.3f}% to "
              f"{rates.max()*100:+.3f}%)", flush=True)
        all_rates.extend(rates.tolist())
        all_fwd.extend(fwd.tolist())

    if not all_rates:
        print("no data — sweep aborted")
        return 1
    rates = np.asarray(all_rates); fwd = np.asarray(all_fwd)
    thresholds_pos = [0.0001, 0.0002, 0.0003, 0.0005, 0.001]
    table = bucket_report(rates, fwd, thresholds_pos)
    print("\nAggregate across all symbols:\n")
    print(table)

    out_dir = _REPO / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"funding_sweep_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.md"
    out_path.write_text(
        "# Funding-rate gate sweep\n\n"
        f"_Generated {datetime.now(timezone.utc).isoformat()}_\n\n"
        f"Symbols: {symbols}\nHistory: {args.history} funding records each "
        f"(~{args.history*8/24:.0f} days)\nForward window: {args.fwd_hours}h\n\n"
        + table + "\n", encoding="utf-8")
    print(f"\nReport: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
