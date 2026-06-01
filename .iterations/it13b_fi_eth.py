"""Iteration 13: feature importance on BNB swing+RF × 365d.

BNB at 20 folds had marginal z=+2.31 significance. Which features
contribute? If the cascade / MTF / position-in-range columns added
this session show up in the top importance ranks, the merge work
was directional. If only the legacy indicators (RSI/EMA/macd_hist)
matter, the new work was decorative."""

from __future__ import annotations

from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

from daytrade.config import load_config
from daytrade.features import FeaturePipeline
from daytrade.indicators.frame import ohlcv_to_frame
from daytrade.labels.generate import breakout_label
from daytrade.research.history import download_history


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    candles = download_history("ETHUSDT", interval="1h", days=365)
    frame = ohlcv_to_frame(candles)
    pipe = FeaturePipeline(cfg.features, cfg.indicators)
    feats = pipe.transform(candles)
    labels = breakout_label(frame["close"], horizon=20, threshold=0.01)

    # Align + drop NaNs.
    df = feats.copy()
    df["__y__"] = labels
    df = df.dropna()
    X = df.drop(columns=["__y__"])
    y = df["__y__"].astype(int)
    print(f"samples: {len(df)}  features: {X.shape[1]}  "
          f"class balance: up={y.mean():.1%}")

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    rf = RandomForestClassifier(
        n_estimators=200, max_depth=6, min_samples_leaf=5,
        random_state=42, n_jobs=1,
    )
    rf.fit(Xs, y)
    print(f"in-sample train acc: {rf.score(Xs, y):.3f}")

    importances = sorted(
        zip(X.columns, rf.feature_importances_),
        key=lambda p: -p[1],
    )
    new_cols = {
        "slope_15m", "slope_1h",
        "ret_15", "rsi_dist_oversold", "rsi_dist_overbought",
        "volume_ratio_20",
        "pct_from_60_high", "pct_from_60_low", "pos_in_60_range",
        "cascade_body_atr", "cascade_vol_spike", "cascade_lower_wick",
        "cascade_active", "cascade_exhaustion",
    }
    print("\nFeature importance ranking (top 28):")
    new_in_top10 = 0
    legacy_in_top10 = 0
    for i, (name, imp) in enumerate(importances[:28], 1):
        tag = "NEW" if name in new_cols else "legacy"
        if i <= 10:
            if tag == "NEW":
                new_in_top10 += 1
            else:
                legacy_in_top10 += 1
        print(f"  {i:>2}. {name:<24} {imp:.4f}  [{tag}]")
    print(f"\nTop 10: {new_in_top10} new / {legacy_in_top10} legacy "
          f"(out of {len(new_cols)} new vs {X.shape[1] - len(new_cols)} legacy)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
