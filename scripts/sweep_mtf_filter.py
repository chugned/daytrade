#!/usr/bin/env python3
"""Sweep the multi-timeframe alignment filter on real history.

Measures whether requiring higher-TF (15m + 1h) trend alignment with the
1m primary signal actually improves win rate out-of-sample. Same
discipline as the stop-multiplier and meta-gate-multiple sweeps:
empirically prove the threshold before flipping it on.

For each historical 1m bar in the test window we ask "if the primary
issued a BUY here, would the HTF filter pass it?" and then measure the
forward N-minute return AMONG those bars vs ALL bars. A useful filter
selects bars with systematically better forward returns.

Usage::

    PYTHONPATH=src python3 scripts/sweep_mtf_filter.py \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT --days 30

Outputs a markdown table to ``reports/mtf_sweep_<timestamp>.md`` and to
stdout. Read-only — does not touch the running bot.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from daytrade.observatory.multi_timeframe import (  # noqa: E402
    check_higher_tf_alignment,
)
from daytrade.research.history import download_history  # noqa: E402


@dataclass
class SweepRow:
    threshold_min_slope: float
    fire_rate: float
    fwd_return_when_aligned: float
    fwd_return_when_misaligned: float
    fwd_return_baseline: float

    @property
    def lift_vs_baseline(self) -> float:
        return self.fwd_return_when_aligned - self.fwd_return_baseline


def evaluate_symbol(symbol: str, days: int, fwd_min: int,
                    thresholds: List[float]) -> List[SweepRow]:
    """For one symbol, score every bar through the filter at each threshold."""
    candles = download_history(symbol, interval="1m", days=days)
    if len(candles) < 300:
        return []
    print(f"  {symbol}: {len(candles)} 1m candles", flush=True)

    closes = pd.Series([c.close for c in candles])
    fwd_ret = (closes.shift(-fwd_min) / closes - 1.0) * 100

    rows: List[SweepRow] = []
    # We slide a 240-bar window across the series and ask the filter
    # "is this BUY aligned with 15m+1h?" — this matches what the live
    # bot sees each cycle.
    window = 240
    for thr in thresholds:
        aligned_returns: List[float] = []
        misaligned_returns: List[float] = []
        for i in range(window, len(candles) - fwd_min, 30):  # sample every 30 min
            window_candles = candles[i - window: i]
            result = check_higher_tf_alignment(window_candles, "buy",
                                               min_slope=thr)
            f = fwd_ret.iloc[i]
            if not np.isfinite(f):
                continue
            (aligned_returns if result.aligned else misaligned_returns).append(f)
        total = len(aligned_returns) + len(misaligned_returns)
        if total == 0:
            continue
        rows.append(SweepRow(
            threshold_min_slope=thr,
            fire_rate=len(aligned_returns) / total,
            fwd_return_when_aligned=(np.mean(aligned_returns)
                                     if aligned_returns else 0.0),
            fwd_return_when_misaligned=(np.mean(misaligned_returns)
                                        if misaligned_returns else 0.0),
            fwd_return_baseline=np.mean(aligned_returns + misaligned_returns),
        ))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--fwd-min", type=int, default=60,
                    help="Forward window (minutes) to measure outcome.")
    args = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    thresholds = [0.0, 0.00005, 0.0001, 0.0002, 0.0005]

    print(f"\n# Sweep — multi-timeframe alignment filter")
    print(f"\nSymbols: {symbols}    Days: {args.days}    Fwd window: "
          f"{args.fwd_min}m\n")
    rows_by_sym = {}
    for s in symbols:
        rows = evaluate_symbol(s, args.days, args.fwd_min, thresholds)
        rows_by_sym[s] = rows

    # Aggregate.
    print("\nAggregate across all symbols:\n")
    lines = ["| min_slope | fires (%) | fwd@aligned | fwd@misaligned | "
             "baseline | lift |",
             "|---:|---:|---:|---:|---:|---:|"]
    for i, thr in enumerate(thresholds):
        rs = [rows_by_sym[s][i] for s in symbols
              if rows_by_sym.get(s) and len(rows_by_sym[s]) > i]
        if not rs:
            continue
        fire = float(np.mean([r.fire_rate for r in rs])) * 100
        a = float(np.mean([r.fwd_return_when_aligned for r in rs]))
        m = float(np.mean([r.fwd_return_when_misaligned for r in rs]))
        base = float(np.mean([r.fwd_return_baseline for r in rs]))
        lift = a - base
        lines.append(f"| {thr:.5f} | {fire:.1f} | {a:+.4f}% | {m:+.4f}% | "
                     f"{base:+.4f}% | {lift:+.4f}% |")

    table = "\n".join(lines)
    print(table)

    out_dir = _REPO / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"mtf_sweep_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.md"
    out_path.write_text(
        "# MTF filter sweep\n\n"
        f"_Generated {datetime.now(timezone.utc).isoformat()}_\n\n"
        f"Symbols: {symbols}\nDays: {args.days}\nFwd window: {args.fwd_min}m\n\n"
        + table + "\n", encoding="utf-8")
    print(f"\nReport: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
