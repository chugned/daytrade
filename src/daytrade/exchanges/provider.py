"""Market-data provider — multi-source orchestration with failover.

Wraps a set of :class:`MarketDataClient` instances and exposes a clean API to
the rest of the pipeline: consensus price, candles and orderbook, each with
graceful failover when an individual source is degraded or down.
"""

from __future__ import annotations

from typing import List, Optional

from ..config.schema import AppConfig
from ..models import (
    OHLCV,
    ConsensusPrice,
    ExchangeStatus,
    OrderBookSnapshot,
    PriceTick,
)
from ..runtime import get_logger
from .base import ExchangeError, MarketDataClient
from .consensus import compute_consensus
from .mock import MockExchange, generate_random_walk
from .public import build_public_client

_log = get_logger("exchanges.provider")


class MarketDataProvider:
    """Aggregates several market-data sources behind one interface."""

    def __init__(self, clients: List[MarketDataClient], config: AppConfig) -> None:
        if not clients:
            raise ValueError("MarketDataProvider needs at least one client")
        self._clients = clients
        self._config = config

    @property
    def sources(self) -> List[str]:
        return [c.name for c in self._clients]

    def get_consensus_price(self, symbol: str) -> ConsensusPrice:
        """Collect ticks from every source and fuse them into a consensus.

        A source that raises is recorded as ``DOWN`` rather than aborting the
        whole read — that is the failover path.
        """
        ticks: List[PriceTick] = []
        for client in self._clients:
            try:
                ticks.append(client.get_ticker(symbol))
            except (ExchangeError, Exception) as exc:  # noqa: BLE001
                _log.warning("source %s ticker failed: %s", client.name, exc)
                ticks.append(self._down_tick(symbol, client.name))
        return compute_consensus(ticks, symbol, self._config.consensus)

    def get_ohlcv(self, symbol: str, limit: int = 200) -> List[OHLCV]:
        """Return candles from the first source that can serve them."""
        last_err: Optional[Exception] = None
        for client in self._clients:
            try:
                candles = client.get_ohlcv(symbol, limit=limit)
                if candles:
                    return candles
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                _log.warning("source %s ohlcv failed: %s", client.name, exc)
        raise ExchangeError(f"no source could provide OHLCV for {symbol}: {last_err}")

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBookSnapshot:
        """Return an orderbook from the first source that can serve one."""
        last_err: Optional[Exception] = None
        for client in self._clients:
            try:
                return client.get_orderbook(symbol, depth=depth)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                _log.warning("source %s orderbook failed: %s", client.name, exc)
        raise ExchangeError(f"no source could provide orderbook for {symbol}: {last_err}")

    @staticmethod
    def _down_tick(symbol: str, exchange: str) -> PriceTick:
        # A placeholder tick flagged DOWN so the consensus engine excludes it.
        # price must be > 0 to satisfy validation; status is what matters.
        from datetime import datetime, timezone

        return PriceTick(
            symbol=symbol,
            exchange=exchange,
            price=1.0,
            timestamp=datetime.now(timezone.utc),
            status=ExchangeStatus.DOWN,
        )


def build_provider(
    config: AppConfig,
    candles: Optional[List[OHLCV]] = None,
) -> MarketDataProvider:
    """Construct a provider from config.

    With ``runtime.allow_network`` false (default) this builds deterministic
    :class:`MockExchange` sources. The mock sources are given small, distinct
    price biases so the consensus engine has realistic dispersion to chew on.
    """
    symbol = config.symbol

    if config.runtime.allow_network:
        clients: List[MarketDataClient] = []
        for name in config.exchanges.sources:
            try:
                clients.append(
                    build_public_client(
                        name,
                        timeout=config.exchanges.timeout_seconds,
                        max_retries=config.exchanges.max_retries,
                        allow_network=True,
                    )
                )
            except ExchangeError as exc:
                _log.warning("skipping public source %s: %s", name, exc)
        if clients:
            return MarketDataProvider(clients, config)
        _log.warning("no public sources available — falling back to mock")

    # Offline / mock path.
    if candles is None:
        candles = generate_random_walk(
            symbol=symbol,
            n_bars=600,
            start_price=100.0,
            drift=0.0,
            volatility=0.004,
            seed=config.runtime.random_seed,
        )
    # Three mock "exchanges" with tiny biases -> realistic consensus dispersion.
    biases = [(-1.5, 3), (0.0, 7), (1.5, 11)]
    mock_clients: List[MarketDataClient] = [
        MockExchange(
            candles,
            name=f"mock_{i}",
            price_bias_bps=bias,
            orderbook_imbalance=0.0,
            spread_bps=4.0,
            depth_seed=seed,
        )
        for i, (bias, seed) in enumerate(biases)
    ]
    return MarketDataProvider(mock_clients, config)
