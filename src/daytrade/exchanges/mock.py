"""Deterministic mock exchange.

The mock exchange is the default data source. Given a fixed seed it produces
the *exact same* candles, ticks and orderbooks every run — a precondition for
reproducible research and stable tests. It never touches the network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import numpy as np

from ..models import OHLCV, ExchangeStatus, OrderBookSnapshot, PriceTick
from ..models.market import OrderBookLevel
from .base import MarketDataClient

_BAR = timedelta(minutes=1)


def generate_random_walk(
    symbol: str,
    n_bars: int = 500,
    start_price: float = 100.0,
    drift: float = 0.0,
    volatility: float = 0.004,
    seed: int = 42,
    end_time: Optional[datetime] = None,
) -> List[OHLCV]:
    """Generate a deterministic OHLCV series via a seeded log-return walk.

    Args:
        drift: mean log-return per bar.
        volatility: std-dev of log-return per bar.

    The series is built so each candle's OHLC is internally consistent
    (low <= open/close <= high), which the ``OHLCV`` validator also enforces.
    """
    if n_bars < 1:
        raise ValueError("n_bars must be >= 1")
    rng = np.random.default_rng(seed)
    end_time = end_time or datetime(2026, 1, 1, tzinfo=timezone.utc)
    start_time = end_time - _BAR * (n_bars - 1)

    log_returns = rng.normal(loc=drift, scale=volatility, size=n_bars)
    closes = start_price * np.exp(np.cumsum(log_returns))

    candles: List[OHLCV] = []
    prev_close = start_price
    for i in range(n_bars):
        close = float(closes[i])
        open_ = prev_close
        # Intrabar wick noise proportional to volatility.
        wick = abs(rng.normal(0.0, volatility)) * max(open_, close)
        high = max(open_, close) + wick
        low = min(open_, close) - wick
        low = max(low, 1e-9)
        volume = float(abs(rng.normal(1000.0, 250.0)))
        candles.append(
            OHLCV(
                symbol=symbol,
                timestamp=start_time + _BAR * i,
                open=round(open_, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=round(volume, 4),
            )
        )
        prev_close = close
    return candles


def build_orderbook(
    symbol: str,
    mid_price: float,
    exchange: str = "mock",
    depth: int = 20,
    spread_bps: float = 4.0,
    base_quantity: float = 1.0,
    imbalance: float = 0.0,
    timestamp: Optional[datetime] = None,
    seed: int = 0,
    jitter: float = 0.1,
) -> OrderBookSnapshot:
    """Construct a deterministic L2 orderbook around ``mid_price``.

    Args:
        imbalance: in [-1, 1]. Positive => more bid depth (buy pressure),
            negative => more ask depth (sell pressure). E.g. ``-0.13`` ~ "30%
            more sellers".
        spread_bps: bid/ask spread in basis points of the mid price.
        jitter: per-level relative size noise; set 0 for an exact, smooth book.
    """
    if not -1.0 <= imbalance <= 1.0:
        raise ValueError("imbalance must be in [-1, 1]")
    rng = np.random.default_rng(seed)
    timestamp = timestamp or datetime(2026, 1, 1, tzinfo=timezone.utc)

    half_spread = mid_price * (spread_bps / 2.0) / 10_000.0
    # Level spacing scales with price — no fixed floor, so low-priced assets
    # (e.g. an alt at $0.40) get a sensible book rather than a crossed one.
    tick = mid_price * 0.0001
    # Round prices to enough significant digits for the asset's magnitude.
    price_dp = 2 if mid_price >= 100 else (4 if mid_price >= 1 else 8)

    # Convert a symmetric imbalance into per-side size multipliers.
    bid_mult = 1.0 + imbalance
    ask_mult = 1.0 - imbalance

    bids: List[OrderBookLevel] = []
    asks: List[OrderBookLevel] = []
    for i in range(depth):
        # Depth decays away from top of book; small deterministic jitter.
        decay = float(np.exp(-0.15 * i))
        jitter_b = 1.0 + jitter * float(rng.standard_normal())
        jitter_a = 1.0 + jitter * float(rng.standard_normal())
        bid_price = mid_price - half_spread - tick * i
        ask_price = mid_price + half_spread + tick * i
        bid_qty = max(base_quantity * decay * bid_mult * jitter_b, 1e-6)
        ask_qty = max(base_quantity * decay * ask_mult * jitter_a, 1e-6)
        bids.append(OrderBookLevel(price=round(bid_price, price_dp), quantity=round(bid_qty, 6)))
        asks.append(OrderBookLevel(price=round(ask_price, price_dp), quantity=round(ask_qty, 6)))

    return OrderBookSnapshot(
        symbol=symbol,
        exchange=exchange,
        timestamp=timestamp,
        bids=bids,
        asks=asks,
    )


class MockExchange(MarketDataClient):
    """A read-only mock market-data source backed by a fixed candle series.

    Multiple ``MockExchange`` instances with different ``price_bias`` values
    feed the consensus engine, simulating cross-exchange price dispersion.
    """

    def __init__(
        self,
        candles: List[OHLCV],
        name: str = "mock",
        price_bias_bps: float = 0.0,
        orderbook_imbalance: float = 0.0,
        spread_bps: float = 4.0,
        status: ExchangeStatus = ExchangeStatus.OK,
        depth_seed: int = 0,
    ) -> None:
        if not candles:
            raise ValueError("MockExchange requires at least one candle")
        self.name = name
        self._candles = list(candles)
        self._price_bias_bps = price_bias_bps
        self._imbalance = orderbook_imbalance
        self._spread_bps = spread_bps
        self._status = status
        self._depth_seed = depth_seed

    @property
    def symbol(self) -> str:
        return self._candles[-1].symbol

    def _biased(self, price: float) -> float:
        return price * (1.0 + self._price_bias_bps / 10_000.0)

    def get_ticker(self, symbol: str) -> PriceTick:
        last = self._candles[-1]
        return PriceTick(
            symbol=symbol,
            exchange=self.name,
            price=round(self._biased(last.close), 2),
            timestamp=last.timestamp,
            volume_24h=sum(c.volume for c in self._candles[-1440:]),
            status=self._status,
        )

    def get_ohlcv(self, symbol: str, limit: int = 200) -> List[OHLCV]:
        return self._candles[-limit:]

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBookSnapshot:
        last = self._candles[-1]
        return build_orderbook(
            symbol=symbol,
            mid_price=self._biased(last.close),
            exchange=self.name,
            depth=depth,
            spread_bps=self._spread_bps,
            base_quantity=1.0,
            imbalance=self._imbalance,
            timestamp=last.timestamp,
            seed=self._depth_seed,
        )
