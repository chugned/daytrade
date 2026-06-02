"""Exchange infrastructure: mock data, consensus, outlier rejection, failover."""

from __future__ import annotations

import pytest

from daytrade.config import load_config
from daytrade.exchanges import (
    MockExchange,
    build_provider,
    compute_consensus,
    generate_random_walk,
)
from daytrade.exchanges.mock import build_orderbook
from daytrade.models import ExchangeStatus, PriceTick


def test_random_walk_is_deterministic():
    a = generate_random_walk("BTC", n_bars=200, seed=42)
    b = generate_random_walk("BTC", n_bars=200, seed=42)
    assert [c.close for c in a] == [c.close for c in b]


def test_random_walk_respects_count():
    assert len(generate_random_walk("BTC", n_bars=123, seed=1)) == 123


def test_mock_exchange_ticker_and_ohlcv():
    candles = generate_random_walk("BTCUSDT", n_bars=100, seed=1)
    ex = MockExchange(candles)
    tick = ex.get_ticker("BTCUSDT")
    assert tick.price > 0
    assert len(ex.get_ohlcv("BTCUSDT", limit=50)) == 50


def test_mock_exchange_orderbook_uncrossed():
    candles = generate_random_walk("BTCUSDT", n_bars=50, seed=1)
    book = MockExchange(candles).get_orderbook("BTCUSDT", depth=10)
    assert book.best_bid < book.best_ask


def test_consensus_averages_clean_sources():
    ticks = [
        PriceTick(symbol="BTC", exchange=e, price=p, timestamp=0)
        for e, p in [("a", 100.0), ("b", 102.0), ("c", 101.0)]
    ]
    cp = compute_consensus(ticks, "BTC")
    assert cp.price == pytest.approx(101.0)
    assert cp.n_sources == 3


def test_consensus_rejects_flash_crash_outlier():
    """A wildly divergent price is rejected, not averaged in."""
    ticks = [
        PriceTick(symbol="BTC", exchange=e, price=p, timestamp=0)
        for e, p in [("a", 100.0), ("b", 101.0), ("c", 100.5), ("d", 10.0)]
    ]
    cp = compute_consensus(ticks, "BTC")
    assert "D" in cp.sources_rejected
    assert cp.price == pytest.approx(100.5)  # mean of the 3 clean prints


def test_consensus_excludes_down_sources():
    ticks = [
        PriceTick(symbol="BTC", exchange="a", price=100.0, timestamp=0),
        PriceTick(symbol="BTC", exchange="b", price=100.0, timestamp=0),
        PriceTick(symbol="BTC", exchange="c", price=999.0, timestamp=0, status=ExchangeStatus.DOWN),
    ]
    cp = compute_consensus(ticks, "BTC")
    assert "C" in cp.sources_rejected
    assert cp.degraded is True


def test_consensus_all_down_raises():
    ticks = [
        PriceTick(symbol="BTC", exchange="a", price=1.0, timestamp=0, status=ExchangeStatus.DOWN)
    ]
    with pytest.raises(ValueError):
        compute_consensus(ticks, "BTC")


def test_orderbook_imbalance_direction():
    """Negative imbalance => ask depth exceeds bid depth."""
    book = build_orderbook("BTC", 100.0, imbalance=-0.2, jitter=0.0)
    assert book.depth("ask") > book.depth("bid")


def test_provider_offline_consensus():
    cfg = load_config(load_dotenv_file=False)
    provider = build_provider(cfg)
    cp = provider.get_consensus_price("BTCUSDT")
    assert cp.price > 0
    assert len(provider.sources) >= 1
