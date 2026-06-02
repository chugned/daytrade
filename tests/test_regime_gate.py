"""Regime-gate tests — only trade regimes with a proven edge."""

from __future__ import annotations

from daytrade.observatory.prediction_tracker import GroupAccuracy, PredictionMemory
from daytrade.observatory.regime_gate import evaluate_regime_gate


def _memory(**conditions: tuple[int, int]) -> PredictionMemory:
    """Build a PredictionMemory with the given regime (samples, correct)."""
    mem = PredictionMemory()
    for label, (samples, correct) in conditions.items():
        mem.by_condition[label] = GroupAccuracy(
            label=label, samples=samples, correct=correct, mean_confidence=0.6
        )
    return mem


def test_regime_gate_blocks_a_losing_regime():
    mem = _memory(CALM=(120, 55))  # 46% accuracy, plenty of samples
    result = evaluate_regime_gate("CALM", mem, min_accuracy=0.50, min_samples=30)
    assert result.allowed is False
    assert result.accuracy is not None and result.accuracy < 0.50


def test_regime_gate_allows_a_winning_regime():
    mem = _memory(MIXED=(120, 71))  # 59% accuracy
    result = evaluate_regime_gate("MIXED", mem, min_accuracy=0.50, min_samples=30)
    assert result.allowed is True


def test_regime_gate_allows_when_evidence_is_thin():
    """A regime with too few samples is allowed through to gather evidence."""
    mem = _memory(CALM=(8, 2))  # 25% but only 8 samples
    result = evaluate_regime_gate("CALM", mem, min_accuracy=0.50, min_samples=30)
    assert result.allowed is True
    assert result.samples == 8


def test_regime_gate_allows_an_unknown_regime():
    result = evaluate_regime_gate("BRAND_NEW", _memory(), 0.50, 30)
    assert result.allowed is True
    assert result.samples == 0
