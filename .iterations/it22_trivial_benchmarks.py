"""Iteration 22: trivial benchmarks.

Does the trained RF beat dumb baselines?
  - 'majority class':  always predict the most common label
  - 'momentum':        predict same direction as last bar's return sign
  - 'mean-revert':     predict opposite of last bar's return sign

If the RF can't beat these, all 'WEAK SIGNAL' verdicts tonight were
the lab giving credit to memorisation that didn't beat a rule a
child could code in 10 seconds."""

from __future__ import annotations

import statistics

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler

from daytrade.config import load_config
from daytrade.features import FeaturePipeline
from daytrade.indicators.frame import ohlcv_to_frame
from daytrade.labels.generate import breakout_label
from daytrade.research.history import download_history


def wf_with_baselines(X: pd.DataFrame, y: pd.Series, close: pd.Series,
                     *, n_folds: int = 20, train_window: int = 300,
                     test_window: int = 60):
    """Return RF and three baseline accuracies per fold."""
    rf_accs, maj_accs, mom_accs, rev_accs = [], [], [], []
    n = len(y)
    step = max(1, (n - train_window) // n_folds)
    for k in range(n_folds):
        tr_lo, tr_hi = k * step, k * step + train_window
        te_lo, te_hi = tr_hi, min(tr_hi + test_window, n)
        if te_hi <= te_lo:
            break
        X_tr = X.iloc[tr_lo:tr_hi]
        y_tr = y.iloc[tr_lo:tr_hi]
        X_te = X.iloc[te_lo:te_hi]
        y_te = y.iloc[te_lo:te_hi]
        if y_tr.nunique() < 2:
            continue
        sc = StandardScaler()
        rf = RandomForestClassifier(
            n_estimators=200, max_depth=6, min_samples_leaf=5,
            random_state=42, n_jobs=1,
        )
        rf.fit(sc.fit_transform(X_tr), y_tr)
        rf_pred = rf.predict(sc.transform(X_te))
        rf_accs.append(float(accuracy_score(y_te, rf_pred)))

        # Majority class baseline
        majority = int(y_tr.mode()[0])
        maj_accs.append(float(accuracy_score(y_te, [majority] * len(y_te))))

        # Momentum: predict 'up' if last close in train > prior close
        # For each test bar, use that bar's prior-bar direction.
        close_te = close.iloc[te_lo:te_hi]
        prev_close = close.shift(1).iloc[te_lo:te_hi]
        mom_pred = (close_te > prev_close).astype(int).values
        rev_pred = (close_te <= prev_close).astype(int).values
        mom_accs.append(float(accuracy_score(y_te, mom_pred)))
        rev_accs.append(float(accuracy_score(y_te, rev_pred)))
    return rf_accs, maj_accs, mom_accs, rev_accs


def summary(name: str, accs: list) -> str:
    if not accs:
        return f"{name}: no folds"
    mean = statistics.mean(accs)
    stdev = statistics.stdev(accs) if len(accs) > 1 else 0
    se = stdev / (len(accs) ** 0.5) if len(accs) > 1 else 0
    z = (mean - 0.5) / se if se > 0 else 0
    return f"{name:<14} n={len(accs):>2}  mean={mean*100:>5.1f}%  z={z:+.2f}"


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    cfg = cfg.model_copy(update={
        "labels": cfg.labels.model_copy(update={
            "horizon": 20, "breakout_threshold": 0.01,
        }),
    })
    for sym in ("BNBUSDT", "ETHUSDT", "BTCUSDT"):
        for days in (365, 730):
            candles = download_history(sym, interval="1h", days=days)
            frame = ohlcv_to_frame(candles)
            feats = FeaturePipeline(cfg.features, cfg.indicators).transform(candles)
            labels = breakout_label(frame["close"], horizon=20, threshold=0.01)
            df = feats.copy()
            df["__y__"] = labels
            df["__close__"] = frame["close"]
            df = df.dropna()
            y = df["__y__"].astype(int)
            close = df["__close__"]
            X = df.drop(columns=["__y__", "__close__"])

            rf, maj, mom, rev = wf_with_baselines(X, y, close)
            print(f"\n=== {sym} × {days}d (n={len(df)}) ===")
            print(f"  {summary('RF',           rf)}")
            print(f"  {summary('majority',     maj)}")
            print(f"  {summary('momentum',     mom)}")
            print(f"  {summary('mean-revert',  rev)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
