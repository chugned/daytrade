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
import sys
from dataclasses import asdict
from typing import Dict, List, Optional

import pandas as pd

from daytrade.config import AppConfig
from daytrade.indicators.frame import ohlcv_to_frame
from daytrade.features.pipeline import FeaturePipeline
from daytrade.labels.generate import triple_barrier_label
from daytrade.ml.meta import MetaLabelModel, barrier_distances
from daytrade.models import OHLCV
from daytrade.research.cascade_meta_interaction import (
    SliceMetrics,
    analyze_cascade_meta_interaction,
)
from daytrade.research.history import download_history


_DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT"
_DEFAULT_COST_BPS = 24.0  # 10bp fee × 2 + 2bp base slippage × 2


def _pull(symbol: str, days: int) -> List[OHLCV]:
    return download_history(symbol, interval="1m", days=days)


def _train_test_split(frame: pd.DataFrame, train_frac: float = 0.7) -> tuple:
    """Chronological split (no shuffle — preserves train-before-test order)."""
    cut = int(len(frame) * train_frac)
    return frame.iloc[:cut].copy(), frame.iloc[cut:].copy()


def _build_per_symbol_frame(candles: List[OHLCV], config: AppConfig) -> Optional[pd.DataFrame]:
    """Feature frame joined with triple-barrier labels + horizon return.

    Returns ``None`` when there isn't enough resolvable history.
    """
    if len(candles) < 200:
        return None
    frame = ohlcv_to_frame(candles)
    pipe = FeaturePipeline(config.features, config.indicators)
    feats = pipe.transform_frame(frame)
    stop_d, target_d = barrier_distances(frame, config)
    max_hold = max(1, config.risk.max_hold_bars)
    labels = triple_barrier_label(frame, stop_d, target_d, max_hold)

    # Forward return at the same horizon as the label (vertical barrier).
    # The triple-barrier return is bounded by stop/target; raw close-to-close
    # at the same window gives a clean apples-to-apples slice metric.
    close = frame["close"].astype(float)
    fwd_return_bps = (close.shift(-max_hold) - close) / close * 10_000.0

    joined = feats.join(labels, how="inner")
    joined["forward_return_bps"] = fwd_return_bps
    joined = joined.dropna()
    return joined


def _score_test_frame(model: MetaLabelModel, test_df: pd.DataFrame) -> Optional[pd.Series]:
    """Vectorised predict_proba over the test slice's feature rows."""
    feature_cols = model.feature_names
    if not all(c in test_df.columns for c in feature_cols):
        return None
    X_test = test_df[feature_cols].to_numpy(dtype=float)
    classes = list(model._pipeline.classes_)
    proba_all = model._pipeline.predict_proba(X_test)
    if 1 in classes:
        return pd.Series(proba_all[:, classes.index(1)], index=test_df.index)
    return pd.Series(1.0 if classes[0] == 1 else 0.0, index=test_df.index)


def _evaluate_symbol(symbol: str, candles: List[OHLCV], config: AppConfig,
                     gate_multiple: float, cost_bps: float) -> Optional[Dict]:
    """Per-symbol training: trains a fresh meta-model on this symbol's
    70% only. Stricter test than production (which pools across symbols)."""
    frame = _build_per_symbol_frame(candles, config)
    if frame is None or len(frame) < 200:
        return None

    train_df, test_df = _train_test_split(frame, train_frac=0.7)
    if len(train_df) < 100 or len(test_df) < 30:
        return None

    model = MetaLabelModel()
    train_result = model.train([candles[: int(len(candles) * 0.7)]], config)
    if train_result is None or not model.is_trained:
        return None

    proba = _score_test_frame(model, test_df)
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


def _evaluate_pooled(candles_by_symbol: Dict[str, List[OHLCV]],
                     config: AppConfig, gate_multiple: float,
                     cost_bps: float) -> List[Dict]:
    """Pooled training: trains ONE meta-model on the union of every
    symbol's first 70%, then scores each symbol's last 30% with it.
    Mirrors how the live observer trains (single pooled model)."""
    # Build per-symbol frames so we can chronologically split each one
    per_symbol: Dict[str, tuple] = {}
    pooled_train_candles: List[List[OHLCV]] = []
    for sym, candles in candles_by_symbol.items():
        frame = _build_per_symbol_frame(candles, config)
        if frame is None or len(frame) < 200:
            continue
        train_df, test_df = _train_test_split(frame, train_frac=0.7)
        if len(train_df) < 100 or len(test_df) < 30:
            continue
        per_symbol[sym] = (train_df, test_df)
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
        proba = _score_test_frame(model, test_df)
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
    args = p.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    config = AppConfig()

    print(f"# cascade × meta-gate interaction sweep", file=sys.stderr)
    print(f"# symbols={','.join(symbols)} days={args.days} "
          f"training={args.training} "
          f"gate_multiple={args.gate_multiple} cost_bps={args.cost_bps}",
          file=sys.stderr)

    # Pull all candles up front so both modes share the same input
    candles_by_symbol: Dict[str, List[OHLCV]] = {}
    for sym in symbols:
        print(f"  {sym}: pulling cached candles...", file=sys.stderr)
        try:
            candles_by_symbol[sym] = _pull(sym, args.days)
        except Exception as exc:  # noqa: BLE001
            print(f"    WARN  {sym}: {exc}", file=sys.stderr)
            continue

    if not candles_by_symbol:
        print("ERROR: no symbols produced candles", file=sys.stderr)
        return 1

    results: List[Dict] = []
    if args.training == "per_symbol":
        for sym, candles in candles_by_symbol.items():
            print(f"  {sym}: per-symbol train + score (n_bars={len(candles)})...",
                  file=sys.stderr)
            r = _evaluate_symbol(sym, candles, config,
                                 gate_multiple=args.gate_multiple,
                                 cost_bps=args.cost_bps)
            if r is None:
                print(f"    SKIP  {sym}: insufficient data or training",
                      file=sys.stderr)
                continue
            results.append(r)
    else:  # pooled
        print(f"  pooled training on {len(candles_by_symbol)} symbols...",
              file=sys.stderr)
        results = _evaluate_pooled(candles_by_symbol, config,
                                   gate_multiple=args.gate_multiple,
                                   cost_bps=args.cost_bps)

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
