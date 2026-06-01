"""Iteration 14: train on a curated 12-feature subset, drop the rest.

Iterations 13a/13b showed the same picture on BNB and ETH:
  - Position-in-range features are top 10 winners
  - Cascade features are bottom-quartile
  - HTF slopes are weak

If most features are noise, dropping them should *raise* OOS accuracy
(less to overfit on, same true signal). If WF acc stays flat, the
signal is symbol-specific noise either way. 20 folds for proper
variance estimation."""

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


# The 12 features that were top-importance across BOTH symbols.
TOP_FEATURES = [
    "roll_skew", "roll_kurt", "roll_std", "volatility",
    "macd", "macd_signal", "macd_hist",
    "ema_gap", "trend_slope",
    "pct_from_60_high", "pct_from_60_low", "pos_in_60_range",
]


def manual_walk_forward(X: pd.DataFrame, y: pd.Series, *,
                        n_folds: int = 20,
                        train_window: int = 300,
                        test_window: int = 60) -> list:
    """Same walk-forward semantics as the lab, but on a *subset* of features
    so we can isolate the effect of feature pruning."""
    n = len(y)
    step = max(1, (n - train_window) // n_folds)
    accs = []
    for k in range(n_folds):
        tr_lo = k * step
        tr_hi = tr_lo + train_window
        te_lo = tr_hi
        te_hi = min(te_lo + test_window, n)
        if te_hi <= te_lo:
            break
        X_tr = X.iloc[tr_lo:tr_hi]
        y_tr = y.iloc[tr_lo:tr_hi]
        X_te = X.iloc[te_lo:te_hi]
        y_te = y.iloc[te_lo:te_hi]
        if y_tr.nunique() < 2:
            continue
        scaler = StandardScaler()
        Xs_tr = scaler.fit_transform(X_tr)
        Xs_te = scaler.transform(X_te)
        rf = RandomForestClassifier(
            n_estimators=200, max_depth=6, min_samples_leaf=5,
            random_state=42, n_jobs=1,
        )
        rf.fit(Xs_tr, y_tr)
        pred = rf.predict(Xs_te)
        accs.append(float(accuracy_score(y_te, pred)))
    return accs


def main() -> int:
    cfg = load_config(load_dotenv_file=False)

    for sym in ("BNBUSDT", "ETHUSDT"):
        candles = download_history(sym, interval="1h", days=365)
        frame = ohlcv_to_frame(candles)
        pipe = FeaturePipeline(cfg.features, cfg.indicators)
        feats = pipe.transform(candles)
        labels = breakout_label(frame["close"], horizon=20, threshold=0.01)

        df = feats.copy()
        df["__y__"] = labels
        df = df.dropna()
        y = df["__y__"].astype(int)

        print(f"\n=== {sym} ({len(df)} samples) ===")

        for name, cols in (("ALL 35 features", list(feats.columns)),
                           ("Top 12 only",     TOP_FEATURES)):
            X = df[cols]
            accs = manual_walk_forward(X, y)
            if not accs:
                print(f"  {name}: no folds")
                continue
            mean = statistics.mean(accs)
            stdev = statistics.stdev(accs) if len(accs) > 1 else 0
            se = stdev / (len(accs) ** 0.5) if len(accs) > 1 else 0
            z = (mean - 0.5) / se if se > 0 else 0
            print(f"  {name:<18}  n={len(accs):>2}  mean={mean*100:.1f}%  "
                  f"stdev={stdev*100:.1f}%  z={z:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
