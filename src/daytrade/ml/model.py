"""Predictive model wrapper.

Wraps a scikit-learn classifier (behind a ``StandardScaler``) and exposes a
trading-oriented API:

* :meth:`fit` — train on a :class:`Dataset`
* :meth:`predict_signal` — emit an :class:`MLSignal` with an "intelligent
  score" in [-1, 1] (``prob_up - prob_down``)
* :meth:`save` / :meth:`load` — persist a trained model

An untrained model is honest about it: it emits a neutral signal with zero
confidence rather than pretending to predict.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..config.schema import AppConfig
from ..features import FeaturePipeline
from ..models import OHLCV, MLSignal, ModelKind
from ..models.enums import Bias
from ..runtime import get_logger
from .dataset import Dataset

_log = get_logger("ml.model")
_FORMAT_VERSION = 1


def build_estimator(kind: ModelKind, seed: int = 42) -> Pipeline:
    """Construct a scaler + classifier pipeline for ``kind``."""
    if kind is ModelKind.LOGISTIC_REGRESSION:
        clf = LogisticRegression(max_iter=1000, C=1.0, random_state=seed)
    elif kind is ModelKind.RANDOM_FOREST:
        clf = RandomForestClassifier(
            n_estimators=200,
            max_depth=6,
            min_samples_leaf=5,
            random_state=seed,
            n_jobs=1,
        )
    elif kind is ModelKind.GRADIENT_BOOSTING:
        clf = GradientBoostingClassifier(
            n_estimators=150,
            max_depth=3,
            learning_rate=0.05,
            random_state=seed,
        )
    else:  # pragma: no cover - exhaustive
        raise ValueError(f"unsupported model kind: {kind}")
    return Pipeline([("scaler", StandardScaler()), ("clf", clf)])


@dataclass
class TrainResult:
    """Metrics from a single :meth:`PredictiveModel.fit` call."""

    samples: int
    accuracy: float
    auc: float
    class_balance: Dict[int, int] = field(default_factory=dict)


class PredictiveModel:
    """A trainable directional classifier."""

    def __init__(self, kind: ModelKind = ModelKind.GRADIENT_BOOSTING, seed: int = 42) -> None:
        self.kind = kind
        self.seed = seed
        self._pipeline: Optional[Pipeline] = None
        self.feature_names: List[str] = []
        self.version: str = "untrained"

    @property
    def is_trained(self) -> bool:
        return self._pipeline is not None

    def fit(self, dataset: Dataset) -> TrainResult:
        """Train on ``dataset`` and report in-sample metrics.

        In-sample metrics are diagnostic only — they say nothing about
        generalization. Use walk-forward validation for that.
        """
        if len(dataset) < 20:
            raise ValueError(f"need >= 20 samples to train, got {len(dataset)}")
        if dataset.y.nunique() < 2:
            raise ValueError("training labels contain only one class")

        pipe = build_estimator(self.kind, self.seed)
        pipe.fit(dataset.X.values, dataset.y.values)
        self._pipeline = pipe
        self.feature_names = list(dataset.feature_names)
        self.version = f"{self.kind.value}-n{len(dataset)}"

        proba = pipe.predict_proba(dataset.X.values)[:, 1]
        preds = (proba >= 0.5).astype(int)
        acc = float(accuracy_score(dataset.y.values, preds))
        try:
            auc = float(roc_auc_score(dataset.y.values, proba))
        except ValueError:
            auc = 0.5
        _log.info(
            "trained %s: samples=%d acc=%.3f auc=%.3f", self.kind.value, len(dataset), acc, auc
        )
        return TrainResult(
            samples=len(dataset),
            accuracy=acc,
            auc=auc,
            class_balance=dataset.class_balance,
        )

    def predict_proba_up(self, X: pd.DataFrame) -> np.ndarray:
        """Probability of an up move for each row of ``X``."""
        if self._pipeline is None:
            raise RuntimeError("model is not trained")
        proba = self._pipeline.predict_proba(X.values)
        classes = list(self._pipeline.classes_)
        if 1 in classes:
            return proba[:, classes.index(1)]
        # Degenerate single-class model — return that class's certainty.
        return np.full(len(X), 1.0 if classes[0] == 1 else 0.0)

    def predict_signal(
        self,
        candles: List[OHLCV],
        config: AppConfig,
    ) -> MLSignal:
        """Emit an :class:`MLSignal` for the most recent candle."""
        symbol = candles[-1].symbol
        ts = candles[-1].timestamp

        if self._pipeline is None:
            return MLSignal(
                symbol=symbol,
                timestamp=ts,
                bias=Bias.NEUTRAL,
                score=0.0,
                confidence=0.0,
                reasoning=["ML model not trained — neutral signal"],
                prob_up=0.5,
                prob_down=0.5,
                model_kind=self.kind.value,
                model_version="untrained",
                feature_count=0,
            )

        pipeline = FeaturePipeline(config.features, config.indicators)
        row = pipeline.latest(candles)
        X = row.to_frame().T[self.feature_names]
        if X.isna().any(axis=None):
            return MLSignal(
                symbol=symbol,
                timestamp=ts,
                bias=Bias.NEUTRAL,
                score=0.0,
                confidence=0.0,
                reasoning=["Insufficient history for ML features — neutral"],
                prob_up=0.5,
                prob_down=0.5,
                model_kind=self.kind.value,
                model_version=self.version,
                feature_count=len(self.feature_names),
            )

        prob_up = float(self.predict_proba_up(X)[0])
        prob_down = 1.0 - prob_up
        score = max(-1.0, min(1.0, prob_up - prob_down))
        # Confidence scales with how far the probability is from a coin flip.
        confidence = max(0.0, min(1.0, abs(prob_up - 0.5) * 2.0))
        if score > 0.10:
            bias = Bias.BULLISH
        elif score < -0.10:
            bias = Bias.BEARISH
        else:
            bias = Bias.NEUTRAL

        return MLSignal(
            symbol=symbol,
            timestamp=ts,
            bias=bias,
            score=score,
            confidence=confidence,
            reasoning=[
                f"P(up)={prob_up:.3f} P(down)={prob_down:.3f}",
                f"Model: {self.version}",
            ],
            prob_up=prob_up,
            prob_down=prob_down,
            model_kind=self.kind.value,
            model_version=self.version,
            feature_count=len(self.feature_names),
        )

    def save(self, path: Path | str) -> Path:
        """Persist the trained model to ``path``."""
        if self._pipeline is None:
            raise RuntimeError("refusing to save an untrained model")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": _FORMAT_VERSION,
            "kind": self.kind.value,
            "seed": self.seed,
            "version": self.version,
            "feature_names": self.feature_names,
            "pipeline": self._pipeline,
        }
        with path.open("wb") as fh:
            pickle.dump(payload, fh)
        return path

    @classmethod
    def load(cls, path: Path | str) -> PredictiveModel:
        """Load a model previously written by :meth:`save`."""
        path = Path(path)
        with path.open("rb") as fh:
            payload = pickle.load(fh)
        if payload.get("format_version") != _FORMAT_VERSION:
            raise ValueError(f"unsupported model format in {path}")
        model = cls(kind=ModelKind(payload["kind"]), seed=payload["seed"])
        model._pipeline = payload["pipeline"]
        model.feature_names = payload["feature_names"]
        model.version = payload["version"]
        return model
