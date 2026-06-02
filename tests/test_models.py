"""Domain model validation and serialization tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from daytrade.models import (
    OHLCV,
    Action,
    OrderBookSnapshot,
    PriceTick,
    Side,
    TradingDecision,
)
from daytrade.models._base import normalize_timestamp


def test_timestamp_normalization_units():
    """Epoch values are auto-detected and normalized to tz-aware UTC."""
    secs = normalize_timestamp(1_700_000_000)
    millis = normalize_timestamp(1_700_000_000_000)
    assert secs == millis
    assert secs.tzinfo is timezone.utc


def test_timestamp_normalization_iso_and_z():
    assert normalize_timestamp("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_timestamp_rejects_bool():
    with pytest.raises(TypeError):
        normalize_timestamp(True)


def test_pricetick_positive_price():
    with pytest.raises(ValueError):
        PriceTick(symbol="BTC", exchange="x", price=-1.0, timestamp=0)


def test_pricetick_symbol_uppercased():
    tick = PriceTick(symbol="btcusdt", exchange="binance", price=1.0, timestamp=0)
    assert tick.symbol == "BTCUSDT" and tick.exchange == "BINANCE"


def test_ohlcv_rejects_high_below_low():
    with pytest.raises(ValueError):
        OHLCV(symbol="BTC", timestamp=0, open=10, high=8, low=9, close=10)


def test_ohlcv_rejects_close_outside_range():
    with pytest.raises(ValueError):
        OHLCV(symbol="BTC", timestamp=0, open=10, high=11, low=9, close=20)


def test_ohlcv_helpers():
    candle = OHLCV(symbol="BTC", timestamp=0, open=10, high=12, low=8, close=11)
    assert candle.is_bullish
    assert candle.range == 4
    assert candle.typical_price == pytest.approx((12 + 8 + 11) / 3)


def test_orderbook_rejects_unsorted_bids():
    with pytest.raises(ValueError):
        OrderBookSnapshot(
            symbol="BTC",
            exchange="x",
            timestamp=0,
            bids=[{"price": 9, "quantity": 1}, {"price": 10, "quantity": 1}],
            asks=[{"price": 11, "quantity": 1}],
        )


def test_orderbook_rejects_crossed_book():
    with pytest.raises(ValueError):
        OrderBookSnapshot(
            symbol="BTC",
            exchange="x",
            timestamp=0,
            bids=[{"price": 12, "quantity": 1}],
            asks=[{"price": 11, "quantity": 1}],
        )


def test_orderbook_mid_and_spread():
    book = OrderBookSnapshot(
        symbol="BTC",
        exchange="x",
        timestamp=0,
        bids=[{"price": 99, "quantity": 2}],
        asks=[{"price": 101, "quantity": 3}],
    )
    assert book.mid_price == 100
    assert book.spread == 2
    assert book.spread_bps == pytest.approx(200.0)
    assert book.depth("bid") == 2


def test_trading_decision_buy_geometry_enforced():
    """A BUY must satisfy stop < entry < target."""
    with pytest.raises(ValueError):
        TradingDecision(
            symbol="BTC",
            timestamp=0,
            action=Action.BUY,
            confidence=0.5,
            entry=100,
            stop=110,
            target=120,
        )


def test_trading_decision_risk_reward():
    d = TradingDecision(
        symbol="BTC", timestamp=0, action=Action.BUY, confidence=0.5, entry=100, stop=95, target=115
    )
    assert d.risk_reward == pytest.approx(3.0)
    assert d.is_actionable


def test_trading_decision_hold_needs_no_levels():
    d = TradingDecision(symbol="BTC", timestamp=0, action=Action.HOLD, confidence=0.2)
    assert not d.is_actionable
    assert d.risk_reward is None


def test_model_is_frozen():
    tick = PriceTick(symbol="BTC", exchange="x", price=1.0, timestamp=0)
    with pytest.raises(Exception):
        tick.price = 2.0  # type: ignore[misc]


def test_model_json_roundtrip():
    d = TradingDecision(
        symbol="BTC",
        timestamp=0,
        action=Action.SELL,
        confidence=0.5,
        entry=100,
        stop=105,
        target=90,
    )
    assert TradingDecision.from_json(d.to_json()) == d


def test_side_helpers():
    assert Side.BUY.sign == 1 and Side.SELL.sign == -1
    assert Side.BUY.opposite is Side.SELL
