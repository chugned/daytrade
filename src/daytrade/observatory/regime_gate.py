"""Regime gate — only trade in market regimes with a proven edge.

The live diagnosis showed the strategy made 87% of its predictions in the
"CALM" regime, where it was right just 48% of the time — worse than a coin
flip. Trading a regime with no edge only bleeds fees.

This gate consults the strategy's *own* accumulated, out-of-sample accuracy
per regime and blocks new trades in regimes that have proven unprofitable.
A regime with too little history is allowed through — the gate needs
evidence before it judges, and a blocked regime keeps producing (un-traded)
predictions, so its accuracy stays live and the gate can re-open it.

The gate can only suppress a trade, never create one. Paper / simulation
only — it never touches a real order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .prediction_tracker import PredictionMemory


@dataclass(frozen=True)
class RegimeGateResult:
    """Outcome of a regime-gate check."""

    allowed: bool
    regime: str
    reason: str
    accuracy: Optional[float] = None
    samples: int = 0


def evaluate_regime_gate(
    regime: str,
    memory: PredictionMemory,
    min_accuracy: float,
    min_samples: int,
) -> RegimeGateResult:
    """Decide whether new trades are allowed in ``regime``.

    Args:
        regime: the current market regime / condition label.
        memory: accumulated prediction-outcome memory.
        min_accuracy: accuracy floor a judged regime must clear.
        min_samples: evaluated predictions a regime needs before it is judged.
    """
    group = memory.by_condition.get(regime)
    samples = group.samples if group else 0

    if group is None or samples < min_samples:
        return RegimeGateResult(
            allowed=True,
            regime=regime,
            samples=samples,
            reason=f"only {samples} samples — allowed through to gather evidence",
            accuracy=group.accuracy if group else None,
        )
    if group.accuracy < min_accuracy:
        return RegimeGateResult(
            allowed=False,
            regime=regime,
            samples=samples,
            accuracy=group.accuracy,
            reason=(
                f"regime accuracy {group.accuracy:.0%} below the "
                f"{min_accuracy:.0%} floor ({samples} samples)"
            ),
        )
    return RegimeGateResult(
        allowed=True,
        regime=regime,
        samples=samples,
        accuracy=group.accuracy,
        reason=(f"regime accuracy {group.accuracy:.0%} clears the " f"{min_accuracy:.0%} floor"),
    )
