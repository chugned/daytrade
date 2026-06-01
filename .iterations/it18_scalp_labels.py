"""Iteration 18: scalp labels (3 bars × 0.2%) — orthogonal to swing.

Maybe very short-horizon mean-reversion has signal where longer
horizons don't. Test at 365 and 730 days, 20 folds, BNB+ETH+BTC."""

from __future__ import annotations

import statistics

from daytrade.config import load_config
from daytrade.models.enums import ModelKind
from daytrade.research.history import download_history
from daytrade.validation import walk_forward_validate


def run(sym: str, days: int, cfg) -> None:
    candles = download_history(sym, interval="1h", days=days)
    report = walk_forward_validate(candles, cfg)
    accs = [f.test_accuracy for f in report.folds]
    if not accs:
        print(f"  {sym} × {days}d: no folds"); return
    mean = statistics.mean(accs)
    stdev = statistics.stdev(accs) if len(accs) > 1 else 0
    se = stdev / (len(accs) ** 0.5) if len(accs) > 1 else 0
    z = (mean - 0.5) / se if se > 0 else 0
    print(f"  {sym:<8} × {days:>3}d  n={len(accs):>2}  mean={mean*100:.1f}%  "
          f"stdev={stdev*100:.1f}%  z={z:+.2f}")


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    cfg = cfg.model_copy(update={
        "ml": cfg.ml.model_copy(update={"model_kind": ModelKind.RANDOM_FOREST}),
        "labels": cfg.labels.model_copy(update={
            "horizon": 3, "breakout_threshold": 0.002,
        }),
        "walkforward": cfg.walkforward.model_copy(update={
            "n_folds": 20, "train_window": 300, "test_window": 60,
        }),
    })
    print(f"scalp labels: horizon=3 bars (3h) × 0.2% threshold")
    print(f"n_folds={cfg.walkforward.n_folds} model={cfg.ml.model_kind}")
    for sym in ("BTCUSDT", "ETHUSDT", "BNBUSDT"):
        for d in (365, 730):
            run(sym, d, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
