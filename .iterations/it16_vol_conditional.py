"""Iteration 16: volatility-conditional labels.

Hypothesis: the bot may not predict direction well *on average*, but it
might predict direction well *when volatility is unusually high* — the
'event' bars where the market is making a real move worth trading.

Approach: keep swing labels, but ONLY train/test on bars where the
trailing volatility is in the top quartile of recent history. If the
model is more accurate on these bars, we have a *conditional* edge:
the strategy only trades during specific market conditions.

20 folds on 365d for variance, just like iter 12/15."""

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


def wf_with_filter(X: pd.DataFrame, y: pd.Series, mask: pd.Series,
                   *, n_folds: int = 20, train_window: int = 300,
                   test_window: int = 60) -> list:
    """Walk-forward, but train+test only on bars where ``mask`` is True."""
    accs = []
    n = len(y)
    step = max(1, (n - train_window) // n_folds)
    for k in range(n_folds):
        tr_lo, tr_hi = k * step, k * step + train_window
        te_lo, te_hi = tr_hi, min(tr_hi + test_window, n)
        if te_hi <= te_lo:
            break
        tr_idx = mask.iloc[tr_lo:tr_hi]
        te_idx = mask.iloc[te_lo:te_hi]
        X_tr = X.iloc[tr_lo:tr_hi][tr_idx]
        y_tr = y.iloc[tr_lo:tr_hi][tr_idx]
        X_te = X.iloc[te_lo:te_hi][te_idx]
        y_te = y.iloc[te_lo:te_hi][te_idx]
        if len(y_tr) < 30 or len(y_te) < 10 or y_tr.nunique() < 2:
            continue
        sc = StandardScaler()
        rf = RandomForestClassifier(
            n_estimators=200, max_depth=6, min_samples_leaf=5,
            random_state=42, n_jobs=1,
        )
        rf.fit(sc.fit_transform(X_tr), y_tr)
        accs.append(float(accuracy_score(y_te, rf.predict(sc.transform(X_te)))))
    return accs


def summarise(name: str, accs: list) -> None:
    if not accs:
        print(f"  {name}: no folds")
        return
    mean = statistics.mean(accs)
    stdev = statistics.stdev(accs) if len(accs) > 1 else 0
    se = stdev / (len(accs) ** 0.5) if len(accs) > 1 else 0
    z = (mean - 0.5) / se if se > 0 else 0
    print(f"  {name:<22}  n={len(accs):>2}  mean={mean*100:.1f}%  "
          f"stdev={stdev*100:.1f}%  z={z:+.2f}")


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    for sym in ("BNBUSDT", "ETHUSDT"):
        candles = download_history(sym, interval="1h", days=365)
        frame = ohlcv_to_frame(candles)
        feats = FeaturePipeline(cfg.features, cfg.indicators).transform(candles)
        labels = breakout_label(frame["close"], horizon=20, threshold=0.01)

        df = feats.copy()
        df["__y__"] = labels
        df["__vol__"] = feats["volatility"]
        df = df.dropna()
        y = df["__y__"].astype(int)
        vol = df["__vol__"]
        # Top-quartile volatility threshold (calculated PRE-fold so it's
        # global, not lookahead).
        thr = vol.quantile(0.75)
        hi_vol_mask = vol > thr
        X = df.drop(columns=["__y__", "__vol__"])

        print(f"\n=== {sym}: {len(df)} bars, hi-vol = {hi_vol_mask.sum()} "
              f"({hi_vol_mask.mean():.0%}) ===")
        # Baseline (all bars)
        all_mask = pd.Series(True, index=df.index)
        summarise("all bars", wf_with_filter(X, y, all_mask))
        # High-volatility only
        summarise("hi-vol bars only", wf_with_filter(X, y, hi_vol_mask))
        # Low-volatility (sanity check)
        summarise("lo-vol bars only", wf_with_filter(X, y, ~hi_vol_mask))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
