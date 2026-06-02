"""Market Safety Score and condition classifier.

Condenses many market signals into one number (0-100) and two human-readable
labels. The language is deliberately about *observation conditions*, never
investment advice — a high score means "conditions are favourable for this
paper strategy to be studied", NOT "safe to invest".

Score bands
-----------
* 0-20   UNSAFE
* 21-40  HIGH_RISK
* 41-60  WAIT
* 61-100 SAFE_TO_OBSERVE  (61-80 acceptable, 81-100 strong paper conditions)

Market condition is a separate axis: CALM, OPPORTUNISTIC, MIXED, CHOPPY,
PANIC, ILLIQUID, OVEREXTENDED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class SafetyInputs:
    """Raw market signals fed into the safety score (one symbol or aggregate)."""

    trend_strength: float  # |normalized trend slope|, ~0..1+
    volatility: float  # realized per-bar volatility, fraction
    liquidity_notional: float  # USD notional resting in the book
    spread_bps: float  # top-of-book spread, basis points
    imbalance: float  # orderbook imbalance, -1..1
    chop: bool  # chop zone detected
    slippage_estimate_bps: float  # expected adverse slippage, bps
    panic: bool  # panic / systemic-stress regime
    recent_accuracy: Optional[float] = None  # 0..1 directional accuracy
    paper_drawdown_pct: float = 0.0  # 0..1 current drawdown
    prediction_reliability: Optional[float] = None  # 0..1 confidence calibration


@dataclass(frozen=True)
class SafetyAssessment:
    """The computed safety verdict."""

    score: float  # 0-100
    status: str  # SAFE_TO_OBSERVE | WAIT | HIGH_RISK | UNSAFE
    condition: str  # CALM | OPPORTUNISTIC | MIXED | ...
    headline: str  # one-line plain-language reason
    reasons: List[str] = field(default_factory=list)
    breakdown: Dict[str, float] = field(default_factory=dict)


# Score band -> status label.
def status_for_score(score: float) -> str:
    if score <= 20:
        return "UNSAFE"
    if score <= 40:
        return "HIGH_RISK"
    if score <= 60:
        return "WAIT"
    return "SAFE_TO_OBSERVE"


def band_label(score: float) -> str:
    """The descriptive band label for a score."""
    return {
        "UNSAFE": "unsafe conditions",
        "HIGH_RISK": "high-risk conditions",
        "WAIT": "wait / mixed conditions",
    }.get(
        status_for_score(score),
        "strong paper conditions" if score > 80 else "acceptable paper conditions",
    )


def _liquidity_score(notional: float) -> float:
    # 0 at $0, ~100 at $1M+ of resting notional.
    return _clamp(notional / 1_000_000.0 * 100.0)


def _volatility_score(vol: float) -> float:
    # Calm (<=0.4% per bar) is best; >2% per bar is dangerous.
    if vol <= 0.004:
        return 100.0
    if vol >= 0.020:
        return 0.0
    return _clamp(100.0 * (0.020 - vol) / (0.020 - 0.004))


def _spread_score(spread_bps: float) -> float:
    if spread_bps <= 3.0:
        return 100.0
    if spread_bps >= 25.0:
        return 0.0
    return _clamp(100.0 * (25.0 - spread_bps) / 22.0)


def _slippage_score(slip_bps: float) -> float:
    if slip_bps <= 3.0:
        return 100.0
    if slip_bps >= 40.0:
        return 0.0
    return _clamp(100.0 * (40.0 - slip_bps) / 37.0)


def _trend_score(strength: float) -> float:
    # A clean, moderate trend is the best observable condition; a flat or a
    # violently strong trend are both less favourable.
    return _clamp(100.0 - abs(strength - 0.5) * 140.0)


def compute_safety_score(inputs: SafetyInputs) -> SafetyAssessment:
    """Compute the safety score, status and market condition for ``inputs``."""
    breakdown: Dict[str, float] = {
        "liquidity": _liquidity_score(inputs.liquidity_notional),
        "volatility": _volatility_score(inputs.volatility),
        "spread": _spread_score(inputs.spread_bps),
        "slippage": _slippage_score(inputs.slippage_estimate_bps),
        "trend": _trend_score(inputs.trend_strength),
        "imbalance": _clamp(100.0 - abs(inputs.imbalance) * 110.0),
    }
    if inputs.recent_accuracy is not None:
        breakdown["model_accuracy"] = _clamp(inputs.recent_accuracy * 200.0 - 50.0)
    if inputs.prediction_reliability is not None:
        breakdown["prediction_reliability"] = _clamp(inputs.prediction_reliability * 100.0)
    breakdown["drawdown"] = _clamp(100.0 - inputs.paper_drawdown_pct * 400.0)

    score = sum(breakdown.values()) / len(breakdown)
    reasons: List[str] = []

    # Hard hazard caps — these conditions dominate any averaged score.
    if inputs.panic:
        score = min(score, 18.0)
        reasons.append("panic / systemic-stress regime detected")
    if inputs.liquidity_notional < 150_000 or inputs.spread_bps > 20.0:
        score = min(score, 38.0)
        reasons.append("thin liquidity / wide spread")
    if inputs.chop:
        score = min(score, 56.0)
        reasons.append("chop zone — directionless price action")
    if inputs.recent_accuracy is not None and inputs.recent_accuracy < 0.45:
        score = min(score, 48.0)
        reasons.append(f"recent prediction accuracy low ({inputs.recent_accuracy:.0%})")
    if inputs.paper_drawdown_pct > 0.10:
        reasons.append(f"paper drawdown elevated ({inputs.paper_drawdown_pct:.0%})")
    if inputs.volatility > 0.018:
        reasons.append("volatility extreme")

    score = round(_clamp(score), 1)
    status = status_for_score(score)
    condition = classify_condition(inputs, score)

    if not reasons:
        reasons.append(band_label(score))
    headline = f"{condition} — {band_label(score)}"

    return SafetyAssessment(
        score=score,
        status=status,
        condition=condition,
        headline=headline,
        reasons=reasons,
        breakdown=breakdown,
    )


def classify_condition(inputs: SafetyInputs, score: float) -> str:
    """Classify the market into one of the seven conditions."""
    if inputs.panic:
        return "PANIC"
    if inputs.liquidity_notional < 150_000 or inputs.spread_bps > 20.0:
        return "ILLIQUID"
    if inputs.volatility > 0.016 and inputs.trend_strength > 0.8:
        return "OVEREXTENDED"
    if inputs.chop:
        return "CHOPPY"
    if score >= 65 and inputs.trend_strength >= 0.35 and inputs.volatility <= 0.010:
        return "OPPORTUNISTIC"
    if score >= 60 and inputs.volatility <= 0.006:
        return "CALM"
    return "MIXED"


def aggregate_safety(assessments: List[SafetyAssessment]) -> SafetyAssessment:
    """Combine per-symbol assessments into one global market assessment.

    The global score leans pessimistic — it is the mean pulled toward the
    worst symbol, because a market is only as safe as its weak points.
    """
    if not assessments:
        return SafetyAssessment(
            50.0, "WAIT", "MIXED", "MIXED — no data yet", ["no symbols observed"], {}
        )
    scores = [a.score for a in assessments]
    total = len(assessments)
    mean = sum(scores) / total
    worst = min(scores)
    score = round(_clamp(0.6 * mean + 0.4 * worst), 1)

    # Global condition: the most severe condition present wins — but a
    # *severe* label (PANIC/ILLIQUID/OVEREXTENDED) only becomes the market-
    # wide headline when it affects a meaningful share of symbols. A couple
    # of thin names out of 35 do not make the whole market "illiquid".
    severity = ["PANIC", "ILLIQUID", "OVEREXTENDED", "CHOPPY", "MIXED", "CALM", "OPPORTUNISTIC"]
    severe = {"PANIC", "ILLIQUID", "OVEREXTENDED"}
    _severe_share_threshold = 0.30

    def _count(cond: str) -> int:
        return sum(1 for a in assessments if a.condition == cond)

    condition = "MIXED"
    for c in severity:
        n = _count(c)
        if n == 0:
            continue
        if c in severe and n / total < _severe_share_threshold:
            continue  # an outlier-few — not a market-wide condition
        condition = c
        break

    status = status_for_score(score)
    reasons = [f"{total} symbols observed; mean {mean:.0f}, weakest {worst:.0f}"]
    for c in severe:
        n = _count(c)
        if n and c != condition:
            reasons.append(f"{n} symbol(s) flagged {c} (outliers)")
        elif n and c == condition:
            reasons.append(f"{n}/{total} symbols in {c}")
    return SafetyAssessment(
        score=score,
        status=status,
        condition=condition,
        headline=f"{condition} — {band_label(score)}",
        reasons=reasons,
        breakdown={"mean": round(mean, 1), "weakest": round(worst, 1)},
    )
