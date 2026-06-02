"""Meta-labelling — a precision filter on the primary signal.

The primary strategy (the fusion engine) decides *direction*. Its live
direction accuracy is ~50%, and even its correct calls often get stopped
out before they pay. Meta-labelling (Marcos Lopez de Prado, *Advances in
Financial Machine Learning*) adds a *secondary* model with a narrower job:
"if I take a long here, will it hit its target before its stop?"

The meta-model is trained on triple-barrier outcomes — for every past bar,
did a long entered there reach the target, the stop, or time out. At
decision time the primary proposes a BUY and the meta-model gates it: only
the BUYs it scores above a probability floor are traded. Fewer trades,
higher precision.

Paper / simulation only — it never places a real order.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..config.schema import AppConfig
from ..features import FeaturePipeline
from ..indicators import core
from ..indicators.frame import ohlcv_to_frame
from ..labels import triple_barrier_label
from ..models import OHLCV
from ..runtime import get_logger

_log = get_logger("ml.meta")
_FORMAT_VERSION = 1
_MIN_TRAIN_ROWS = 60


def barrier_distances(frame, config: AppConfig) -> tuple:
    """Per-bar (stop, target) price distances, matching the fusion geometry.

    Uses the same volatility unit the live engine uses: a 14-bar ATR clipped
    to the configured volatility-fraction band, scaled by the stop / target
    multipliers.
    """
    close = frame["close"]
    atr = core.atr(frame["high"], frame["low"], frame["close"], 14)
    frac = (atr / close).clip(
        lower=config.fusion.min_volatility_fraction, upper=config.fusion.max_volatility_fraction
    )
    frac = frac.fillna(config.fusion.min_volatility_fraction)
    unit = close * frac
    return unit * config.fusion.stop_vol_mult, unit * config.fusion.target_vol_mult


@dataclass
class MetaTrainResult:
    """Metrics from a :meth:`MetaLabelModel.train` call."""

    samples: int
    base_win_rate: float  # fraction of training trades that won
    train_accuracy: float


class MetaLabelModel:
    """A secondary classifier predicting P(a long here hits target first)."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self._pipeline: Optional[Pipeline] = None
        self.feature_names: List[str] = []
        self.n_samples = 0
        # Unconditional fraction of training trades that won — the meta-gate
        # threshold is set relative to this, not to a fixed probability.
        self.base_win_rate: Optional[float] = None

    @property
    def is_trained(self) -> bool:
        return self._pipeline is not None

    def _training_frame(self, candles: List[OHLCV], config: AppConfig):
        """Build aligned (features, triple-barrier labels) for ``candles``."""
        frame = ohlcv_to_frame(candles)
        pipe = FeaturePipeline(config.features, config.indicators)
        feats = pipe.transform_frame(frame)
        stop_d, target_d = barrier_distances(frame, config)
        labels = triple_barrier_label(frame, stop_d, target_d, max(1, config.risk.max_hold_bars))
        joined = feats.join(labels, how="inner").dropna()
        return joined, list(pipe.columns)

    def train(self, candle_sets: List[List[OHLCV]], config: AppConfig) -> Optional[MetaTrainResult]:
        """Train on one or more candle series pooled together.

        Returns None when there is too little resolvable history, or when
        every trade in the window shares a single outcome.
        """
        rows = []
        cols: List[str] = []
        for candles in candle_sets:
            if len(candles) < 120:
                continue
            joined, cols = self._training_frame(candles, config)
            if not joined.empty:
                rows.append(joined)
        if not rows or not cols:
            return None

        data = rows[0] if len(rows) == 1 else _concat(rows)
        if len(data) < _MIN_TRAIN_ROWS:
            return None
        X = data[cols].to_numpy(dtype=float)
        y = data["meta_label"].astype(int).to_numpy()
        if len(np.unique(y)) < 2:
            return None

        pipeline = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    GradientBoostingClassifier(
                        n_estimators=120, max_depth=3, learning_rate=0.05, random_state=self.seed
                    ),
                ),
            ]
        )
        pipeline.fit(X, y)
        self._pipeline = pipeline
        self.feature_names = cols
        self.n_samples = len(data)
        self.base_win_rate = float(y.mean())
        acc = float((pipeline.predict(X) == y).mean())
        result = MetaTrainResult(
            samples=len(data), base_win_rate=self.base_win_rate, train_accuracy=acc
        )
        _log.info(
            "meta-model trained: samples=%d base_win=%.3f acc=%.3f",
            result.samples,
            result.base_win_rate,
            result.train_accuracy,
        )
        return result

    def predict_win_proba(self, candles: List[OHLCV], config: AppConfig) -> Optional[float]:
        """P(a long entered on the latest bar hits target before stop).

        Returns None when the model is untrained or features are unavailable
        — callers treat None as "no opinion; do not block".
        """
        if self._pipeline is None:
            return None
        pipe = FeaturePipeline(config.features, config.indicators)
        row = pipe.latest(candles)
        X = row.to_frame().T[self.feature_names]
        if X.isna().any(axis=None):
            return None
        classes = list(self._pipeline.classes_)
        proba = self._pipeline.predict_proba(X.to_numpy(dtype=float))[0]
        if 1 in classes:
            return float(proba[classes.index(1)])
        return 1.0 if classes[0] == 1 else 0.0

    def save(self, path: Path | str) -> Path:
        if self._pipeline is None:
            raise RuntimeError("refusing to save an untrained meta-model")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump(
                {
                    "format_version": _FORMAT_VERSION,
                    "seed": self.seed,
                    "feature_names": self.feature_names,
                    "n_samples": self.n_samples,
                    "base_win_rate": self.base_win_rate,
                    "pipeline": self._pipeline,
                },
                fh,
            )
        return path

    @classmethod
    def load(cls, path: Path | str) -> MetaLabelModel:
        with Path(path).open("rb") as fh:
            payload = pickle.load(fh)
        if payload.get("format_version") != _FORMAT_VERSION:
            raise ValueError(f"unsupported meta-model format in {path}")
        model = cls(seed=payload["seed"])
        model._pipeline = payload["pipeline"]
        model.feature_names = payload["feature_names"]
        model.n_samples = payload["n_samples"]
        model.base_win_rate = payload.get("base_win_rate")
        return model


def _concat(frames):
    """Row-concatenate training frames (kept local to avoid a top-level dep)."""
    import pandas as pd

    return pd.concat(frames, axis=0, ignore_index=True)
