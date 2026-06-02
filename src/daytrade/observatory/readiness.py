"""Paper Strategy Readiness score.

A 0-100 score answering: *how proven is this paper strategy?* — NOT "is it
safe to invest". The wording is deliberately conservative, and there is a
hard rule: before the 30-day window completes the score is capped at 60, so
the dashboard cannot turn falsely optimistic early.

Even a high score never means "safe to invest" — only "strong paper
performance, still not guaranteed".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

# Minimum evaluated predictions for the data-sufficiency input to max out.
_TARGET_PREDICTIONS = 600
# Readiness ceiling until the full observation window has elapsed.
_PRE_COMPLETION_CAP = 60.0


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class ReadinessInputs:
    """The evidence the readiness score is computed from."""

    day_number: int
    target_days: int
    predictions_evaluated: int
    uptime_pct: float
    max_drawdown_pct: float  # 0..100
    overall_accuracy: float  # 0..1
    false_confidence_count: int  # number of fake-confidence warnings
    regimes_observed: int  # distinct market regimes seen
    regime_accuracy_spread: float  # max-min accuracy across regimes, 0..1
    api_failures: int  # API / data failures logged


@dataclass(frozen=True)
class ReadinessAssessment:
    """The computed readiness verdict."""

    score: float
    level: str
    capped: bool
    day_number: int
    headline: str
    breakdown: Dict[str, float] = field(default_factory=dict)
    blockers: List[str] = field(default_factory=list)


def readiness_level(score: float) -> str:
    """Map a readiness score to its descriptive level."""
    if score <= 25:
        return "NOT ENOUGH DATA"
    if score <= 50:
        return "UNRELIABLE"
    if score <= 70:
        return "PROMISING BUT UNPROVEN"
    if score <= 85:
        return "STABLE IN PAPER CONDITIONS"
    return "STRONG PAPER PERFORMANCE, STILL NOT GUARANTEED"


def compute_readiness(inputs: ReadinessInputs) -> ReadinessAssessment:
    """Compute the Paper Strategy Readiness score and level."""
    breakdown: Dict[str, float] = {
        "data_sufficiency": _clamp(inputs.predictions_evaluated / _TARGET_PREDICTIONS * 100.0),
        "uptime": _clamp(inputs.uptime_pct),
        "drawdown": _clamp(100.0 - (inputs.max_drawdown_pct - 5.0) * 5.0),
        "accuracy": _clamp((inputs.overall_accuracy - 0.50) / 0.15 * 100.0),
        "confidence_honesty": _clamp(100.0 - inputs.false_confidence_count * 25.0),
        "regime_robustness": _clamp(
            min(inputs.regimes_observed / 5.0, 1.0) * 100.0 - inputs.regime_accuracy_spread * 120.0
        ),
        "api_reliability": _clamp(100.0 - inputs.api_failures * 8.0),
    }
    # Data sufficiency and accuracy carry the most weight.
    weights = {
        "data_sufficiency": 0.20,
        "uptime": 0.12,
        "drawdown": 0.15,
        "accuracy": 0.22,
        "confidence_honesty": 0.13,
        "regime_robustness": 0.13,
        "api_reliability": 0.05,
    }
    raw = sum(breakdown[k] * w for k, w in weights.items())

    blockers: List[str] = []
    complete = inputs.day_number >= inputs.target_days
    capped = False
    score = raw
    if not complete:
        if raw > _PRE_COMPLETION_CAP:
            capped = True
        score = min(raw, _PRE_COMPLETION_CAP)
        blockers.append(
            f"Only day {inputs.day_number}/{inputs.target_days} — readiness "
            f"is capped at {_PRE_COMPLETION_CAP:.0f} until the window completes."
        )
    if inputs.predictions_evaluated < 200:
        blockers.append(
            f"Only {inputs.predictions_evaluated} evaluated predictions — "
            "not enough evidence yet."
        )
    if inputs.overall_accuracy < 0.5 and inputs.predictions_evaluated >= 50:
        blockers.append(
            "Directional accuracy below a coin flip — " "strategy currently unreliable."
        )
    if inputs.max_drawdown_pct > 20:
        blockers.append(f"Paper drawdown {inputs.max_drawdown_pct:.0f}% is high.")
    if inputs.false_confidence_count > 0:
        blockers.append(
            f"{inputs.false_confidence_count} false-confidence "
            "warning(s) — high confidence is not yet proven."
        )
    if inputs.regimes_observed < 3:
        blockers.append("Too few market regimes observed to judge robustness.")

    score = round(_clamp(score), 1)
    level = readiness_level(score)
    headline = level
    if not complete:
        headline += f" (day {inputs.day_number}/{inputs.target_days})"
    return ReadinessAssessment(
        score=score,
        level=level,
        capped=capped,
        day_number=inputs.day_number,
        headline=headline,
        breakdown={k: round(v, 1) for k, v in breakdown.items()},
        blockers=blockers,
    )
