#!/usr/bin/env python3
"""P5-4: validate the P5-3 winners with pooled training on 90 days.

P5-3 found that BNB at 240m with gate=4.0 nets +30.87 bp at retail
cost on n=220 events — but it was per-symbol training on a 27-day
held-out window. The two open caveats were:

- Production trains ONE pooled meta-model across all symbols.
- The 27-day held-out window is too short to bind regime variance.

P5-4 re-runs the winning cells with:
- Pooled training (matches production)
- 90-day history (longest the cache covers)
- Focused matrix: BNB + SOL × {120m, 240m} × gate {3.0, 4.0, 5.0}
- Compares against per-symbol baseline so the lift/drop from
  pooling is visible directly.

Verdict at top: GO if BNB-240m-gate-4 (or stronger) still clears
retail cost on pooled+90d. NO-GO otherwise.

Read-only. No live state touched.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from daytrade.config import AppConfig
from daytrade.ml.meta import MetaLabelModel
from daytrade.models import OHLCV
from daytrade.research.cascade_meta_interaction import (
    analyze_cascade_meta_interaction,
)
from daytrade.research.cost_horizon import recompute_net
from daytrade.research.history import download_history

# Import the cached-frame builder from the existing sweep
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sweep_cascade_meta_interaction import (  # noqa: E402
    _build_per_symbol_frame, _load_or_build_frame, _score_test_frame,
    _train_test_split,
)


def _config_with_horizon(base: AppConfig, horizon_minutes: int) -> AppConfig:
    return base.model_copy(update={
        "risk": base.risk.model_copy(update={"max_hold_bars": horizon_minutes}),
    })


def _evaluate_pooled_at_horizon(symbols: List[str], horizon: int, days: int,
                                base_config: AppConfig,
                                gate_multiples: List[float],
                                cost_bps: float) -> Dict:
    """Train ONE pooled meta-model at the given horizon; score each
    symbol's held-out 30% with it; report slice metrics per
    (symbol, gate_multiple)."""
    config = _config_with_horizon(base_config, horizon)

    per_symbol: Dict[str, tuple] = {}
    pooled_train_candles: List[List[OHLCV]] = []
    for sym in symbols:
        frame = _load_or_build_frame(sym, days, config, use_cache=True)
        if frame is None or len(frame) < 200:
            print(f"    SKIP {sym}: insufficient frame", file=sys.stderr)
            continue
        train_df, test_df = _train_test_split(frame, train_frac=0.7)
        if len(train_df) < 100 or len(test_df) < 30:
            print(f"    SKIP {sym}: split too small", file=sys.stderr)
            continue
        per_symbol[sym] = (train_df, test_df)
        candles = download_history(sym, interval="1m", days=days)
        cut = int(len(candles) * 0.7)
        pooled_train_candles.append(candles[:cut])

    if not per_symbol:
        return {"horizon": horizon, "results": []}

    print(f"  horizon={horizon}m: pooling {len(per_symbol)} symbols "
          f"({sum(len(c) for c in pooled_train_candles)} candles)...",
          file=sys.stderr)
    t0 = time.monotonic()
    model = MetaLabelModel()
    train_result = model.train(pooled_train_candles, config)
    if train_result is None or not model.is_trained:
        return {"horizon": horizon, "results": []}
    print(f"    trained pooled model in {time.monotonic()-t0:.1f}s, "
          f"base_win_rate={train_result.base_win_rate:.3f}, "
          f"n_train={train_result.samples}", file=sys.stderr)

    results = []
    for sym, (_, test_df) in per_symbol.items():
        proba = _score_test_frame(model, test_df)
        if proba is None:
            continue
        for gate_mult in gate_multiples:
            metrics = analyze_cascade_meta_interaction(
                cascade_exhaustion=test_df["cascade_exhaustion"].astype(int),
                meta_label=test_df["meta_label"].astype(int),
                meta_proba=proba,
                forward_return_bps=test_df["forward_return_bps"].astype(float),
                base_win_rate=train_result.base_win_rate,
                gate_multiple=gate_mult,
                round_trip_cost_bps=cost_bps,
            )
            for slice_name in ("meta_gated", "cascade_or_gated"):
                m = metrics[slice_name]
                results.append({
                    "symbol": sym,
                    "horizon": horizon,
                    "gate_multiple": gate_mult,
                    "slice": slice_name,
                    "n": m.n,
                    "gross_bps": m.mean_return_bps,
                    "net_bps": m.mean_return_net_bps,
                    "win_rate": m.win_rate,
                })
    return {
        "horizon": horizon,
        "pooled_n_train": train_result.samples,
        "base_win_rate": train_result.base_win_rate,
        "results": results,
    }


def _verdict(all_results: List[Dict], cost_bps: float) -> str:
    """Build the top-of-doc GO/NO-GO verdict."""
    # Flatten + filter to retail-cost winners with n >= 30 on BNB or SOL
    target_winners = []
    for batch in all_results:
        for r in batch["results"]:
            if r["symbol"] in ("BNBUSDT", "SOLUSDT"):
                if (r["net_bps"] is not None and r["net_bps"] >= 0
                        and r["n"] >= 30):
                    target_winners.append(r)
    target_winners.sort(key=lambda r: r["net_bps"], reverse=True)

    # Compare against P5-3 headline (per_symbol per-symbol training)
    p5_3_headline = {
        ("SOLUSDT", 240, 3.0): 58.17,
        ("BNBUSDT", 240, 5.0): 39.49,
        ("BNBUSDT", 240, 4.0): 30.87,
        ("BNBUSDT", 240, 3.0): 9.47,
        ("BNBUSDT", 120, 3.0): 1.14,
    }

    lines = [
        "## TL;DR — pooled + 90d validation outcome\n",
    ]
    if not target_winners:
        lines.append("**NO-GO.** No BNB / SOL cell clears retail cost at "
                     "n>=30 events under pooled training on 90 days. The "
                     "P5-3 per-symbol headline did not survive validation.\n")
    else:
        lines.append(f"**{len(target_winners)} BNB / SOL cell(s)** clear "
                     f"retail (24 bp) cost with n>=30 events.\n")
        lines.append("\n| Symbol | Horizon | Slice | Gate × | n | Gross | Net | P5-3 same cell |")
        lines.append("| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |")
        for r in target_winners[:10]:
            prior = p5_3_headline.get(
                (r["symbol"], r["horizon"], r["gate_multiple"])
            )
            prior_str = f"+{prior:.2f}" if prior is not None else "—"
            lines.append(
                f"| {r['symbol']} | {r['horizon']}m | `{r['slice']}` | "
                f"{r['gate_multiple']:.1f} | {r['n']} | "
                f"{r['gross_bps']:+.2f} | **{r['net_bps']:+.2f}** | {prior_str} |"
            )

        # Recommendation
        best = target_winners[0]
        lines.append(
            f"\n**Recommendation:** if this survives, the winning config to "
            f"wire is `max_hold_bars={best['horizon']}, "
            f"meta_label_edge_multiple={best['gate_multiple']:.1f}, "
            f"watchlist=[{best['symbol']}]` for primary, with the next "
            f"few cells as secondary candidates.\n"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT",
                   help="ALL 6 symbols pool for training (test is BNB/SOL focus)")
    p.add_argument("--horizons", default="120,240",
                   help="Horizons in minutes — P5-3 winners")
    p.add_argument("--gate-multiples", default="3.0,4.0,5.0",
                   help="Gate strictnesses to evaluate")
    p.add_argument("--days", type=int, default=90,
                   help="Days of cached 1m history per symbol")
    p.add_argument("--cost-bps", type=float, default=24.0,
                   help="Retail-tier round-trip cost in bps")
    p.add_argument("--out", default="docs/P5-4-POOLED-VALIDATION-FINDINGS.md")
    args = p.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    gate_mults = [float(g) for g in args.gate_multiples.split(",") if g.strip()]
    base_config = AppConfig()

    print(f"# P5-4 pooled validation", file=sys.stderr)
    print(f"# pool: {','.join(symbols)} ({args.days}d)", file=sys.stderr)
    print(f"# eval focus: BNBUSDT, SOLUSDT × {horizons}m × gate {gate_mults}",
          file=sys.stderr)

    t0 = time.monotonic()
    all_results = []
    for hz in horizons:
        result = _evaluate_pooled_at_horizon(
            symbols, hz, args.days, base_config, gate_mults, args.cost_bps
        )
        all_results.append(result)
    elapsed = time.monotonic() - t0
    print(f"  total eval: {elapsed:.1f}s", file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        fh.write("# P5-4: Pooled Training × 90d Validation of P5-3 Winners\n\n")
        fh.write(f"- Training pool: {', '.join(symbols)} (90 days)\n")
        fh.write(f"- Eval focus: BNBUSDT + SOLUSDT (the P5-3 retail-cost winners)\n")
        fh.write(f"- Horizons: {horizons} minutes\n")
        fh.write(f"- Gate multiples: {gate_mults}\n")
        fh.write(f"- Cost: {args.cost_bps} bp round-trip (retail)\n")
        fh.write(f"- Training mode: **pooled (matches live observer)**\n")
        fh.write(f"- Total compute: {elapsed:.1f}s\n\n")
        fh.write(_verdict(all_results, args.cost_bps))
        fh.write("\n## Full results — all symbols × all gates\n\n")

        # Full breakdown
        for batch in all_results:
            hz = batch["horizon"]
            fh.write(f"\n### Horizon = {hz}m\n\n")
            fh.write(f"- Pooled training: n={batch.get('pooled_n_train', '?')}, "
                     f"base_win_rate={batch.get('base_win_rate', 0):.3f}\n\n")
            fh.write("| Symbol | Slice | Gate × | n | Win rate | Gross | Net |\n")
            fh.write("| --- | --- | ---: | ---: | ---: | ---: | ---: |\n")
            for r in batch["results"]:
                wr = "—" if r["win_rate"] is None else f"{r['win_rate']*100:5.1f}%"
                gross = "—" if r["gross_bps"] is None else f"{r['gross_bps']:+.2f}"
                net = "—" if r["net_bps"] is None else f"{r['net_bps']:+.2f}"
                bold = "**" if r["net_bps"] is not None and r["net_bps"] >= 0 else ""
                fh.write(
                    f"| {r['symbol']} | `{r['slice']}` | {r['gate_multiple']:.1f} | "
                    f"{r['n']} | {wr} | {gross} | {bold}{net}{bold} |\n"
                )

    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
