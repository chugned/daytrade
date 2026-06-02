"""Training-dataset assembly: align features with future-looking labels.

This is the one place features (causal) and labels (forward-looking) meet.
The join is index-aligned, then every row containing a NaN — warmup gaps *and*
the trailing rows whose future is unknown — is dropped. What remains is a
clean, leak-free supervised dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pandas as pd

from ..config.schema import AppConfig
from ..features import FeaturePipeline
from ..indicators.frame import ohlcv_to_frame
from ..labels import make_labels
from ..models import OHLCV


@dataclass(frozen=True)
class Dataset:
    """An aligned, NaN-free supervised dataset."""

    X: pd.DataFrame
    y: pd.Series
    feature_names: List[str]

    def __len__(self) -> int:
        return len(self.X)

    @property
    def class_balance(self) -> dict[int, int]:
        return {int(k): int(v) for k, v in self.y.value_counts().items()}


def build_dataset(
    candles: List[OHLCV],
    config: AppConfig,
    label_kind: str = "breakout",
) -> Dataset:
    """Assemble a :class:`Dataset` from raw candles and the app config."""
    if len(candles) < 50:
        raise ValueError("need at least 50 candles to build a dataset")

    frame = ohlcv_to_frame(candles)
    pipeline = FeaturePipeline(config.features, config.indicators)
    features = pipeline.transform_frame(frame)
    labels = make_labels(
        frame,
        horizon=config.labels.horizon,
        threshold=config.labels.breakout_threshold,
        kind=label_kind,
    )

    joined = features.join(labels, how="inner")
    # Drop warmup-NaN feature rows AND trailing rows with no known future.
    joined = joined.dropna()

    X = joined[pipeline.columns].copy()
    y = joined["label"].astype(int)
    return Dataset(X=X, y=y, feature_names=list(pipeline.columns))
