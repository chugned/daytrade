"""Multi-timeframe trend filter tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from daytrade.models import OHLCV
from daytrade.observatory.multi_timeframe import (
    MTFAlignmentResult,
    check_higher_tf_alignment,
)


def _linear_candles(n: int = 240, start_price: float = 100.0,
                    drift_per_bar: float = 0.001) -> List[OHLCV]:
    """A deterministic candle ramp — used to construct trended timeframes."""
    t0 = datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc)
    candles: List[OHLCV] = []
    p = start_price
    for i in range(n):
        nxt = p * (1.0 + drift_per_bar)
        candles.append(OHLCV(symbol="X",
                             timestamp=t0 + timedelta(minutes=i),
                             open=p, high=max(p, nxt), low=min(p, nxt),
                             close=nxt, volume=1.0))
        p = nxt
    return candles


def test_uptrend_allows_a_buy():
    candles = _linear_candles(drift_per_bar=+0.001)
    result = check_higher_tf_alignment(candles, "buy")
    assert isinstance(result, MTFAlignmentResult)
    assert result.aligned is True
    assert result.slope_15m is not None and result.slope_15m > 0
    assert result.slope_1h is not None and result.slope_1h > 0


def test_uptrend_blocks_a_sell():
    candles = _linear_candles(drift_per_bar=+0.001)
    result = check_higher_tf_alignment(candles, "sell")
    assert result.aligned is False


def test_downtrend_blocks_a_buy():
    candles = _linear_candles(drift_per_bar=-0.001)
    result = check_higher_tf_alignment(candles, "buy")
    assert result.aligned is False


def test_downtrend_allows_a_sell():
    candles = _linear_candles(drift_per_bar=-0.001)
    result = check_higher_tf_alignment(candles, "sell")
    assert result.aligned is True


def test_no_opinion_on_non_directional_action():
    result = check_higher_tf_alignment(_linear_candles(), "hold")
    assert result.aligned is True


def test_insufficient_history_is_permissive():
    """Too little history -> allow through, do not artificially silence."""
    # Only 30 1m candles -> < 8 fifteen-minute bars after resample.
    result = check_higher_tf_alignment(_linear_candles(n=30), "buy")
    assert result.aligned is True
    assert "insufficient" in result.reason.lower()


def test_min_slope_threshold_can_make_a_weak_trend_unaligned():
    """A very small drift should fail a 1e-4 min_slope check."""
    weak = _linear_candles(n=240, drift_per_bar=+5e-6)
    strict = check_higher_tf_alignment(weak, "buy", min_slope=1e-3)
    assert strict.aligned is False
