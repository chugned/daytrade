"""Shared helpers for the cascade × meta-gate sweep scripts.

The sweep scripts in ``scripts/`` (sweep_cascade_meta_interaction.py,
sweep_cost_horizon.py, sweep_p5_4_validate.py) all need the same
sequence:

1. Pull cached candles for ``(symbol, days)``.
2. Build a feature + label + forward-return frame.
3. Cache the result at a horizon-keyed parquet path.
4. Split chronologically into train/test.
5. Score the test set with a trained ``MetaLabelModel``.

Earlier those helpers lived in ``sweep_cascade_meta_interaction.py``
and the other scripts cross-imported them via ``sys.path.insert(0,
…)`` hacks. This module makes them a proper package import so:

- The cross-script ``sys.path`` shim goes away.
- The helpers are testable in isolation.
- The cache key + frame contract live in one place — changing them
  doesn't risk drifting between scripts.

All functions here are pure (deterministic for given inputs) and
read-only — no live state touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from ..config import AppConfig
from ..features.pipeline import FeaturePipeline
from ..indicators.frame import ohlcv_to_frame
from ..labels.generate import triple_barrier_label
from ..ml.meta import MetaLabelModel, barrier_distances
from ..models import OHLCV
from .history import download_history


#: Parquet cache root. Files keyed by ``(symbol, days, max_hold)`` —
#: see ``frame_cache_path``. A 90d × 1m × 6-symbol cold build is ~3 min;
#: a warm read is ~5s. Cache GC is the operator's responsibility (just
#: ``rm -rf artifacts/cache/cascade_meta_frames`` to force a rebuild).
FRAME_CACHE_DIR = Path("artifacts/cache/cascade_meta_frames")


def pull_candles(symbol: str, days: int, interval: str = "1m") -> List[OHLCV]:
    """Thin wrapper for clarity at call sites — the SQLite cache in
    ``research.history`` does the heavy lifting."""
    return download_history(symbol, interval=interval, days=days)


def frame_cache_path(symbol: str, days: int, max_hold: int = 48) -> Path:
    """Parquet path for the joined feature/label/return frame.

    Cache key MUST include ``max_hold`` — the joined frame includes
    triple-barrier labels and forward returns, both of which depend on
    the horizon. A previous version of this code keyed only on
    ``(symbol, days)`` and would silently serve stale labels to a
    sweep running at a different horizon.
    """
    return FRAME_CACHE_DIR / f"{symbol}_{days}d_h{max_hold}.parquet"


def train_test_split(frame: pd.DataFrame,
                     train_frac: float = 0.7) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split. No shuffle — preserves the train-before-test
    ordering that makes the held-out window an honest forward test."""
    cut = int(len(frame) * train_frac)
    return frame.iloc[:cut].copy(), frame.iloc[cut:].copy()


def build_per_symbol_frame(candles: List[OHLCV],
                           config: AppConfig) -> Optional[pd.DataFrame]:
    """Feature pipeline + triple-barrier labels + forward returns,
    joined and NaN-pruned. Returns ``None`` for too-short candle lists."""
    if len(candles) < 200:
        return None
    frame = ohlcv_to_frame(candles)
    pipe = FeaturePipeline(config.features, config.indicators)
    feats = pipe.transform_frame(frame)
    stop_d, target_d = barrier_distances(frame, config)
    max_hold = max(1, config.risk.max_hold_bars)
    labels = triple_barrier_label(frame, stop_d, target_d, max_hold)
    # Forward return at the same horizon as the label (vertical
    # barrier). The triple-barrier return is bounded by stop/target;
    # raw close-to-close at the same window gives a clean apples-to-
    # apples slice metric for the analyzer.
    close = frame["close"].astype(float)
    fwd_return_bps = (close.shift(-max_hold) - close) / close * 10_000.0
    joined = feats.join(labels, how="inner")
    joined["forward_return_bps"] = fwd_return_bps
    return joined.dropna()


def load_or_build_frame(symbol: str, days: int, config: AppConfig,
                        use_cache: bool = True) -> Optional[pd.DataFrame]:
    """Cached ``build_per_symbol_frame`` round-trip. First run writes
    the parquet; subsequent runs at the same ``(symbol, days, max_hold)``
    load it in ~1s instead of rebuilding in ~20s."""
    cache_path = frame_cache_path(symbol, days,
                                   max(1, config.risk.max_hold_bars))
    if use_cache and cache_path.exists():
        try:
            return pd.read_parquet(cache_path)
        except (OSError, ValueError):
            pass  # corrupted cache — fall through to rebuild
    candles = pull_candles(symbol, days)
    frame = build_per_symbol_frame(candles, config)
    if frame is None or frame.empty:
        return frame
    if use_cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            frame.to_parquet(cache_path)
        except (OSError, ImportError):
            # pyarrow not available or write failed — return uncached
            pass
    return frame


def score_test_frame(model: MetaLabelModel,
                     test_df: pd.DataFrame) -> Optional[pd.Series]:
    """Vectorised P(win) per row of ``test_df`` using ``model``.
    Returns ``None`` if the model's feature columns aren't all present
    (defensive — would only fire on a stale cache from before the
    feature pipeline added a new column)."""
    if not model.is_trained:
        return None
    feature_cols = model.feature_names
    if not all(c in test_df.columns for c in feature_cols):
        return None
    X = test_df[feature_cols].to_numpy(dtype=float)
    classes = list(model._pipeline.classes_)
    proba_all = model._pipeline.predict_proba(X)
    if 1 in classes:
        return pd.Series(proba_all[:, classes.index(1)], index=test_df.index)
    return pd.Series(1.0 if classes[0] == 1 else 0.0, index=test_df.index)
