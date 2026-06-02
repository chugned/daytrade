"""Portfolio, position and fill models for the paper-trading engine."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from pydantic import Field, field_validator

from ._base import DaytradeModel, normalize_timestamp
from .enums import Side


class Fill(DaytradeModel):
    """A single simulated execution. There is no such thing as a real fill here."""

    order_id: str
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    price: float = Field(gt=0, description="Effective fill price AFTER slippage.")
    requested_price: float = Field(gt=0, description="Price before slippage.")
    fee: float = Field(ge=0)
    slippage: float = Field(ge=0, description="Absolute adverse price move modeled on this fill.")
    timestamp: datetime
    is_partial: bool = False

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: object) -> datetime:
        return normalize_timestamp(v)

    @property
    def notional(self) -> float:
        return self.price * self.quantity

    @property
    def cash_delta(self) -> float:
        """Signed cash impact: buys cost cash (+fee), sells return cash (-fee)."""
        gross = self.notional
        return (-gross - self.fee) if self.side is Side.BUY else (gross - self.fee)


class Position(DaytradeModel):
    """An open spot position. Long-only, no leverage, no shorting on margin."""

    symbol: str
    quantity: float = Field(ge=0, description="Units held (0 == flat).")
    avg_entry_price: float = Field(ge=0)
    realized_pnl: float = 0.0

    def unrealized_pnl(self, mark_price: float) -> float:
        if self.quantity == 0:
            return 0.0
        return (mark_price - self.avg_entry_price) * self.quantity

    def market_value(self, mark_price: float) -> float:
        return self.quantity * mark_price

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0


class PortfolioSnapshot(DaytradeModel):
    """An immutable point-in-time view of the paper portfolio."""

    timestamp: datetime
    cash: float
    positions: Dict[str, Position] = Field(default_factory=dict)
    mark_prices: Dict[str, float] = Field(default_factory=dict)
    realized_pnl: float = 0.0

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: object) -> datetime:
        return normalize_timestamp(v)

    @property
    def positions_value(self) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            total += pos.market_value(self.mark_prices.get(sym, pos.avg_entry_price))
        return total

    @property
    def equity(self) -> float:
        """Total account value = cash + marked-to-market positions."""
        return self.cash + self.positions_value

    @property
    def unrealized_pnl(self) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            total += pos.unrealized_pnl(self.mark_prices.get(sym, pos.avg_entry_price))
        return total

    @property
    def open_symbols(self) -> List[str]:
        return [s for s, p in self.positions.items() if not p.is_flat]
