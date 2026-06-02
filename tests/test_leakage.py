"""Leakage / lookahead-bias tests — the most important correctness suite.

A feature is leak-free iff its value at bar ``t`` is unchanged by the arrival
of bars after ``t``. These tests assert exactly that across indicators and the
feature pipeline, and assert that labels (which DO use the future) are NaN
wherever the future is unknown.
"""

from __future__ import annotations

import numpy as np
import pytest

from daytrade.features import FeaturePipeline
from daytrade.indicators import core
from daytrade.indicators.frame import ohlcv_to_frame
from daytrade.labels import directional_label, future_return, make_labels

pytestmark = pytest.mark.leakage


def test_rsi_no_lookahead(uptrend_candles):
    """RSI at bar t is identical whether or not later bars exist."""
    frame = ohlcv_to_frame(uptrend_candles)
    full = core.rsi(frame["close"], 14)
    partial = core.rsi(frame["close"].iloc[:250], 14)
    assert np.allclose(full.iloc[:250].dropna(), partial.dropna())


def test_macd_no_lookahead(uptrend_candles):
    frame = ohlcv_to_frame(uptrend_candles)
    full = core.macd(frame["close"])
    partial = core.macd(frame["close"].iloc[:200])
    assert np.allclose(full["macd"].iloc[:200].dropna(), partial["macd"].dropna())


def test_trend_slope_no_lookahead(uptrend_candles):
    frame = ohlcv_to_frame(uptrend_candles)
    full = core.trend_slope(frame["close"], 20)
    partial = core.trend_slope(frame["close"].iloc[:180], 20)
    assert np.allclose(full.iloc[:180].dropna(), partial.dropna())


def test_atr_no_lookahead(uptrend_candles):
    frame = ohlcv_to_frame(uptrend_candles)
    full = core.atr(frame["high"], frame["low"], frame["close"], 14)
    partial = core.atr(
        frame["high"].iloc[:200], frame["low"].iloc[:200], frame["close"].iloc[:200], 14
    )
    assert np.allclose(full.iloc[:200].dropna(), partial.dropna())


def test_feature_pipeline_no_lookahead(uptrend_candles, config):
    """Every feature at bar t is unchanged by future bars."""
    pipe = FeaturePipeline(config.features, config.indicators)
    full = pipe.transform(uptrend_candles)
    partial = pipe.transform(uptrend_candles[:250])
    common = full.iloc[:250].dropna()
    aligned = partial.loc[common.index]
    assert np.allclose(common.values, aligned.values)


def test_feature_latest_equals_last_row(uptrend_candles, config):
    """The online path (latest) equals the last row of the offline path."""
    pipe = FeaturePipeline(config.features, config.indicators)
    offline_last = pipe.transform(uptrend_candles).iloc[-1]
    online = pipe.latest(uptrend_candles)
    assert np.allclose(offline_last.values, online.values, equal_nan=True)


def test_labels_reference_future(uptrend_candles):
    """The trailing ``horizon`` labels must be NaN — their future is unknown."""
    frame = ohlcv_to_frame(uptrend_candles)
    horizon = 5
    fwd = future_return(frame["close"], horizon)
    assert fwd.iloc[-horizon:].isna().all()
    assert fwd.iloc[:-horizon].notna().all()


def test_directional_label_matches_future_sign(uptrend_candles):
    frame = ohlcv_to_frame(uptrend_candles)
    fwd = future_return(frame["close"], 5)
    label = directional_label(frame["close"], 5)
    valid = fwd.dropna().index
    assert ((label.loc[valid] == 1.0) == (fwd.loc[valid] > 0)).all()


def test_make_labels_breakout_drops_indecisive(uptrend_candles):
    frame = ohlcv_to_frame(uptrend_candles)
    labels = make_labels(frame, horizon=5, threshold=0.01, kind="breakout")
    # Every non-NaN label is strictly 0 or 1.
    assert set(labels.dropna().unique()).issubset({0.0, 1.0})
