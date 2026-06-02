"""Market-data domain models: ticks, consensus prices, candles, orderbooks."""

from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

from pydantic import Field, field_validator, model_validator

from ._base import DaytradeModel, normalize_timestamp
from .enums import ExchangeStatus


class PriceTick(DaytradeModel):
    """A single price observation from one exchange."""

    symbol: str
    exchange: str
    price: float = Field(gt=0, description="Last/mid price, quote currency.")
    timestamp: datetime
    volume_24h: float = Field(default=0.0, ge=0)
    status: ExchangeStatus = ExchangeStatus.OK

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: object) -> datetime:
        return normalize_timestamp(v)

    @field_validator("symbol", "exchange", mode="before")
    @classmethod
    def _norm_str(cls, v: object) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("must be a non-empty string")
        return v.strip().upper()


class ConsensusPrice(DaytradeModel):
    """A cross-exchange consensus price with the per-source provenance.

    Produced by the consensus engine after outlier (flash-crash) rejection.
    """

    symbol: str
    price: float = Field(gt=0, description="Consensus (robust mean) price.")
    timestamp: datetime
    sources_used: List[str] = Field(default_factory=list)
    sources_rejected: List[str] = Field(default_factory=list)
    dispersion: float = Field(
        default=0.0,
        ge=0,
        description="Relative spread of accepted source prices (max-min)/median.",
    )
    degraded: bool = Field(
        default=False,
        description="True when too few healthy sources remained.",
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: object) -> datetime:
        return normalize_timestamp(v)

    @property
    def n_sources(self) -> int:
        return len(self.sources_used)


class OHLCV(DaytradeModel):
    """A single OHLCV candle. ``timestamp`` is the candle OPEN time."""

    symbol: str
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(default=0.0, ge=0)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: object) -> datetime:
        return normalize_timestamp(v)

    @model_validator(mode="after")
    def _check_ohlc(self) -> OHLCV:
        hi, lo = self.high, self.low
        if hi < lo:
            raise ValueError(f"high ({hi}) < low ({lo})")
        for name, val in (("open", self.open), ("close", self.close)):
            if not (lo <= val <= hi):
                raise ValueError(f"{name} ({val}) outside [low, high]=[{lo},{hi}]")
        return self

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def typical_price(self) -> float:
        """(H+L+C)/3 — common volatility/indicator input."""
        return (self.high + self.low + self.close) / 3.0


class OrderBookLevel(DaytradeModel):
    """One price level in an orderbook."""

    price: float = Field(gt=0)
    quantity: float = Field(ge=0)

    @property
    def notional(self) -> float:
        return self.price * self.quantity


class OrderBookSnapshot(DaytradeModel):
    """A point-in-time L2 orderbook snapshot.

    Bids are stored descending (best/highest first); asks ascending
    (best/lowest first). Validators enforce this so microstructure code can
    trust ``bids[0]`` / ``asks[0]`` is always the top of book.
    """

    symbol: str
    exchange: str
    timestamp: datetime
    bids: List[OrderBookLevel] = Field(default_factory=list)
    asks: List[OrderBookLevel] = Field(default_factory=list)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: object) -> datetime:
        return normalize_timestamp(v)

    @model_validator(mode="after")
    def _check_sorted_and_crossed(self) -> OrderBookSnapshot:
        bid_prices = [lvl.price for lvl in self.bids]
        ask_prices = [lvl.price for lvl in self.asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("bids must be sorted descending by price")
        if ask_prices != sorted(ask_prices):
            raise ValueError("asks must be sorted ascending by price")
        if bid_prices and ask_prices and bid_prices[0] >= ask_prices[0]:
            raise ValueError("crossed book: best bid >= best ask")
        return self

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float | None:
        """Spread in basis points of the mid price."""
        mid, sp = self.mid_price, self.spread
        if mid is None or sp is None or mid == 0:
            return None
        return 10_000.0 * sp / mid

    def depth(self, side: str, levels: int | None = None) -> float:
        """Total quantity available on ``side`` ('bid'|'ask'), top ``levels``."""
        book = self.bids if side == "bid" else self.asks
        chosen = book if levels is None else book[:levels]
        return sum(lvl.quantity for lvl in chosen)

    def notional_depth(self, side: str, levels: int | None = None) -> float:
        book = self.bids if side == "bid" else self.asks
        chosen = book if levels is None else book[:levels]
        return sum(lvl.notional for lvl in chosen)

    def top_levels(self, levels: int = 5) -> Tuple[List[OrderBookLevel], List[OrderBookLevel]]:
        return self.bids[:levels], self.asks[:levels]
