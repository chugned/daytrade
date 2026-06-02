"""Real Binance market-data feed tests.

These are hermetic — the HTTP layer is mocked, so no network is touched.
They verify parsing, caching and the feed-selection logic.
"""

from __future__ import annotations

from datetime import datetime, timezone

from daytrade.config import load_config
from daytrade.observatory.real_feed import RealMarketFeed, _minute_ms, build_feed

_T = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def _kline(open_ms, o, h, l, c, v):
    return [
        open_ms,
        str(o),
        str(h),
        str(l),
        str(c),
        str(v),
        open_ms + 59_999,
        "0",
        10,
        "0",
        "0",
        "0",
    ]


class _MockFeed(RealMarketFeed):
    """RealMarketFeed with a canned, call-counting HTTP layer."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def _get(self, path, params):
        self.calls.append((path, dict(params)))
        if path == "/api/v3/klines":
            limit = params.get("limit", 1)
            if limit == 1:  # price_at single-minute fetch
                start = params["startTime"]
                return [_kline(start, 100, 105, 98, 101.5, 9)]
            base = int(_T.timestamp() // 60) * 60_000
            return [
                _kline(base + i * 60_000, 100 + i, 106 + i, 97 + i, 101 + i, 5 + i)
                for i in range(limit)
            ]
        if path == "/api/v3/depth":
            return {
                "bids": [["100.0", "2.0"], ["99.5", "3.0"]],
                "asks": [["100.5", "1.5"], ["101.0", "4.0"]],
            }
        if path == "/api/v3/ticker/24hr":
            return {"symbol": params["symbol"], "lastPrice": "100.25", "quoteVolume": "123456789.0"}
        raise AssertionError(f"unexpected path {path}")


def test_minute_ms_floors_to_minute():
    ms = _minute_ms(datetime(2026, 1, 1, 0, 3, 45, tzinfo=timezone.utc))
    assert ms % 60_000 == 0
    assert _minute_ms(datetime(2026, 1, 1, 0, 3, 0, tzinfo=timezone.utc)) == ms


def test_candles_at_parses_klines():
    feed = _MockFeed()
    candles = feed.candles_at("BTCUSDT", _T, n_bars=20)
    assert len(candles) == 20
    c = candles[0]
    assert c.symbol == "BTCUSDT"
    assert c.high >= c.low and c.low <= c.close <= c.high


def test_candles_at_uses_ttl_cache():
    feed = _MockFeed()
    feed.candles_at("BTCUSDT", _T, n_bars=20)
    n = len(feed.calls)
    feed.candles_at("BTCUSDT", _T, n_bars=20)  # within TTL -> no new call
    assert len(feed.calls) == n


def test_price_at_returns_close_and_caches():
    feed = _MockFeed()
    px = feed.price_at("ETHUSDT", _T)
    assert px == 101.5
    n = len(feed.calls)
    again = feed.price_at("ETHUSDT", _T)  # same minute -> cached
    assert again == 101.5 and len(feed.calls) == n


def test_orderbook_at_is_uncrossed():
    book = _MockFeed().orderbook_at("BTCUSDT", _T)
    assert book.best_bid < book.best_ask
    assert book.depth("bid") > 0 and book.depth("ask") > 0


def test_tick_at_parses_ticker():
    tick = _MockFeed().tick_at("BTCUSDT", _T)
    assert tick.price == 100.25
    assert tick.volume_24h == 123456789.0
    assert tick.exchange.upper() == "BINANCE"


def test_real_feed_source_label():
    assert RealMarketFeed.source == "real"


# --- feed selection --------------------------------------------------------


def test_build_feed_offline_is_simulator():
    cfg = load_config(load_dotenv_file=False)
    assert cfg.runtime.allow_network is False
    from daytrade.observatory.feed import LiveMockFeed

    assert isinstance(build_feed(cfg), LiveMockFeed)


def test_build_feed_online_is_real(monkeypatch):
    monkeypatch.setenv("DAYTRADE_ALLOW_NETWORK", "true")
    cfg = load_config(load_dotenv_file=False)
    assert cfg.runtime.allow_network is True
    assert isinstance(build_feed(cfg), RealMarketFeed)
