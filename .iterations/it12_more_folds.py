"""Iteration 12: more walk-forward folds for variance stabilisation.

Iteration 11 showed WF accuracy varies wildly between adjacent windows
(69% / 38% / 67% / 40%) — that's the signature of a high-variance
estimator with too few folds. Default is n_folds=5. Try n_folds=20
on the 365d ETH 1h swing-labels + RF config that scored 67% on it.9,
and see whether the average converges or whether the wide spread
across folds confirms the signal is noise."""

from __future__ import annotations

import statistics

from daytrade.config import load_config
from daytrade.models.enums import ModelKind
from daytrade.research import render_research, run_research
from daytrade.research.history import download_history
from daytrade.validation import walk_forward_validate
from rich.console import Console


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    # 20 folds — much more granular than the default 5.
    cfg = cfg.model_copy(update={
        "ml": cfg.ml.model_copy(update={"model_kind": ModelKind.RANDOM_FOREST}),
        "labels": cfg.labels.model_copy(update={
            "horizon": 20, "breakout_threshold": 0.01,
        }),
        "walkforward": cfg.walkforward.model_copy(update={
            "n_folds": 20, "train_window": 300, "test_window": 60,
        }),
    })
    print(f"n_folds={cfg.walkforward.n_folds} "
          f"train_window={cfg.walkforward.train_window} "
          f"test_window={cfg.walkforward.test_window}")

    for sym in ("ETHUSDT", "BNBUSDT"):
        candles = download_history(sym, interval="1h", days=365)
        report = walk_forward_validate(candles, cfg)
        accs = [f.test_accuracy for f in report.folds]
        if not accs:
            print(f"{sym}: no folds")
            continue
        mean = statistics.mean(accs)
        med = statistics.median(accs)
        stdev = statistics.stdev(accs) if len(accs) > 1 else 0.0
        print(f"\n{sym}: {len(accs)} folds")
        print(f"  mean WF acc  = {mean * 100:.1f}%")
        print(f"  median WF acc= {med * 100:.1f}%")
        print(f"  stdev        = {stdev * 100:.1f}%")
        print(f"  min .. max   = {min(accs) * 100:.1f}% .. {max(accs) * 100:.1f}%")
        # Distribution buckets
        buckets = [0, 0, 0, 0, 0]  # <45, 45-49, 49-51, 51-55, >55
        for a in accs:
            p = a * 100
            if p < 45:
                buckets[0] += 1
            elif p < 49:
                buckets[1] += 1
            elif p <= 51:
                buckets[2] += 1
            elif p <= 55:
                buckets[3] += 1
            else:
                buckets[4] += 1
        print(f"  distribution: <45%={buckets[0]} 45-49%={buckets[1]} "
              f"49-51%={buckets[2]} 51-55%={buckets[3]} >55%={buckets[4]}")
        # one-sample z-test against 50% null
        if stdev > 0 and len(accs) > 1:
            se = stdev / (len(accs) ** 0.5)
            z = (mean - 0.5) / se
            print(f"  z-score vs 50% null: {z:+.2f} "
                  f"({'significant' if abs(z) > 1.96 else 'not significant'} "
                  f"at p=0.05)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
