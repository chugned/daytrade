"""Iteration 21: lower-cap symbols.

Most academic crypto research finds edge in less-liquid tokens where
retail flow and noise dominate. Test DOGE, LTC, AVAX, LINK at 1h × 365d
with swing labels + RF + 20 folds."""

from __future__ import annotations

import statistics

from daytrade.config import load_config
from daytrade.models.enums import ModelKind
from daytrade.research.history import download_history
from daytrade.validation import walk_forward_validate


def run(sym: str, days: int, cfg) -> None:
    try:
        candles = download_history(sym, interval="1h", days=days)
    except Exception as exc:
        print(f"  {sym}: download failed ({exc})")
        return
    if len(candles) < 500:
        print(f"  {sym}: only {len(candles)} bars, skipping")
        return
    report = walk_forward_validate(candles, cfg)
    accs = [f.test_accuracy for f in report.folds]
    if not accs:
        print(f"  {sym}: no folds")
        return
    mean = statistics.mean(accs)
    stdev = statistics.stdev(accs) if len(accs) > 1 else 0
    se = stdev / (len(accs) ** 0.5) if len(accs) > 1 else 0
    z = (mean - 0.5) / se if se > 0 else 0
    sig = "**SIG**" if abs(z) > 1.96 else "      "
    print(f"  {sym:<8}  bars={len(candles):>5}  n={len(accs):>2}  "
          f"mean={mean*100:>5.1f}%  stdev={stdev*100:>4.1f}%  z={z:+5.2f}  {sig}")


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
    # Lower-cap-ish liquid alts. All listed on Binance spot with USDT.
    lower_caps = ("DOGEUSDT", "LTCUSDT", "AVAXUSDT", "LINKUSDT",
                  "DOTUSDT", "MATICUSDT")
    for d in (365, 730):
        print(f"\n=== {d}d window ===")
        for sym in lower_caps:
            run(sym, d, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
