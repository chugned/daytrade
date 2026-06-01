"""Iteration 20: stack confidence-threshold + lo-vol filter.

The two strongest single-iteration signals tonight:
  - confidence ≥ 0.65 (it. 19): BNB 365d z=+3.02
  - lo-vol bars only (it. 16):  BNB 365d z=+3.04
Stack them. The intersection is much smaller — maybe coverage drops to
20% of bars — but the precision could be much higher. And critically:
does the combined signal survive 730d where the singles didn't?"""

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


def wf_stacked(X: pd.DataFrame, y: pd.Series, vol_mask: pd.Series,
              *, n_folds: int = 20, train_window: int = 300,
              test_window: int = 60, conf_thr: float = 0.65):
    """Walk-forward with both filters applied to the test set.
    Training is on ALL bars (don't shrink train set), evaluation is
    on bars that are both lo-vol AND high-confidence."""
    accs, covers = [], []
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
        vmask_te = vol_mask.iloc[te_lo:te_hi].values
        if y_tr.nunique() < 2:
            continue
        sc = StandardScaler()
        rf = RandomForestClassifier(
            n_estimators=200, max_depth=6, min_samples_leaf=5,
            random_state=42, n_jobs=1,
        )
        rf.fit(sc.fit_transform(X_tr), y_tr)
        proba = rf.predict_proba(sc.transform(X_te))
        conf = np.max(proba, axis=1)
        pred = rf.classes_[np.argmax(proba, axis=1)]
        combined = (conf >= conf_thr) & vmask_te  # lo-vol AND high conf
        if combined.sum() == 0:
            continue
        accs.append(float(accuracy_score(y_te.values[combined], pred[combined])))
        covers.append(float(combined.mean()))
    return accs, covers


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    for sym in ("BNBUSDT", "ETHUSDT"):
        for days in (365, 730):
            candles = download_history(sym, interval="1h", days=days)
            frame = ohlcv_to_frame(candles)
            feats = FeaturePipeline(cfg.features, cfg.indicators).transform(candles)
            labels = breakout_label(frame["close"], horizon=20, threshold=0.01)
            df = feats.copy()
            df["__y__"] = labels
            df["__vol__"] = feats["volatility"]
            df = df.dropna()
            y = df["__y__"].astype(int)
            X = df.drop(columns=["__y__", "__vol__"])
            # lo-vol = below 75th percentile of volatility
            thr = df["__vol__"].quantile(0.75)
            lo_vol_mask = df["__vol__"] <= thr

            print(f"\n=== {sym} × {days}d (n={len(df)}) ===")
            for conf in (0.55, 0.60, 0.65, 0.70):
                accs, covers = wf_stacked(X, y, lo_vol_mask, conf_thr=conf)
                if not accs:
                    print(f"  conf≥{conf}+lo-vol: no folds"); continue
                mean = statistics.mean(accs)
                stdev = statistics.stdev(accs) if len(accs) > 1 else 0
                se = stdev / (len(accs) ** 0.5) if len(accs) > 1 else 0
                z = (mean - 0.5) / se if se > 0 else 0
                cov = statistics.mean(covers)
                print(f"  conf≥{conf} + lo-vol:  n={len(accs):>2}  "
                      f"cov={cov*100:>5.1f}%  acc={mean*100:>5.1f}%  z={z:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
