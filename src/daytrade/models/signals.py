"""Analysis-layer signal models.

Every analysis layer (technical, microstructure, macro, ML) emits a signal
with a common shape — a directional ``bias``, a ``score`` in [-1, 1] and a
``confidence`` in [0, 1] — so the fusion engine can treat them uniformly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from pydantic import Field, field_validator

from ._base import DaytradeModel, normalize_timestamp
from .enums import Bias, MarketRegime, RiskLevel


class _SignalBase(DaytradeModel):
    """Common fields for all analysis signals."""

    symbol: str
    timestamp: datetime
    bias: Bias = Bias.NEUTRAL
    score: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Directional strength: -1 fully bearish .. +1 fully bullish.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="How much trust to place in this signal.",
    )
    reasoning: List[str] = Field(
        default_factory=list,
        description="Human-readable bullet points explaining the signal.",
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: object) -> datetime:
        return normalize_timestamp(v)


class TechnicalSignal(_SignalBase):
    """Output of the technical-indicator engine."""

    rsi: float | None = Field(default=None, ge=0, le=100)
    ema_fast: float | None = None
    ema_slow: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    volatility: float | None = Field(default=None, ge=0)
    momentum: float | None = None
    trend_slope: float | None = None
    indicators: Dict[str, float] = Field(
        default_factory=dict,
        description="Any additional named indicator values.",
    )


class MicrostructureSignal(_SignalBase):
    """Output of the orderbook / microstructure analysis layer."""

    imbalance: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="(bid-ask depth) / (bid+ask depth): + means buy pressure.",
    )
    spread_bps: float | None = Field(default=None, ge=0)
    regime: MarketRegime = MarketRegime.RANGE
    thin_liquidity: bool = False
    chop_zone: bool = False
    support: float | None = Field(default=None, gt=0)
    resistance: float | None = Field(default=None, gt=0)
    liquidity_walls: List[float] = Field(default_factory=list)
    liquidity_interpretation: str = ""


class MacroSignal(_SignalBase):
    """Output of the macro AI context engine."""

    risk_level: RiskLevel = RiskLevel.MEDIUM
    regime_label: str = Field(
        default="neutral",
        description="e.g. risk_on, panic, institutional_buying, exchange_collapse.",
    )
    source: str = Field(
        default="mock",
        description="'mock' deterministic analyzer or 'gemini'.",
    )
    headline: str = ""


class MLSignal(_SignalBase):
    """Output of the ML prediction layer.

    ``score`` is the *intelligent score* in [-1, 1] derived from the model's
    class probabilities (prob_up - prob_down).
    """

    prob_up: float = Field(default=0.5, ge=0.0, le=1.0)
    prob_down: float = Field(default=0.5, ge=0.0, le=1.0)
    model_kind: str = "none"
    model_version: str = "untrained"
    feature_count: int = Field(default=0, ge=0)

    # ``model_*`` would otherwise collide with pydantic's protected namespace.
    model_config = DaytradeModel.model_config | {"protected_namespaces": ()}
