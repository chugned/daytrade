"""Real market-data feed — live Binance public data.

``RealMarketFeed`` is the production counterpart to :class:`LiveMockFeed`.
It serves the observatory the *actual* market: real 1-minute candles, real
orderbooks and real tickers from Binance's public, read-only market-data
endpoints.

SAFETY: this uses only public market-data endpoints — no API key, no auth,
no orders. The base host is ``data-api.binance.vision`` (Binance's public
data mirror). Nothing here can place a trade or move money.

Prediction-vs-reality stays honest: a prediction made at time T is evaluated
later by fetching the *real historical candle* at T+horizon — only once that
time has actually passed. Closed candles never change, so they are cached
permanently; recent candles are cached briefly.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..exchanges.base import ExchangeError
from ..models import OHLCV, OrderBookSnapshot, PriceTick
from ..models.market import OrderBookLevel
from ..runtime import get_logger

_log = get_logger("observatory.real_feed")

# Binance public market-data mirror — no geo-block, no auth, read-only.
_BASE_URL = "https://data-api.binance.vision"
_RECENT_TTL = 25.0  # seconds to trust a cached "recent candles" fetch


def _minute_ms(when: datetime) -> int:
    """Epoch milliseconds of the start of the minute containing ``when``."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (int(when.timestamp()) // 60) * 60_000


