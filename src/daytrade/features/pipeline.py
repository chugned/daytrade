"""Feature engineering pipeline.

THE CRITICAL INVARIANT
----------------------
There is exactly **one** feature computation function — :func:`compute_features`.
The offline training path and the online inference path both call it. They
cannot drift apart, because there is nothing to drift: training computes
features over the whole history; inference computes features over the history
available so far and takes the last row. Same code, same columns, same order.

Every feature at bar ``t`` is causal — a function of bars ``<= t`` only — so a
feature value never changes when future bars later arrive. The leakage tests
assert exactly that.
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ..config.schema import FeatureConfig, IndicatorConfig
from ..indicators import core
from ..indicators.frame import ohlcv_to_frame
from ..models import OHLCV


def compute_features(
    frame: pd.DataFrame,
    feature_config: FeatureConfig | None = None,
    indicator_config: IndicatorConfig | None = None,
) -> pd.DataFrame:
    """Compute the full feature matrix from an OHLCV DataFrame.

    Args:
        frame: time-indexed OHLCV DataFrame (see ``ohlcv_to_frame``).

    Returns:
        A DataFrame of features aligned to ``frame.index``. Warmup rows where
        an indicator is undefined contain NaN; callers decide whether to drop
        them (training) or just read the final row (inference).
    """
    fcfg = feature_config or FeatureConfig()
    icfg = indicator_config or IndicatorConfig()

    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]
    feats: dict[str, pd.Series] = {}

    # --- Multi-horizon returns ---
    for w in fcfg.return_windows:
        feats[f"ret_{w}"] = core.returns(close, w)
    feats["logret_1"] = core.log_returns(close, 1)

    # --- Distributional shape of recent 1-bar returns ---
    ret1 = core.returns(close, 1)
    feats["roll_std"] = core.rolling_std(ret1, fcfg.rolling_std_window)
    feats["roll_skew"] = core.rolling_skew(ret1, fcfg.skew_kurtosis_window)
    feats["roll_kurt"] = core.rolling_kurtosis(ret1, fcfg.skew_kurtosis_window)

    # --- Trend / momentum / oscillators ---
    feats["rsi"] = core.rsi(close, icfg.rsi_period)
    macd_df = core.macd(close, icfg.ema_fast, icfg.ema_slow, icfg.macd_signal)
    feats["macd"] = macd_df["macd"]
    feats["macd_signal"] = macd_df["signal"]
    feats["macd_hist"] = macd_df["histogram"]

    ema_fast = core.ema(close, icfg.ema_fast)
    ema_slow = core.ema(close, icfg.ema_slow)
    feats["ema_gap"] = (ema_fast - ema_slow) / ema_slow.replace(0.0, np.nan)
    feats["momentum"] = core.momentum(close, icfg.momentum_window)
    feats["trend_slope"] = core.trend_slope(close, icfg.trend_window)
    feats["volatility"] = core.volatility(close, icfg.volatility_window)

    # --- Candle geometry ---
    rng = high - low
    feats["range_pct"] = rng / close.replace(0.0, np.nan)
    body = (close - frame["open"]).abs()
    feats["body_to_range"] = body / rng.replace(0.0, np.nan)
    feats["close_to_high"] = (high - close) / rng.replace(0.0, np.nan)

    # --- Volume ---
    vol_mean = volume.rolling(fcfg.rolling_std_window, min_periods=2).mean()
    vol_std = volume.rolling(fcfg.rolling_std_window, min_periods=2).std()
    feats["volume_z"] = (volume - vol_mean) / vol_std.replace(0.0, np.nan)
    feats["volume_chg"] = volume.pct_change()

    # --- Higher-timeframe context (Richer-Meta-Features) ----------------
    # Multi-timeframe trend filter research consistently shows that a
    # *higher-TF* slope is one of the single strongest features available
    # to a 1-minute strategy. Resample the 1m close to 15m / 1h, compute
    # the OLS slope on the higher TF, then forward-fill back onto the 1m
    # index so the feature is causal (uses only past data).
    if isinstance(frame.index, pd.DatetimeIndex):
        try:
            c15 = close.resample("15min").last().dropna()
            c1h = close.resample("1h").last().dropna()
            slope_15m_full = core.trend_slope(c15, max(2, min(8, len(c15) - 1)))
            slope_1h_full = core.trend_slope(c1h, max(2, min(4, len(c1h) - 1)))
            # Critical: shift by 1 HTF bar so each 1m bar sees the slope as
            # of the previous, FULLY CLOSED higher-TF bar. Without this the
            # 1m bars inside an unfinished HTF bucket would see "future"
            # values (data after their own timestamp), breaking causality.
            slope_15m = slope_15m_full.shift(1).reindex(frame.index, method="ffill")
            slope_1h = slope_1h_full.shift(1).reindex(frame.index, method="ffill")
            feats["slope_15m"] = slope_15m
            feats["slope_1h"] = slope_1h
        except Exception:  # noqa: BLE001 - never let a feature crash a cycle
            feats["slope_15m"] = pd.Series(np.nan, index=frame.index)
            feats["slope_1h"] = pd.Series(np.nan, index=frame.index)
    else:
        feats["slope_15m"] = pd.Series(np.nan, index=frame.index)
        feats["slope_1h"] = pd.Series(np.nan, index=frame.index)

    # --- Mean-reversion indicators (Richer-Meta-Features) ---------------
    # Surfaces the same signals the dedicated mean-reversion detector uses,
    # but as continuous features the meta-model can learn to weigh rather
    # than as hard binary gates.
    feats["ret_15"] = core.returns(close, 15)  # 15-min return
    feats["rsi_dist_oversold"] = feats["rsi"] - 30.0  # negative -> oversold
    feats["rsi_dist_overbought"] = feats["rsi"] - 70.0  # positive -> overbought
    feats["volume_ratio_20"] = volume / vol_mean.replace(0.0, np.nan)

    # --- Position-in-range features (causal, ratio-style) ---------------
    high_60 = high.rolling(60, min_periods=10).max()
    low_60 = low.rolling(60, min_periods=10).min()
    rng_60 = (high_60 - low_60).replace(0.0, np.nan)
    feats["pct_from_60_high"] = (close - high_60) / close.replace(0.0, np.nan)
    feats["pct_from_60_low"] = (close - low_60) / close.replace(0.0, np.nan)
    feats["pos_in_60_range"] = (close - low_60) / rng_60

    # --- Liquidation-cascade fingerprint as features --------------------
    # The 90-day sweep showed CASCADE_EXHAUSTION carries a real (and
    # symbol-specific) edge — strong on SOL at 30m horizon, opposite sign
    # on ETH. Rather than hard-gating, we expose the fingerprint to the
    # meta-model so it can learn the per-symbol weighting. See
    # docs/RESEARCH-90D-FINDINGS.md.
    open_ = frame["open"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = tr.rolling(14, min_periods=14).mean()
    # vol baseline shifted by 1 so the ratio at bar t uses bars < t only
    vol_baseline = volume.rolling(20, min_periods=20).mean().shift(1)
    body = close - open_
    body_atr_mult = body / atr14.replace(0.0, np.nan)
    vol_spike = volume / vol_baseline.replace(0.0, np.nan)
    rng_bar = (high - low).replace(0.0, np.nan)
    lower_wick = pd.concat([open_, close], axis=1).min(axis=1) - low
    lower_wick_ratio = (lower_wick / rng_bar).clip(lower=0.0, upper=1.0)
    feats["cascade_body_atr"] = body_atr_mult
    feats["cascade_vol_spike"] = vol_spike
    feats["cascade_lower_wick"] = lower_wick_ratio
    # Binary state flags — the same gate-style classification, just
    # surfaced as 0/1 features rather than a hard block.
    active = ((body_atr_mult <= -2.0) & (vol_spike >= 3.0)).astype(float)
    prev_active_body = body_atr_mult.shift(1)
    exhaustion = (
        (prev_active_body <= -2.0) & (lower_wick_ratio >= 0.55) & (vol_spike >= 1.5)
    ).astype(float)
    feats["cascade_active"] = active
    feats["cascade_exhaustion"] = exhaustion

    out = pd.DataFrame(feats, index=frame.index)
    # Replace +/-inf (from rare divide-by-zero) with NaN so they are handled
    # like any other warmup gap rather than poisoning the model.
    return out.replace([np.inf, -np.inf], np.nan)


def feature_columns(
    feature_config: FeatureConfig | None = None,
) -> List[str]:
    """The canonical ordered feature-column list (used to lock train/infer)."""
    fcfg = feature_config or FeatureConfig()
    cols = [f"ret_{w}" for w in fcfg.return_windows]
    cols += [
        "logret_1",
        "roll_std",
        "roll_skew",
        "roll_kurt",
        "rsi",
        "macd",
        "macd_signal",
        "macd_hist",
        "ema_gap",
        "momentum",
        "trend_slope",
        "volatility",
        "range_pct",
        "body_to_range",
        "close_to_high",
        "volume_z",
        "volume_chg",
        # Richer-Meta-Features additions (HTF + MR + position-in-range):
        "slope_15m",
        "slope_1h",
        "ret_15",
        "rsi_dist_oversold",
        "rsi_dist_overbought",
        "volume_ratio_20",
        "pct_from_60_high",
        "pct_from_60_low",
        "pos_in_60_range",
        # Cascade-As-Feature additions:
        "cascade_body_atr",
        "cascade_vol_spike",
        "cascade_lower_wick",
        "cascade_active",
        "cascade_exhaustion",
    ]
    return cols


class FeaturePipeline:
    """Stateless wrapper exposing the shared feature logic for both paths."""

    def __init__(
        self,
        feature_config: FeatureConfig | None = None,
        indicator_config: IndicatorConfig | None = None,
    ) -> None:
        self.feature_config = feature_config or FeatureConfig()
        self.indicator_config = indicator_config or IndicatorConfig()

    @property
    def columns(self) -> List[str]:
        return feature_columns(self.feature_config)

    def transform(self, candles: List[OHLCV]) -> pd.DataFrame:
        """OFFLINE path: features for every bar (used to build training sets)."""
        frame = ohlcv_to_frame(candles)
        feats = compute_features(frame, self.feature_config, self.indicator_config)
        return feats[self.columns]

    def transform_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Same as :meth:`transform` but from an already-built OHLCV frame."""
        feats = compute_features(frame, self.feature_config, self.indicator_config)
        return feats[self.columns]

    def latest(self, candles: List[OHLCV]) -> pd.Series:
        """ONLINE path: the feature row for the most recent bar.

        This is the exact last row of :meth:`transform` — by construction,
        not by convention.
        """
        feats = self.transform(candles)
        if feats.empty:
            raise ValueError("not enough data to compute features")
        return feats.iloc[-1]
