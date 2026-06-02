"""Enumerations shared across the domain model.

Using enums (rather than bare strings) keeps the pipeline self-documenting and
lets validation reject nonsense values at the boundary.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """A str-backed enum — JSON-serializes as its plain string value."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class Side(StrEnum):
    """Direction of an order or an open position."""

    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        """+1 for BUY, -1 for SELL — handy for PnL math."""
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class Bias(StrEnum):
    """Directional opinion emitted by an analysis layer (signal)."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

    @property
    def sign(self) -> int:
        return {"bullish": 1, "bearish": -1, "neutral": 0}[self.value]


class Action(StrEnum):
    """Final actionable output of the fusion engine."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class RiskLevel(StrEnum):
    """Coarse risk classification used by macro analysis and the risk engine."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "extreme": 3}[self.value]


class MarketRegime(StrEnum):
    """Microstructure / volatility regime classification."""

    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    CHOP = "chop"
    VOLATILE = "volatile"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ExchangeStatus(StrEnum):
    """Health of an upstream market-data source."""

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class ModelKind(StrEnum):
    """Supported ML estimator families."""

    LOGISTIC_REGRESSION = "logistic_regression"
    RANDOM_FOREST = "random_forest"
    GRADIENT_BOOSTING = "gradient_boosting"
