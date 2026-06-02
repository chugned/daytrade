"""Feature pipeline tests (leakage-specific cases live in test_leakage.py)."""

from __future__ import annotations

from daytrade.features import FeaturePipeline, compute_features, feature_columns
from daytrade.indicators.frame import ohlcv_to_frame


def test_feature_columns_stable(config):
    cols = feature_columns(config.features)
    assert len(cols) == len(set(cols))  # no duplicates
    assert "rsi" in cols and "macd" in cols


def test_pipeline_transform_has_expected_columns(uptrend_candles, config):
    pipe = FeaturePipeline(config.features, config.indicators)
    feats = pipe.transform(uptrend_candles)
    assert list(feats.columns) == pipe.columns


def test_pipeline_transform_row_count(uptrend_candles, config):
    pipe = FeaturePipeline(config.features, config.indicators)
    feats = pipe.transform(uptrend_candles)
    assert len(feats) == len(uptrend_candles)


def test_features_have_no_inf(uptrend_candles, config):
    frame = ohlcv_to_frame(uptrend_candles)
    feats = compute_features(frame, config.features, config.indicators)
    import numpy as np

    assert not np.isinf(feats.to_numpy(dtype=float)).any()


def test_pipeline_latest_is_a_series(uptrend_candles, config):
    pipe = FeaturePipeline(config.features, config.indicators)
    row = pipe.latest(uptrend_candles)
    assert list(row.index) == pipe.columns


def test_ohlcv_frame_sorted_and_unique(uptrend_candles):
    frame = ohlcv_to_frame(uptrend_candles)
    assert frame.index.is_monotonic_increasing
    assert frame.index.is_unique
