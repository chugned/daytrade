#!/usr/bin/env python3
"""P4-1: does adding CASCADE_EXHAUSTION lift meta-gate precision past cost?

The cascade columns are already part of the meta-model's feature set
(``features/pipeline.py``). What this sweep measures is whether the
trained meta-model actually picks the signal out — specifically, on
held-out data, does the subset of bars that are BOTH gated AND
cascade-exhaustion-positive have higher precision and (net of 24 bp
round-trip cost) positive expected return?

Process per symbol:
1. Pull 30d of 1m candles from the existing cache.
2. Build feature frame + triple-barrier labels.
3. Drop NaN, split chronologically: first 70% train, last 30% test.
4. Train a per-symbol meta-model on the train slice.
5. Score the held-out test slice (proba + forward return).
6. Run ``analyze_cascade_meta_interaction`` to produce slice metrics.

The sweep writes one Markdown table per symbol + an aggregate
verdict to ``docs/CASCADE-META-INTERACTION-FINDINGS.md``. Read-only.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from joblib import Parallel, delayed

from daytrade.config import AppConfig
from daytrade.ml.meta import MetaLabelModel
from daytrade.models import OHLCV
from daytrade.research.cascade_meta_interaction import (
    SliceMetrics,
    analyze_cascade_meta_interaction,
)
from daytrade.research.sweep_helpers import (
    load_or_build_frame,
    pull_candles,
    score_test_frame,
    train_test_split,
)


_DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT"
_DEFAULT_COST_BPS = 24.0  # 10bp fee × 2 + 2bp base slippage × 2


# Frame build / cache / split / score helpers all moved to
# daytrade.research.sweep_helpers so every sweep script shares the
# same implementation. See that module for full docs.


def _evaluate_symbol(symbol: str, days: int, config: AppConfig,
                     gate_multiple: float, cost_bps: float,
                     use_cache: bool = True) -> Optional[Dict]:
    """Per-symbol training: pulls candles (cached), builds frame (cached),
    trains a fresh meta-model on first 70%, scores last 30%.

    This entry-point is picklable so joblib can fan out across CPUs.
    """
    frame = load_or_build_frame(symbol, days, config, use_cache=use_cache)
    if frame is None or len(frame) < 200:
        return None
    train_df, test_df = train_test_split(frame, train_frac=0.7)
    if len(train_df) < 100 or len(test_df) < 30:
        return None

    # MetaLabelModel.train takes candles, not a frame — re-pull (cached at
    # the SQLite layer, ~instant for the 2nd call) and slice to the train
    # window proportionally.
    candles = pull_candles(symbol, days)
    cut = int(len(candles) * 0.7)
    model = MetaLabelModel()
    train_result = model.train([candles[:cut]], config)
    if train_result is None or not model.is_trained:
        return None

    proba = score_test_frame(model, test_df)
    if proba is None:
        return None

    metrics = analyze_cascade_meta_interaction(
        cascade_exhaustion=test_df["cascade_exhaustion"].astype(int),
        meta_label=test_df["meta_label"].astype(int),
        meta_proba=proba,
        forward_return_bps=test_df["forward_return_bps"].astype(float),
        base_win_rate=train_result.base_win_rate,
        gate_multiple=gate_multiple,
        round_trip_cost_bps=cost_bps,
    )
    return {
        "symbol": symbol,
        "n_train": train_result.samples,
        "base_win_rate": train_result.base_win_rate,
        "n_test": len(test_df),
        "metrics": metrics,
    }


def _evaluate_pooled(symbols: List[str], days: int,
                     config: AppConfig, gate_multiple: float,
                     cost_bps: float, use_cache: bool = True) -> List[Dict]:
    """Pooled training: trains ONE meta-model on the union of every
    symbol's first 70%, then scores each symbol's last 30% with it.
    Mirrors how the live observer trains (single pooled model)."""
    # Build per-symbol frames so we can chronologically split each one.
    # Use the cache so the SECOND time you run pooled with the same
    # (symbols, days) the frames load from parquet (~1s) instead of
    # being rebuilt (~20s × N symbols).
    per_symbol: Dict[str, tuple] = {}
    pooled_train_candles: List[List[OHLCV]] = []
    for sym in symbols:
        frame = load_or_build_frame(sym, days, config, use_cache=use_cache)
        if frame is None or len(frame) < 200:
            continue
        train_df, test_df = train_test_split(frame, train_frac=0.7)
        if len(train_df) < 100 or len(test_df) < 30:
            continue
        per_symbol[sym] = (train_df, test_df)
        candles = pull_candles(sym, days)
        cut = int(len(candles) * 0.7)
        pooled_train_candles.append(candles[:cut])

    if not per_symbol:
        return []

    # One pooled model
    model = MetaLabelModel()
    train_result = model.train(pooled_train_candles, config)
    if train_result is None or not model.is_trained:
        return []

    results: List[Dict] = []
    for sym, (_train_df, test_df) in per_symbol.items():
        proba = score_test_frame(model, test_df)
        if proba is None:
            continue
        metrics = analyze_cascade_meta_interaction(
            cascade_exhaustion=test_df["cascade_exhaustion"].astype(int),
            meta_label=test_df["meta_label"].astype(int),
            meta_proba=proba,
            forward_return_bps=test_df["forward_return_bps"].astype(float),
            base_win_rate=train_result.base_win_rate,
            gate_multiple=gate_multiple,
            round_trip_cost_bps=cost_bps,
        )
        results.append({
            "symbol": sym,
            "n_train": train_result.samples,  # pooled total
            "base_win_rate": train_result.base_win_rate,
            "n_test": len(test_df),
            "metrics": metrics,
        })
    return results


def _fmt_pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:5.1f}%"


def _fmt_bps(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+7.2f}"


def _print_symbol_table(result: Dict, fh) -> None:
    sym = result["symbol"]
    base = result["base_win_rate"]
    n_train = result["n_train"]
    n_test = result["n_test"]
    fh.write(f"\n## {sym}\n\n")
    fh.write(f"- training: n={n_train}, base_win_rate={base * 100:.1f}%\n")
    fh.write(f"- test:     n={n_test} (chronological 70/30 split)\n\n")
    fh.write("| Slice | n | win rate | mean ret (bps) | net of cost | sharpe-like |\n")
    fh.write("| --- | ---: | ---: | ---: | ---: | ---: |\n")
    for slice_name, m in result["metrics"].items():
        fh.write(
            f"| `{slice_name}` | {m.n} | {_fmt_pct(m.win_rate)} | "
            f"{_fmt_bps(m.mean_return_bps)} | "
            f"{_fmt_bps(m.mean_return_net_bps)} | "
            f"{('—' if m.sharpe_like is None else f'{m.sharpe_like:+5.2f}')} |\n"
        )


def _aggregate_verdict(results: List[Dict], cost_bps: float) -> str:
    """Compact verdict comparing meta_gated vs cascade_or_gated (UNION)
    and vs cascade_and_gated (intersection)."""
    n = len(results)

    def _net_pos(slice_name):
        return sum(
            1 for r in results
            if (r["metrics"][slice_name].mean_return_net_bps or 0) > 0
        )

    def _union_lift(min_events: int = 5):
        """Symbols where union beats meta-gate alone on >= min_events."""
        out = 0
        for r in results:
            g = r["metrics"]["meta_gated"]
            u = r["metrics"]["cascade_or_gated"]
            if (g.mean_return_net_bps is not None
                    and u.mean_return_net_bps is not None
                    and u.n >= min_events
                    and u.mean_return_net_bps > g.mean_return_net_bps):
                out += 1
        return out

    return (
        f"\n## Aggregate verdict\n\n"
        f"_Cost threshold: {cost_bps:.1f} bps round-trip_\n\n"
        f"| Slice | symbols clearing net cost |\n"
        f"| --- | ---: |\n"
        f"| `all` (no filter) | {_net_pos('all')}/{n} |\n"
        f"| `cascade_exhaustion` alone | {_net_pos('cascade_exhaustion')}/{n} |\n"
        f"| `meta_gated` (current live rule) | {_net_pos('meta_gated')}/{n} |\n"
        f"| `cascade_and_gated` (intersection) | {_net_pos('cascade_and_gated')}/{n} |\n"
        f"| `cascade_or_gated` (UNION — candidate live rule) | {_net_pos('cascade_or_gated')}/{n} |\n\n"
        f"- {_union_lift()}/{n} symbols show **cascade-OR-gated > meta-gated** "
        f"on ≥5 events (the P5-2 question).\n"
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", default=_DEFAULT_SYMBOLS,
                   help="Comma-separated symbol list")
    p.add_argument("--days", type=int, default=30,
                   help="Days of cached 1m history per symbol")
    p.add_argument("--gate-multiple", type=float, default=2.0,
                   help="meta_label_edge_multiple (default 2.0, matches live config)")
    p.add_argument("--cost-bps", type=float, default=_DEFAULT_COST_BPS,
                   help="Round-trip cost in bps for the 'net' column")
    p.add_argument("--out", default="docs/CASCADE-META-INTERACTION-FINDINGS.md",
                   help="Markdown output path")
    p.add_argument("--training", choices=("per_symbol", "pooled"),
                   default="per_symbol",
                   help="per_symbol = per-symbol meta-model (default); "
                        "pooled = one model on the union of training slices "
                        "(matches live observer)")
    p.add_argument("--jobs", type=int, default=-1,
                   help="Parallel workers for per_symbol mode (joblib). "
                        "-1 = all cores. Pooled mode is single-process by "
                        "construction (one training call).")
    p.add_argument("--no-cache", action="store_true",
                   help="Bypass the parquet feature-frame cache (rebuilds "
                        "every frame from candles). The cache is at "
                        "artifacts/cache/cascade_meta_frames/.")
    args = p.parse_args()
    use_cache = not args.no_cache

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    config = AppConfig()

    print(f"# cascade × meta-gate interaction sweep", file=sys.stderr)
    print(f"# symbols={','.join(symbols)} days={args.days} "
          f"training={args.training} "
          f"gate_multiple={args.gate_multiple} cost_bps={args.cost_bps}",
          file=sys.stderr)

    import time
    t0 = time.monotonic()

    results: List[Dict] = []
    if args.training == "per_symbol":
        print(f"  per_symbol training, jobs={args.jobs} "
              f"(cache: {'on' if use_cache else 'off'})...", file=sys.stderr)
        # joblib fans out across cores. Each worker pulls candles
        # (cached at the SQLite layer), loads/builds the frame
        # (cached at the parquet layer), trains a fresh model, scores.
        # 6 symbols × 90d × 1m on 6 cores → ~6x speedup vs sequential.
        parallel_results = Parallel(n_jobs=args.jobs, prefer="processes",
                                    backend="loky", verbose=5)(
            delayed(_evaluate_symbol)(
                sym, args.days, config, args.gate_multiple, args.cost_bps,
                use_cache,
            ) for sym in symbols
        )
        for sym, r in zip(symbols, parallel_results):
            if r is None:
                print(f"    SKIP  {sym}: insufficient data or training",
                      file=sys.stderr)
                continue
            results.append(r)
    else:  # pooled
        print(f"  pooled training on {len(symbols)} symbols "
              f"(cache: {'on' if use_cache else 'off'})...", file=sys.stderr)
        results = _evaluate_pooled(symbols, args.days, config,
                                   gate_multiple=args.gate_multiple,
                                   cost_bps=args.cost_bps,
                                   use_cache=use_cache)

    elapsed = time.monotonic() - t0
    print(f"  evaluation took {elapsed:.1f}s", file=sys.stderr)

    if not results:
        print("ERROR: no symbols produced results", file=sys.stderr)
        return 1

    from pathlib import Path
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        fh.write("# CASCADE × meta-gate interaction findings\n\n")
        fh.write(f"- Symbols: {', '.join(r['symbol'] for r in results)}\n")
        fh.write(f"- History: last {args.days} days × 1m candles per symbol\n")
        fh.write(f"- Split: chronological 70% train / 30% test\n")
        fh.write(f"- Training mode: **{args.training}**"
                 + (" (matches live observer)" if args.training == "pooled" else "")
                 + "\n")
        fh.write(f"- Gate threshold: ``proba > base_win_rate × {args.gate_multiple}``\n")
        fh.write(f"- Round-trip cost: {args.cost_bps:.1f} bps\n")
        fh.write(f"- Generated by: ``scripts/sweep_cascade_meta_interaction.py``\n")
        for r in results:
            _print_symbol_table(r, fh)
        fh.write(_aggregate_verdict(results, args.cost_bps))
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
