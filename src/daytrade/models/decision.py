"""The fused trading decision — the central output of the analysis pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from pydantic import Field, field_validator, model_validator

from ._base import DaytradeModel, normalize_timestamp
from .enums import Action


class TradingDecision(DaytradeModel):
    """A complete, explainable trading decision produced by the fusion engine.

    For ``HOLD`` the price levels are typically ``None``. For ``BUY``/``SELL``
    the engine fills entry/stop/target and the validator checks they are
    geometrically consistent with the direction.
    """

    symbol: str
    timestamp: datetime
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)

    entry: float | None = Field(default=None, gt=0)
    stop: float | None = Field(default=None, gt=0)
    target: float | None = Field(default=None, gt=0)

    reference_price: float | None = Field(
        default=None,
        gt=0,
        description="Consensus/market price the decision was computed against.",
    )

    component_scores: Dict[str, float] = Field(
        default_factory=dict,
        description="Per-layer scores in [-1,1] (technical, microstructure, macro, ml).",
    )
    fused_score: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="The blended score that drove the action.",
    )

    kill_switch_active: bool = False
    kill_switch_reasons: List[str] = Field(default_factory=list)
    reasoning: List[str] = Field(default_factory=list)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v: object) -> datetime:
        return normalize_timestamp(v)

    @model_validator(mode="after")
    def _check_levels(self) -> TradingDecision:
        if self.action is Action.HOLD:
            return self
        entry, stop, target = self.entry, self.stop, self.target
        if entry is None or stop is None or target is None:
            raise ValueError(f"{self.action} decision requires entry/stop/target")
        if self.action is Action.BUY:
            if not (stop < entry < target):
                raise ValueError(f"BUY needs stop < entry < target, got {stop}/{entry}/{target}")
        else:  # SELL
            if not (target < entry < stop):
                raise ValueError(f"SELL needs target < entry < stop, got {target}/{entry}/{stop}")
        return self

    @property
    def is_actionable(self) -> bool:
        return self.action is not Action.HOLD

    @property
    def risk_reward(self) -> float | None:
        """Reward-to-risk ratio: (target-entry) / (entry-stop), absolute."""
        if not self.is_actionable or self.entry is None:
            return None
        risk = abs(self.entry - self.stop)  # type: ignore[arg-type]
        reward = abs(self.target - self.entry)  # type: ignore[arg-type]
        if risk == 0:
            return None
        return reward / risk
