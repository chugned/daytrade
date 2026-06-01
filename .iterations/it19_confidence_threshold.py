"""Iteration 19: confidence-thresholded predictions.

A deployment-realistic test: the bot doesn't have to predict every bar.
It can choose to act only when its predicted probability exceeds some
threshold (the meta-label-edge gate already does something like this).
Does the model achieve high precision *on the bars it's most confident
about*, even if average accuracy is at noise?

This is the 'precision @ top-K' question — much more relevant to
trading PnL than mean accuracy."""

from __future__ import annotations

import statistics
from typing import List

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


def wf_with_confidence(X: pd.DataFrame, y: pd.Series,
                      *, n_folds: int = 20,
                      train_window: int = 300,
                      test_window: int = 60,
                      thresholds: List[float] = [0.55, 0.60, 0.65, 0.70]):
    """Run walk-forward, recording accuracy on bars where the predicted
    probability >= each threshold."""
    by_thr = {t: [] for t in thresholds}
    coverage = {t: [] for t in thresholds}
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
        proba = rf.predict_proba(sc.transform(X_te))
        # Max-class confidence; predicted label is the argmax.
        conf = np.max(proba, axis=1)
        pred = rf.classes_[np.argmax(proba, axis=1)]
        for thr in thresholds:
            mask = conf >= thr
            if mask.sum() == 0:
                continue
            acc = accuracy_score(y_te.values[mask], pred[mask])
            by_thr[thr].append(acc)
            coverage[thr].append(mask.mean())
    return by_thr, coverage


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    cfg = cfg.model_copy(update={
        "labels": cfg.labels.model_copy(update={
            "horizon": 20, "breakout_threshold": 0.01,
        }),
    })

    for sym in ("BNBUSDT", "ETHUSDT"):
        for days in (365, 730):
            candles = download_history(sym, interval="1h", days=days)
            frame = ohlcv_to_frame(candles)
            feats = FeaturePipeline(cfg.features, cfg.indicators).transform(candles)
            labels = breakout_label(frame["close"], horizon=20, threshold=0.01)
            df = feats.copy()
            df["__y__"] = labels
            df = df.dropna()
            y = df["__y__"].astype(int)
            X = df.drop(columns=["__y__"])
            by_thr, coverage = wf_with_confidence(X, y)
            print(f"\n=== {sym} × {days}d ({len(df)} samples) ===")
            print(f"  {'threshold':>9}  {'n folds':>8}  "
                  f"{'avg coverage':>12}  {'mean acc':>9}  {'z':>6}")
            for thr, accs in by_thr.items():
                if not accs:
                    print(f"  {thr:>9.2f}  {'-':>8}  {'-':>12}  "
                          f"{'(no high-confidence preds)':>9}")
                    continue
                mean_a = statistics.mean(accs)
                cov = statistics.mean(coverage[thr]) if coverage[thr] else 0
                stdev = statistics.stdev(accs) if len(accs) > 1 else 0
                se = stdev / (len(accs) ** 0.5) if len(accs) > 1 else 0
                z = (mean_a - 0.5) / se if se > 0 else 0
                print(f"  {thr:>9.2f}  {len(accs):>8}  {cov*100:>11.1f}%  "
                      f"{mean_a*100:>7.1f}%  {z:>+6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
