"""Named macro scenarios.

These presets are the deterministic backbone of the mock macro analyzer and
the vocabulary the Gemini analyzer is asked to classify into. Keeping them in
one table makes macro behaviour auditable and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.enums import Bias, RiskLevel


@dataclass(frozen=True)
class MacroScenario:
    """A canonical macro regime."""

    label: str
    bias: Bias
    score: float  # directional, [-1, 1]
    confidence: float  # [0, 1]
    risk_level: RiskLevel
    headline: str


SCENARIOS: dict[str, MacroScenario] = {
    "risk_on": MacroScenario(
        label="risk_on",
        bias=Bias.BULLISH,
        score=0.70,
        confidence=0.85,
        risk_level=RiskLevel.LOW,
        headline="Risk-on: steady inflows, calm volatility, broad appetite for risk.",
    ),
    "institutional_buying": MacroScenario(
        label="institutional_buying",
        bias=Bias.BULLISH,
        score=0.80,
        confidence=0.88,
        risk_level=RiskLevel.LOW,
        headline="Institutional accumulation: large persistent bid, low realized vol.",
    ),
    "neutral": MacroScenario(
        label="neutral",
        bias=Bias.NEUTRAL,
        score=0.0,
        confidence=0.40,
        risk_level=RiskLevel.MEDIUM,
        headline="Neutral macro: no dominant directional driver.",
    ),
    "risk_off": MacroScenario(
        label="risk_off",
        bias=Bias.BEARISH,
        score=-0.55,
        confidence=0.75,
        risk_level=RiskLevel.HIGH,
        headline="Risk-off: defensive rotation, rising volatility.",
    ),
    "panic": MacroScenario(
        label="panic",
        bias=Bias.BEARISH,
        score=-0.85,
        confidence=0.90,
        risk_level=RiskLevel.HIGH,
        headline="Systemic panic: cascading liquidations, broad de-risking.",
    ),
    "war": MacroScenario(
        label="war",
        bias=Bias.BEARISH,
        score=-0.80,
        confidence=0.92,
        risk_level=RiskLevel.EXTREME,
        headline="Geopolitical shock (war): extreme uncertainty, flight to safety.",
    ),
    "exchange_collapse": MacroScenario(
        label="exchange_collapse",
        bias=Bias.BEARISH,
        score=-0.95,
        confidence=0.95,
        risk_level=RiskLevel.EXTREME,
        headline="Exchange collapse: counterparty failure, systemic contagion risk.",
    ),
}

DEFAULT_SCENARIO = "neutral"


def get_scenario(name: str) -> MacroScenario:
    """Look up a scenario by name, falling back to ``neutral``."""
    return SCENARIOS.get(name.lower().strip(), SCENARIOS[DEFAULT_SCENARIO])
