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


def _evaluate_symbol(symbol: str, candles: List[OHLCV], config: AppConfig,
                     gate_multiple: float, cost_bps: float) -> Optional[Dict]:
    frame = _build_per_symbol_frame(candles, config)
    if frame is None or len(frame) < 200:
        return None

    train_df, test_df = _train_test_split(frame, train_frac=0.7)
    if len(train_df) < 100 or len(test_df) < 30:
        return None

    # Train a fresh meta-model on the train slice (per-symbol scope).
    model = MetaLabelModel()
    train_result = model.train([candles[: int(len(candles) * 0.7)]], config)
    if train_result is None or not model.is_trained:
        return None

    feature_cols = model.feature_names
    if not all(c in test_df.columns for c in feature_cols):
        return None

    # Score the held-out slice in one batch (predict_proba vectorised).
    X_test = test_df[feature_cols].to_numpy(dtype=float)
    classes = list(model._pipeline.classes_)
    proba_all = model._pipeline.predict_proba(X_test)
    if 1 in classes:
        proba = pd.Series(proba_all[:, classes.index(1)], index=test_df.index)
    else:
        proba = pd.Series(1.0 if classes[0] == 1 else 0.0, index=test_df.index)

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
    fh.write("| Slice | n | win rate | mean ret (bps) | net of 24bp cost | sharpe-like |\n")
    fh.write("| --- | ---: | ---: | ---: | ---: | ---: |\n")
    for slice_name, m in result["metrics"].items():
        fh.write(
            f"| `{slice_name}` | {m.n} | {_fmt_pct(m.win_rate)} | "
            f"{_fmt_bps(m.mean_return_bps)} | "
            f"{_fmt_bps(m.mean_return_net_bps)} | "
            f"{('—' if m.sharpe_like is None else f'{m.sharpe_like:+5.2f}')} |\n"
        )


def _aggregate_verdict(results: List[Dict]) -> str:
    """One-paragraph verdict comparing meta_gated vs cascade_and_gated."""
    n_clear_all = sum(
        1 for r in results
        if (r["metrics"]["meta_gated"].mean_return_net_bps or 0) > 0
    )
    n_clear_cag = sum(
        1 for r in results
        if (r["metrics"]["cascade_and_gated"].mean_return_net_bps or 0) > 0
    )
    n = len(results)
    lift_count = 0
    for r in results:
        g = r["metrics"]["meta_gated"]
        cg = r["metrics"]["cascade_and_gated"]
        if (g.mean_return_net_bps is not None
                and cg.mean_return_net_bps is not None
                and cg.n >= 5
                and cg.mean_return_net_bps > g.mean_return_net_bps):
            lift_count += 1
    return (
        f"\n## Aggregate verdict\n\n"
        f"- {n_clear_all}/{n} symbols clear net cost with the **meta-gate alone**.\n"
        f"- {n_clear_cag}/{n} symbols clear net cost with **cascade ∩ gated**.\n"
        f"- {lift_count}/{n} symbols show **cascade-and-gated > meta-gated** (positive interaction)"
        f" on ≥5 events.\n\n"
        f"If cascade-and-gated dominates more often than not — *and* the per-symbol "
        f"event counts are non-trivial — the recommendation is to either (a) raise "
        f"``meta_label_edge_multiple`` selectively when ``cascade_exhaustion=1`` "
        f"(a bonus gate), or (b) admit cascade-exhaustion bars even when the meta "
        f"gate would normally block, *only* in symbols where the lift is consistent.\n\n"
        f"If cascade-and-gated does NOT consistently dominate, the meta-model is "
        f"likely already extracting whatever signal cascade exposure provides — no "
        f"additional gate logic warranted.\n"
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
    args = p.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    config = AppConfig()

    print(f"# cascade × meta-gate interaction sweep", file=sys.stderr)
    print(f"# symbols={','.join(symbols)} days={args.days} "
          f"gate_multiple={args.gate_multiple} cost_bps={args.cost_bps}",
          file=sys.stderr)

    results: List[Dict] = []
    for sym in symbols:
        print(f"  {sym}: pulling cached candles...", file=sys.stderr)
        try:
            candles = _pull(sym, args.days)
        except Exception as exc:  # noqa: BLE001
            print(f"    WARN  {sym}: {exc}", file=sys.stderr)
            continue
        print(f"  {sym}: training + scoring (n_bars={len(candles)})...",
              file=sys.stderr)
        r = _evaluate_symbol(sym, candles, config,
                             gate_multiple=args.gate_multiple,
                             cost_bps=args.cost_bps)
        if r is None:
            print(f"    SKIP  {sym}: insufficient data or training", file=sys.stderr)
            continue
        results.append(r)

    if not results:
        print("ERROR: no symbols produced results", file=sys.stderr)
        return 1

    from pathlib import Path
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        fh.write("# CASCADE × meta-gate interaction findings (P4-1)\n\n")
        fh.write(f"- Symbols: {', '.join(r['symbol'] for r in results)}\n")
        fh.write(f"- History: last {args.days} days × 1m candles per symbol\n")
        fh.write(f"- Split: chronological 70% train / 30% test\n")
        fh.write(f"- Gate threshold: ``proba > base_win_rate × {args.gate_multiple}``\n")
        fh.write(f"- Round-trip cost: {args.cost_bps:.1f} bps\n")
        fh.write(f"- Generated by: ``scripts/sweep_cascade_meta_interaction.py``\n")
        for r in results:
            _print_symbol_table(r, fh)
        fh.write(_aggregate_verdict(results))
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
