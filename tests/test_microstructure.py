"""Orderbook microstructure analysis tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from daytrade.config.schema import MicrostructureConfig
from daytrade.exchanges.mock import build_orderbook
from daytrade.microstructure import MicrostructureEngine, depth_imbalance, find_walls
from daytrade.models import Bias
from daytrade.models.market import OHLCV, OrderBookLevel


def _flat_candles(n=60, price=30_000.0):
    """A perfectly flat (zero-slope) candle series."""
    t0 = datetime(2026, 5, 17, tzinfo=timezone.utc)
    return [
        OHLCV(
            symbol="BTCUSDT",
            timestamp=t0 + timedelta(minutes=i),
            open=price,
            high=price,
            low=price,
            close=price,
        )
        for i in range(n)
    ]


def test_depth_imbalance_balanced_book():
    book = build_orderbook("BTC", 100.0, imbalance=0.0, jitter=0.0)
    assert abs(depth_imbalance(book, 10)) < 1e-6


def test_depth_imbalance_sell_heavy():
    book = build_orderbook("BTC", 100.0, imbalance=-0.3, jitter=0.0)
    assert depth_imbalance(book, 10) < 0


def test_depth_imbalance_buy_heavy():
    book = build_orderbook("BTC", 100.0, imbalance=0.3, jitter=0.0)
    assert depth_imbalance(book, 10) > 0


def test_find_walls_detects_large_level():
    levels = [OrderBookLevel(price=100 - i, quantity=1.0) for i in range(10)]
    levels[3] = OrderBookLevel(price=97, quantity=50.0)
    walls = find_walls(levels, wall_multiple=3.0)
    assert 97 in walls


def test_microstructure_bearish_on_sell_heavy_book(uptrend_candles):
    book = build_orderbook("BTCUSDT", 30000.0, imbalance=-0.4, jitter=0.0)
    sig = MicrostructureEngine().compute(book, uptrend_candles)
    assert sig.bias is Bias.BEARISH
    assert sig.score < 0


def test_microstructure_bullish_on_buy_heavy_book(uptrend_candles):
    book = build_orderbook("BTCUSDT", 30000.0, imbalance=0.4, jitter=0.0)
    sig = MicrostructureEngine().compute(book, uptrend_candles)
    assert sig.bias is Bias.BULLISH
    assert sig.score > 0


def test_microstructure_thin_liquidity_flag():
    # base_quantity tiny -> notional far below the thin threshold.
    book = build_orderbook("BTC", 100.0, base_quantity=0.001, jitter=0.0)
    sig = MicrostructureEngine().compute(book)
    assert sig.thin_liquidity is True


def test_microstructure_score_in_bounds(flat_candles):
    book = build_orderbook("BTCUSDT", 30000.0, imbalance=-0.1, jitter=0.0)
    sig = MicrostructureEngine().compute(book, flat_candles)
    assert -1.0 <= sig.score <= 1.0
    assert 0.0 <= sig.confidence <= 1.0


def test_microstructure_flags_chop_on_flat_market():
    """A directionless (zero-slope) market is a chop zone."""
    book = build_orderbook("BTCUSDT", 30000.0, jitter=0.0)
    assert MicrostructureEngine().compute(book, _flat_candles()).chop_zone is True


def test_microstructure_chop_threshold_is_configurable(uptrend_candles):
    """chop_max_trend_slope controls when a trending market counts as chop."""
    book = build_orderbook("BTCUSDT", 30000.0, jitter=0.0)
    # A permissive threshold: a real uptrend is NOT a chop zone.
    loose = MicrostructureConfig(chop_max_trend_slope=1e-6)
    assert MicrostructureEngine(loose).compute(book, uptrend_candles).chop_zone is False
    # An extreme threshold flags even a genuine trend as chop.
    strict = MicrostructureConfig(chop_max_trend_slope=1.0)
    assert MicrostructureEngine(strict).compute(book, uptrend_candles).chop_zone is True
