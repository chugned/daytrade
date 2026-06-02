"""Confidence calibration — making the strategy's stated confidence honest.

The live diagnosis showed the strategy was systematically overconfident:
when it said "65% sure" it was right only ~53% of the time. An overconfident
signal makes a confidence threshold meaningless — the gate lets through
trades that only *look* strong.

This fits an isotonic-regression map from *stated* confidence to *empirical*
accuracy, learned from the strategy's own evaluated prediction history. This
is the standard fix for an overconfident classifier (see scikit-learn's
probability-calibration docs); isotonic regression is used because it can
correct any monotonic distortion and there is ample history to fit it.

Until there is enough evaluated history the calibrator is the identity map —
it never invents a correction it cannot support. Paper / simulation only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from sklearn.isotonic import IsotonicRegression

#: Evaluated predictions needed before a calibration map is fitted.
MIN_CALIBRATION_SAMPLES = 80


@dataclass
class ConfidenceCalibrator:
    """Maps a stated confidence to its empirically-true probability."""

    _model: Optional[IsotonicRegression] = None
    n_samples: int = 0

    @property
    def is_fitted(self) -> bool:
        return self._model is not None

    @classmethod
    def fit(
        cls,
        samples: Iterable[Tuple[Optional[float], Optional[object]]],
        min_samples: int = MIN_CALIBRATION_SAMPLES,
    ) -> ConfidenceCalibrator:
        """Fit from ``(stated_confidence, was_correct)`` pairs.

        Returns an unfitted (identity) calibrator when there is too little
        history, or when every outcome shares one class.
        """
        xs: list[float] = []
        ys: list[float] = []
        for stated, correct in samples:
            if stated is None or correct is None:
                continue
            xs.append(float(stated))
            ys.append(1.0 if correct else 0.0)

        if len(xs) < min_samples or len(set(ys)) < 2:
            return cls(_model=None, n_samples=len(xs))

        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(np.asarray(xs, dtype=float), np.asarray(ys, dtype=float))
        return cls(_model=iso, n_samples=len(xs))

    def calibrate(self, stated_confidence: float) -> float:
        """Return the empirically-true probability for ``stated_confidence``.

        Falls back to the identity map while unfitted — an honest "I cannot
        correct what I have not measured".
        """
        stated = float(stated_confidence)
        if self._model is None:
            return stated
        return float(self._model.predict([stated])[0])
