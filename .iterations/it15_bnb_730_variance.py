"""Iteration 15: BNB 1h × 730d × swing + RF with 20 folds.

The last marginally-significant result alive. Stress test it across
2 years with 20-fold variance accounting."""

from __future__ import annotations

import statistics

from daytrade.config import load_config
from daytrade.models.enums import ModelKind
from daytrade.research.history import download_history
from daytrade.validation import walk_forward_validate


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    cfg = cfg.model_copy(update={
        "ml": cfg.ml.model_copy(update={"model_kind": ModelKind.RANDOM_FOREST}),
        "labels": cfg.labels.model_copy(update={
            "horizon": 20, "breakout_threshold": 0.01,
        }),
        "walkforward": cfg.walkforward.model_copy(update={
            "n_folds": 20, "train_window": 300, "test_window": 60,
        }),
    })

    candles = download_history("BNBUSDT", interval="1h", days=730)
    print(f"BNBUSDT × 730d × 1h: {len(candles)} bars")
    report = walk_forward_validate(candles, cfg)
    accs = [f.test_accuracy for f in report.folds]
    if not accs:
        print("no folds!")
        return 1
    mean = statistics.mean(accs)
    med = statistics.median(accs)
    stdev = statistics.stdev(accs) if len(accs) > 1 else 0
    se = stdev / (len(accs) ** 0.5) if len(accs) > 1 else 0
    z = (mean - 0.5) / se if se > 0 else 0
    print(f"\nBNB 730d, {len(accs)} folds, swing+RF:")
    print(f"  mean WF acc  = {mean * 100:.1f}%")
    print(f"  median       = {med * 100:.1f}%")
    print(f"  stdev        = {stdev * 100:.1f}%")
    print(f"  min .. max   = {min(accs) * 100:.1f}% .. {max(accs) * 100:.1f}%")
    print(f"  z vs 50%     = {z:+.2f} "
          f"({'SIGNIFICANT' if abs(z) > 1.96 else 'not significant'} at p=0.05)")
    # Per-fold listing
    print("\nPer-fold accuracies (chronological):")
    for i, f in enumerate(report.folds, 1):
        bar = "█" * int(f.test_accuracy * 50)
        print(f"  {i:>2}: {f.test_accuracy * 100:>5.1f}%  {bar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
