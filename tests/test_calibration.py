"""Confidence-calibration tests — correcting an overconfident strategy."""

from __future__ import annotations

import random

from daytrade.observatory.calibration import ConfidenceCalibrator


def test_calibrator_is_identity_without_enough_history():
    """Too little history -> the calibrator must not invent a correction."""
    cal = ConfidenceCalibrator.fit([(0.6, True), (0.7, False), (0.55, True)])
    assert not cal.is_fitted
    assert cal.calibrate(0.62) == 0.62


def test_calibrator_is_identity_when_one_class():
    """All-correct (or all-wrong) history cannot calibrate anything."""
    cal = ConfidenceCalibrator.fit([(0.6, True)] * 200)
    assert not cal.is_fitted
    assert cal.calibrate(0.9) == 0.9


def test_calibrator_corrects_systematic_overconfidence():
    """A model that overstates confidence by ~15 points gets pulled down."""
    rng = random.Random(42)
    samples = []
    for _ in range(500):
        stated = rng.uniform(0.40, 0.85)
        true_p = stated - 0.15  # systematically overconfident
        samples.append((stated, rng.random() < true_p))

    cal = ConfidenceCalibrator.fit(samples)
    assert cal.is_fitted
    # A stated 0.70 should calibrate well below 0.70 (true rate ~0.55).
    calibrated = cal.calibrate(0.70)
    assert calibrated < 0.66
    assert calibrated < 0.70


def test_calibrator_is_monotonic():
    """Higher stated confidence never calibrates to a lower probability."""
    rng = random.Random(7)
    samples = [(s := rng.uniform(0.4, 0.9), rng.random() < s - 0.1) for _ in range(400)]
    cal = ConfidenceCalibrator.fit(samples)
    assert cal.is_fitted
    assert cal.calibrate(0.50) <= cal.calibrate(0.70) <= cal.calibrate(0.85)