class RealMarketFeed:
    """Live Binance public-data feed (same interface as ``LiveMockFeed``)."""

    source = "real"

    #: QA-CRIT-2: Cap the minute-close cache so it cannot grow without
    #: bound. At ~35 symbols × 1440 minutes/day this would otherwise add
    #: ~50k entries/day forever and is the analogue of the nighttrade
    #: 4.66 GB process-RSS leak. 50k entries ≈ 5 MB of dict, comfortably
    #: covers a full day of minute closes across the watchlist plus
    #: prediction-evaluation horizons.
    _MINUTE_CLOSE_CACHE_MAX = 50_000

    def __init__(self, timeout: float = 10.0, max_retries: int = 3) -> None:
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        # (symbol, minute_ms) -> close price. Closed candles never change,
        # so the *value* is immutable per key; we evict by FIFO on size.
        self._minute_close: OrderedDict[Tuple[str, int], float] = OrderedDict()
        # symbol -> (fetched_at_epoch, candles) short-TTL cache.
        self._recent: Dict[str, Tuple[float, List[OHLCV]]] = {}
        # QA-HIGH-5: one long-lived httpx.Client reuses the TLS
        # session + connection pool across the ~100 calls per cycle.
        # Previously a fresh Client per `_get` meant a fresh TLS
        # handshake per call (~50-150ms each), dominating cycle time.
        # httpx.Client is documented thread-safe — safe to call from
        # the prefetch worker pool the observer can spin up per cycle.
        self._client = httpx.Client(timeout=self._timeout)
        # SPEED: protect the shared dict caches when the observer
        # prefetches symbols in parallel. Each symbol's _recent slot
        # is owned by one thread, but _minute_close gets writes from
        # multiple threads + has eviction (popitem) that's not atomic
        # under concurrent mutation.
        self._cache_lock = threading.RLock()

    def close(self) -> None:
        """Release the underlying HTTP pool. Idempotent."""
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    def __del__(self) -> None:  # best-effort
        self.close()

    def _cache_minute_close(self, key: Tuple[str, int], price: float) -> None:
        """Insert with FIFO eviction when over the cap. O(1).
        Thread-safe via _cache_lock — callable from the observer's
        parallel prefetch pool."""
        with self._cache_lock:
            cache = self._minute_close
            if key in cache:
                cache.move_to_end(key)  # mark as recent
            cache[key] = price
            while len(cache) > self._MINUTE_CLOSE_CACHE_MAX:
                cache.popitem(last=False)  # evict oldest

    # -- HTTP ----------------------------------------------------------------

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        @retry(
            retry=retry_if_exception_type((httpx.HTTPError,)),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=0.5, max=5.0),
            reraise=True,
        )
        def _do() -> Any:
            resp = self._client.get(_BASE_URL + path, params=params)
            resp.raise_for_status()
            return resp.json()

        try:
            return _do()
        except httpx.HTTPError as exc:
            raise ExchangeError(f"binance public data request failed: {exc}") from exc

    @staticmethod
    def _kline_to_ohlcv(symbol: str, k: list) -> OHLCV:
        # Binance kline: [openTime, open, high, low, close, volume, closeTime, ...]
        return OHLCV(
            symbol=symbol,
            timestamp=int(k[0]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        )

    # -- feed interface ------------------------------------------------------

    def candles_at(self, symbol: str, as_of: datetime, n_bars: int = 240) -> List[OHLCV]:
        """The ``n_bars`` real 1-minute candles ending at/just before ``as_of``."""
        cached = self._recent.get(symbol)
        if cached and (time.time() - cached[0]) < _RECENT_TTL and len(cached[1]) >= n_bars:
            return cached[1][-n_bars:]

        params: Dict[str, Any] = {
            "symbol": symbol,
            "interval": "1m",
            "limit": min(1000, max(n_bars, 1)),
        }
        params["endTime"] = int(as_of.timestamp() * 1000)
        rows = self._get("/api/v3/klines", params)
        if not rows:
            raise ExchangeError(f"no klines returned for {symbol}")
        candles = [self._kline_to_ohlcv(symbol, k) for k in rows]
        self._recent[symbol] = (time.time(), candles)
        # Opportunistically warm the minute-close cache for free.
        for k in rows:
            self._cache_minute_close((symbol, int(k[0])), float(k[4]))
        return candles[-n_bars:]

    def price_at(self, symbol: str, when: datetime) -> float:
        """The real close price of the 1-minute candle containing ``when``.

        Used to evaluate matured predictions against what actually happened.
        """
        key = (symbol, _minute_ms(when))
        if key in self._minute_close:
            return self._minute_close[key]
        rows = self._get(
            "/api/v3/klines", {"symbol": symbol, "interval": "1m", "startTime": key[1], "limit": 1}
        )
        if not rows:
            raise ExchangeError(f"no kline for {symbol} at {when.isoformat()}")
        close = float(rows[0][4])
        self._cache_minute_close(key, close)
        return close

    def orderbook_at(self, symbol: str, as_of: datetime) -> OrderBookSnapshot:
        """A real L2 orderbook snapshot for ``symbol``.

        Fetches 100 levels/side: enough depth for a representative liquidity
        reading (the top 20 alone undercounts notional on real books).
        """
        data = self._get("/api/v3/depth", {"symbol": symbol, "limit": 100})
        bids = [
            OrderBookLevel(price=float(p), quantity=float(q))
            for p, q in data.get("bids", [])
            if float(q) > 0
        ]
        asks = [
            OrderBookLevel(price=float(p), quantity=float(q))
            for p, q in data.get("asks", [])
            if float(q) > 0
        ]
        bids.sort(key=lambda lvl: lvl.price, reverse=True)
        asks.sort(key=lambda lvl: lvl.price)
        return OrderBookSnapshot(
            symbol=symbol,
            exchange="binance",
            timestamp=datetime.now(timezone.utc),
            bids=bids,
            asks=asks,
        )

    def tick_at(self, symbol: str, as_of: datetime) -> PriceTick:
        """A real 24h ticker for ``symbol``."""
        data = self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        return PriceTick(
            symbol=symbol,
            exchange="binance",
            price=float(data["lastPrice"]),
            timestamp=datetime.now(timezone.utc),
            volume_24h=float(data.get("quoteVolume", 0.0)),
        )


def build_feed(config):
    """Pick the market feed for the observatory based on config.

    ``runtime.allow_network: true`` -> live Binance public data.
    Otherwise -> the deterministic offline simulator.
    """
    from .feed import LiveMockFeed

    if config.runtime.allow_network:
        _log.info("market feed: REAL Binance public data (read-only)")
        return RealMarketFeed()
    _log.info("market feed: deterministic offline simulator")
    return LiveMockFeed()
