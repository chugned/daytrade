#!/usr/bin/env python3
"""P5-3: cost × horizon sensitivity sweep.

P4-1 / P5-2 found that the cascade-exhaustion direction signal is real
(5/6 symbols positive gross at 30m, +10 to +22 bps) but always loses to
the 24 bp retail-tier round-trip cost. The UNION lift over the meta-gate
is +0.06 to +1.11 bps — consistent but too small to flip net-positive.

The strategic question those negative results leave open: **is there ANY
combination of (symbol, forward horizon, gate strictness, cost tier)
that gets the strategy to net-positive on held-out data?**

This sweep iterates the matrix:

  symbols     × {BTC, ETH, SOL, BNB, LINK, AVAX}     # 6 majors
  horizons    × {15, 30, 60, 120, 240} minutes         # 5 horizons
  gate_mults  × {2.0, 3.0, 4.0, 5.0}                  # 4 gate strictnesses
  costs       × {6, 14, 24} bp round-trip             # VIP / maker / retail

Training is the expensive step (per (symbol, horizon) — different labels).
Gate multiple and cost are post-hoc reslices on the trained model's
probas — cheap. Total: 6 × 5 = 30 training calls; ~30 × 5 × 3 = 450 cells
to evaluate.

Parallelized via joblib (per (symbol, horizon) is the unit of work).
Parquet cache reused from sweep_cascade_meta_interaction.

Output: ``docs/COST-HORIZON-SWEEP-FINDINGS.md`` with:
- Winners table (net >= 0, n >= 30) — answers the strategic question.
- Near-winners table (net >= -5 bp) — useful when no winners.
- Heatmap of best-slice net per (symbol × horizon) at the retail cost tier.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from joblib import Parallel, delayed

from daytrade.config import AppConfig
from daytrade.ml.meta import MetaLabelModel
from daytrade.models import OHLCV
from daytrade.research.cascade_meta_interaction import (
    SliceMetrics,
    analyze_cascade_meta_interaction,
)
from daytrade.research.cost_horizon import find_winners, recompute_net
from daytrade.research.sweep_helpers import build_per_symbol_frame, pull_candles


_DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT"
_DEFAULT_HORIZONS = "15,30,60,120,240"
_DEFAULT_GATE_MULTS = "2.0,3.0,4.0,5.0"
_DEFAULT_COST_TIERS = "6,14,24"

_RETAIL_COST_BPS = 24.0


def _config_with_horizon(base: AppConfig, horizon_minutes: int) -> AppConfig:
    """Return a copy of ``base`` with ``risk.max_hold_bars`` overridden.

    AppConfig is a pydantic model — ``model_copy`` does deep update.
    """
    # pydantic v2's deep update needs a nested dict
    return base.model_copy(update={
        "risk": base.risk.model_copy(update={"max_hold_bars": horizon_minutes}),
    })


def _build_frame(candles: List[OHLCV], config: AppConfig) -> Optional[pd.DataFrame]:
    # Thin alias retained for now; full implementation in sweep_helpers.
    return build_per_symbol_frame(candles, config)


def _evaluate_cell(symbol: str, horizon_minutes: int, days: int,
                   base_config: AppConfig,
                   gate_multiples: List[float],
                   cost_tiers: List[float]) -> List[Tuple]:
    """Train one meta-model for (symbol, horizon), then reslice across
    every (gate_mult, cost) combination. Returns the flat list of cells.
    """
    config = _config_with_horizon(base_config, horizon_minutes)
    candles = pull_candles(symbol, days)
    if len(candles) < 200:
        return []

    frame = _build_frame(candles, config)
    if frame is None or len(frame) < 300:
        return []
    cut = int(len(frame) * 0.7)
    train_df = frame.iloc[:cut]
    test_df = frame.iloc[cut:]
    if len(train_df) < 100 or len(test_df) < 30:
        return []

    # Train ONCE per (symbol, horizon)
    model = MetaLabelModel()
    candle_cut = int(len(candles) * 0.7)
    train_result = model.train([candles[:candle_cut]], config)
    if train_result is None or not model.is_trained:
        return []

    # Score test set ONCE
    feature_cols = model.feature_names
    if not all(c in test_df.columns for c in feature_cols):
        return []
    X = test_df[feature_cols].to_numpy(dtype=float)
    classes = list(model._pipeline.classes_)
    proba_vec = model._pipeline.predict_proba(X)
    if 1 in classes:
        proba = pd.Series(proba_vec[:, classes.index(1)], index=test_df.index)
    else:
        proba = pd.Series(1.0 if classes[0] == 1 else 0.0, index=test_df.index)

    cells: List[Tuple] = []
    for gate_mult in gate_multiples:
        metrics = analyze_cascade_meta_interaction(
            cascade_exhaustion=test_df["cascade_exhaustion"].astype(int),
            meta_label=test_df["meta_label"].astype(int),
            meta_proba=proba,
            forward_return_bps=test_df["forward_return_bps"].astype(float),
            base_win_rate=train_result.base_win_rate,
            gate_multiple=gate_mult,
            round_trip_cost_bps=_RETAIL_COST_BPS,  # placeholder; rewritten below
        )
        for slice_name, m in metrics.items():
            for cost in cost_tiers:
                repriced = recompute_net(m, round_trip_cost_bps=cost)
                cells.append((symbol, horizon_minutes, gate_mult, cost,
                              slice_name, repriced))
    return cells


def _fmt_bps(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+7.2f}"


def _fmt_pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:5.1f}%"


def _winners_table(cells: List[Tuple], min_n: int = 30) -> str:
    winners = find_winners(cells, min_n=min_n, min_net=0.0)
    if not winners:
        return "_No winners — no cell cleared net >= 0 with n >= " + str(min_n) + "._\n"
    out = ["| Symbol | Horizon | Slice | Gate × | Cost (bp) | n | Gross | Net |",
           "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |"]
    for w in winners:
        out.append(
            f"| {w.symbol} | {w.horizon_minutes}m | `{w.slice}` | "
            f"{w.gate_multiple:.1f} | {w.cost_bps:.0f} | {w.n} | "
            f"{w.gross_bps:+.2f} | **{w.net_bps:+.2f}** |"
        )
    return "\n".join(out) + "\n"


def _near_winners_table(cells: List[Tuple], min_n: int = 30,
                        gap_bps: float = 5.0) -> str:
    """Cells within ``gap_bps`` of break-even — useful when no real winners."""
    near = find_winners(cells, min_n=min_n, min_net=-gap_bps)
    actual_wins = find_winners(cells, min_n=min_n, min_net=0.0)
    # Subtract actual winners; we already report those
    actual_set = {(w.symbol, w.horizon_minutes, w.gate_multiple, w.cost_bps,
                   w.slice) for w in actual_wins}
    only_near = [w for w in near
                 if (w.symbol, w.horizon_minutes, w.gate_multiple,
                     w.cost_bps, w.slice) not in actual_set][:20]
    if not only_near:
        return "_None within {} bp of break-even._\n".format(gap_bps)
    out = [f"_Top 20 cells within {gap_bps} bp of break-even (sorted by net):_\n",
           "| Symbol | Horizon | Slice | Gate × | Cost (bp) | n | Gross | Net |",
           "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |"]
    for w in only_near:
        out.append(
            f"| {w.symbol} | {w.horizon_minutes}m | `{w.slice}` | "
            f"{w.gate_multiple:.1f} | {w.cost_bps:.0f} | {w.n} | "
            f"{w.gross_bps:+.2f} | {w.net_bps:+.2f} |"
        )
    return "\n".join(out) + "\n"


def _heatmap_at_cost(cells: List[Tuple], symbols: List[str],
                     horizons: List[int], gate_mult: float, cost: float,
                     slice_name: str) -> str:
    """Best (symbol × horizon) heatmap for one (slice, gate_mult, cost)."""
    # Index by (sym, horizon)
    grid: Dict[Tuple[str, int], SliceMetrics] = {}
    for sym, hz, gm, c, sl, m in cells:
        if gm == gate_mult and c == cost and sl == slice_name:
            grid[(sym, hz)] = m

    out = [f"_slice=`{slice_name}`, gate=×{gate_mult:.1f}, cost={cost:.0f} bp, "
           f"cell = mean_return_net_bps (n in parentheses):_\n",
           "| Symbol | " + " | ".join(f"{h}m" for h in horizons) + " |",
           "| --- | " + " | ".join("---:" for _ in horizons) + " |"]
    for sym in symbols:
        row = [sym]
        for hz in horizons:
            m = grid.get((sym, hz))
            if m is None or m.mean_return_net_bps is None or m.n == 0:
                row.append("—")
            else:
                row.append(f"{m.mean_return_net_bps:+.1f} ({m.n})")
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", default=_DEFAULT_SYMBOLS)
    p.add_argument("--horizons", default=_DEFAULT_HORIZONS,
                   help="Comma-separated horizons in minutes")
    p.add_argument("--gate-multiples", default=_DEFAULT_GATE_MULTS)
    p.add_argument("--cost-tiers", default=_DEFAULT_COST_TIERS,
                   help="Comma-separated round-trip costs in bps")
    p.add_argument("--days", type=int, default=30,
                   help="Days of cached 1m history per symbol")
    p.add_argument("--jobs", type=int, default=-1,
                   help="Parallel workers (joblib). -1 = all cores")
    p.add_argument("--out", default="docs/COST-HORIZON-SWEEP-FINDINGS.md")
    args = p.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    gate_mults = [float(g) for g in args.gate_multiples.split(",") if g.strip()]
    cost_tiers = [float(c) for c in args.cost_tiers.split(",") if c.strip()]
    base_config = AppConfig()

    print(f"# cost × horizon sweep", file=sys.stderr)
    print(f"# symbols={len(symbols)} horizons={horizons} "
          f"gate_mults={gate_mults} cost_tiers={cost_tiers}",
          file=sys.stderr)
    print(f"# matrix: {len(symbols)*len(horizons)} training calls "
          f"× {len(gate_mults)*len(cost_tiers)*5} cells per call",
          file=sys.stderr)

    t0 = time.monotonic()
    # Each (symbol, horizon) is one training call — parallelize.
    jobs = [(sym, hz) for sym in symbols for hz in horizons]
    nested_cells = Parallel(n_jobs=args.jobs, prefer="processes",
                            backend="loky", verbose=5)(
        delayed(_evaluate_cell)(sym, hz, args.days, base_config,
                                gate_mults, cost_tiers)
        for sym, hz in jobs
    )
    elapsed = time.monotonic() - t0
    cells = [c for batch in nested_cells for c in batch]
    print(f"  trained {len(jobs)} cells in {elapsed:.1f}s "
          f"({len(cells)} sliced results)", file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        fh.write("# Cost × Horizon Sensitivity Sweep (P5-3)\n\n")
        fh.write(f"- Symbols: {', '.join(symbols)}\n")
        fh.write(f"- Horizons: {', '.join(str(h) + 'm' for h in horizons)}\n")
        fh.write(f"- Gate multiples: {', '.join(str(g) for g in gate_mults)}\n")
        fh.write(f"- Cost tiers: {', '.join(str(int(c)) + ' bp' for c in cost_tiers)} "
                 f"(retail / maker / VIP)\n")
        fh.write(f"- History: last {args.days} days × 1m candles per symbol\n")
        fh.write(f"- Split: chronological 70% train / 30% test\n")
        fh.write(f"- Matrix size: {len(cells)} cells "
                 f"({len(jobs)} training × {len(gate_mults)*len(cost_tiers)*5} reslices)\n")
        fh.write(f"- Eval time: {elapsed:.1f}s "
                 f"(parallel jobs={args.jobs})\n\n")

        fh.write("## Winners (net >= 0, n >= 30 events)\n\n")
        fh.write("This is the strategic question: *any cell here means a net-positive ")
        fh.write("trade strategy exists at that (symbol, horizon, gate, cost).*\n\n")
        fh.write(_winners_table(cells, min_n=30))
        fh.write("\n")

        fh.write("## Near-winners (net within 5 bp of break-even)\n\n")
        fh.write(_near_winners_table(cells, min_n=30, gap_bps=5.0))
        fh.write("\n")

        # Heatmap at the realistic retail cost, for the candidate UNION slice
        fh.write("## Heatmap — UNION slice at retail (24 bp) cost\n\n")
        fh.write(_heatmap_at_cost(cells, symbols, horizons,
                                  gate_mult=2.0, cost=24.0,
                                  slice_name="cascade_or_gated"))
        fh.write("\n")
        fh.write("## Heatmap — UNION slice at VIP (6 bp) cost\n\n")
        fh.write(_heatmap_at_cost(cells, symbols, horizons,
                                  gate_mult=2.0, cost=6.0,
                                  slice_name="cascade_or_gated"))
        fh.write("\n")
        fh.write("## Heatmap — meta_gated at retail (24 bp) cost\n\n")
        fh.write(_heatmap_at_cost(cells, symbols, horizons,
                                  gate_mult=2.0, cost=24.0,
                                  slice_name="meta_gated"))
        fh.write("\n")
        fh.write("## Heatmap — cascade_exhaustion alone at retail (24 bp) cost\n\n")
        fh.write(_heatmap_at_cost(cells, symbols, horizons,
                                  gate_mult=2.0, cost=24.0,
                                  slice_name="cascade_exhaustion"))
        fh.write("\n")

    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
