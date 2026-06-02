"""Public, read-only market-data clients for real exchanges.

These hit ONLY public market-data endpoints — no auth, no keys, no orders.
They are disabled unless ``runtime.allow_network`` is true, and every call is
wrapped in retries/timeouts so a degraded API downgrades gracefully to a
``DOWN`` status rather than crashing the pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..models import OHLCV, ExchangeStatus, OrderBookSnapshot, PriceTick
from ..models.market import OrderBookLevel
from ..runtime import get_logger
from .base import ExchangeError, MarketDataClient

_log = get_logger("exchanges.public")


class _HttpClient(MarketDataClient):
    """Shared HTTP plumbing: timeouts, retries, network gating."""

    base_url: str = ""

    def __init__(
        self, timeout: float = 5.0, max_retries: int = 3, allow_network: bool = False
    ) -> None:
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._allow_network = allow_network

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        if not self._allow_network:
            raise ExchangeError(f"{self.name}: network disabled (set runtime.allow_network=true)")

        @retry(
            retry=retry_if_exception_type((httpx.HTTPError,)),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=0.5, max=4.0),
            reraise=True,
        )
        def _do() -> Any:
            url = self.base_url + path
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()

        try:
            return _do()
        except httpx.HTTPError as exc:
            raise ExchangeError(f"{self.name}: request failed: {exc}") from exc


class BinanceClient(_HttpClient):
    """Binance public spot market data."""

    name = "binance"
    base_url = "https://api.binance.com"

    def get_ticker(self, symbol: str) -> PriceTick:
        data = self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        return PriceTick(
            symbol=symbol,
            exchange=self.name,
            price=float(data["lastPrice"]),
            timestamp=datetime.now(timezone.utc),
            volume_24h=float(data.get("quoteVolume", 0.0)),
            status=ExchangeStatus.OK,
        )

    def get_ohlcv(self, symbol: str, limit: int = 200) -> List[OHLCV]:
        rows = self._get("/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": limit})
        return [
            OHLCV(
                symbol=symbol,
                timestamp=int(r[0]),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in rows
        ]

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBookSnapshot:
        data = self._get("/api/v3/depth", {"symbol": symbol, "limit": depth})
        return _book_from_pairs(symbol, self.name, data["bids"], data["asks"])


class BybitClient(_HttpClient):
    """Bybit v5 public spot market data."""

    name = "bybit"
    base_url = "https://api.bybit.com"

    def get_ticker(self, symbol: str) -> PriceTick:
        data = self._get("/v5/market/tickers", {"category": "spot", "symbol": symbol})
        row = data["result"]["list"][0]
        return PriceTick(
            symbol=symbol,
            exchange=self.name,
            price=float(row["lastPrice"]),
            timestamp=datetime.now(timezone.utc),
            volume_24h=float(row.get("turnover24h", 0.0)),
            status=ExchangeStatus.OK,
        )

    def get_ohlcv(self, symbol: str, limit: int = 200) -> List[OHLCV]:
        data = self._get(
            "/v5/market/kline",
            {"category": "spot", "symbol": symbol, "interval": "1", "limit": limit},
        )
        rows = data["result"]["list"]
        # Bybit returns newest-first; reverse to oldest-first.
        return [
            OHLCV(
                symbol=symbol,
                timestamp=int(r[0]),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in reversed(rows)
        ]

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBookSnapshot:
        data = self._get(
            "/v5/market/orderbook", {"category": "spot", "symbol": symbol, "limit": depth}
        )
        res = data["result"]
        return _book_from_pairs(symbol, self.name, res["b"], res["a"])


class CoinGeckoClient(_HttpClient):
    """CoinGecko public price data (ticker only — no L2 orderbook API)."""

    name = "coingecko"
    base_url = "https://api.coingecko.com"
    # Minimal symbol -> CoinGecko id map; extend as needed.
    _IDS = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana"}

    def _coin_id(self, symbol: str) -> str:
        cid = self._IDS.get(symbol.upper())
        if cid is None:
            raise ExchangeError(f"coingecko: unknown symbol mapping for {symbol}")
        return cid

    def get_ticker(self, symbol: str) -> PriceTick:
        cid = self._coin_id(symbol)
        data = self._get(
            "/api/v3/simple/price", {"ids": cid, "vs_currencies": "usd", "include_24hr_vol": "true"}
        )
        node = data[cid]
        return PriceTick(
            symbol=symbol,
            exchange=self.name,
            price=float(node["usd"]),
            timestamp=datetime.now(timezone.utc),
            volume_24h=float(node.get("usd_24h_vol", 0.0)),
            status=ExchangeStatus.OK,
        )

    def get_ohlcv(self, symbol: str, limit: int = 200) -> List[OHLCV]:
        raise ExchangeError("coingecko: OHLCV not supported by this client")

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBookSnapshot:
        raise ExchangeError("coingecko: orderbook not supported (no public L2 API)")


class KrakenClient(_HttpClient):
    """Kraken public spot market data."""

    name = "kraken"
    base_url = "https://api.kraken.com"
    # Kraken uses XBT for Bitcoin and its own pair codes.
    _PAIRS = {"BTCUSDT": "XBTUSDT", "ETHUSDT": "ETHUSDT", "SOLUSDT": "SOLUSDT"}

    def _pair(self, symbol: str) -> str:
        return self._PAIRS.get(symbol.upper(), symbol.upper())

    def _result(self, data: dict) -> dict:
        """Unwrap a Kraken response, surfacing any API-level error."""
        errors = data.get("error") or []
        if errors:
            raise ExchangeError(f"kraken: {errors}")
        return data["result"]

    def get_ticker(self, symbol: str) -> PriceTick:
        data = self._get("/0/public/Ticker", {"pair": self._pair(symbol)})
        row = next(iter(self._result(data).values()))
        price = float(row["c"][0])  # last trade price
        base_volume_24h = float(row["v"][1])  # 24h volume in base units
        return PriceTick(
            symbol=symbol,
            exchange=self.name,
            price=price,
            timestamp=datetime.now(timezone.utc),
            volume_24h=base_volume_24h * price,
            status=ExchangeStatus.OK,
        )

    def get_ohlcv(self, symbol: str, limit: int = 200) -> List[OHLCV]:
        data = self._get("/0/public/OHLC", {"pair": self._pair(symbol), "interval": 1})
        result = self._result(data)
        rows = next(v for k, v in result.items() if k != "last")
        # Each row: [time, open, high, low, close, vwap, volume, count]
        return [
            OHLCV(
                symbol=symbol,
                timestamp=int(r[0]),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[6]),
            )
            for r in rows[-limit:]
        ]

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBookSnapshot:
        data = self._get("/0/public/Depth", {"pair": self._pair(symbol), "count": depth})
        book = next(iter(self._result(data).values()))
        # Kraken levels are [price, volume, timestamp] — keep price/volume.
        bids = [(lvl[0], lvl[1]) for lvl in book["bids"]]
        asks = [(lvl[0], lvl[1]) for lvl in book["asks"]]
        return _book_from_pairs(symbol, self.name, bids, asks)


def _book_from_pairs(
    symbol: str, exchange: str, raw_bids: List[List[str]], raw_asks: List[List[str]]
) -> OrderBookSnapshot:
    bids = [OrderBookLevel(price=float(p), quantity=float(q)) for p, q in raw_bids]
    asks = [OrderBookLevel(price=float(p), quantity=float(q)) for p, q in raw_asks]
    bids.sort(key=lambda lvl: lvl.price, reverse=True)
    asks.sort(key=lambda lvl: lvl.price)
    return OrderBookSnapshot(
        symbol=symbol,
        exchange=exchange,
        timestamp=datetime.now(timezone.utc),
        bids=bids,
        asks=asks,
    )


_REGISTRY = {
    "binance": BinanceClient,
    "bybit": BybitClient,
    "kraken": KrakenClient,
    "coingecko": CoinGeckoClient,
}


def build_public_client(
    name: str, timeout: float = 5.0, max_retries: int = 3, allow_network: bool = False
) -> MarketDataClient:
    """Factory: construct a public client by name."""
    cls = _REGISTRY.get(name.lower())
    if cls is None:
        raise ExchangeError(f"unknown public exchange: {name}")
    return cls(timeout=timeout, max_retries=max_retries, allow_network=allow_network)
