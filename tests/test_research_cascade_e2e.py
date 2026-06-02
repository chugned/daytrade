"""End-to-end cohesion test for the cascade × meta-gate research pipeline.

The unit tests cover each layer in isolation:
- ``test_cascade_features.py``           — FeaturePipeline cascade columns
- ``test_cascade_validation.py``         — detector edge stats
- ``test_cascade_meta_interaction.py``   — slice analyser logic
- ``test_meta_label.py``                 — MetaLabelModel train + predict

This file glues them together: a deterministic candle generator
feeds a synthetic cascade-then-rebound regime through the *real*
FeaturePipeline + the *real* MetaLabelModel + the *real* analyser,
and asserts the end-to-end shape matches what the sweep script
emits. If a refactor breaks the boundary between any two of those
layers, this test will fail before the sweep script does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pandas as pd
import pytest

from daytrade.config import AppConfig
from daytrade.features.pipeline import FeaturePipeline
from daytrade.indicators.frame import ohlcv_to_frame
from daytrade.labels.generate import triple_barrier_label
from daytrade.ml.meta import MetaLabelModel, barrier_distances
from daytrade.models import OHLCV
from daytrade.research.cascade_meta_interaction import (
    analyze_cascade_meta_interaction,
)


def _synthetic_market(n_bars: int = 600, seed: int = 0) -> List[OHLCV]:
    """A deterministic mixed-regime tape: drift + noise + occasional
    cascade events. Designed so the meta-model has SOMETHING to learn
    AND the cascade detector fires (CASCADE_ACTIVE bar followed by a
    CASCADE_EXHAUSTION bar with a deep lower wick) several times."""
    rng = np.random.default_rng(seed)
    candles: List[OHLCV] = []
    base = 100.0
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n_bars):
        drift = 0.0001 * np.sin(i / 50.0)
        noise = rng.normal(0, 0.0008)
        ret = drift + noise

        deep_wick = False
        # Every 80 bars: cascade-active (sharp down + volume), then
        # cascade-exhaustion (small body + deep lower wick + still-
        # elevated volume), then rebound.
        if i > 30 and i % 80 == 0:
            ret = -0.012   # CASCADE_ACTIVE: big bearish body
            vol_mult = 6.0
        elif i > 30 and (i - 1) % 80 == 0:
            ret = +0.002   # tiny green body — small body is part of
            vol_mult = 4.0  # the exhaustion signature
            deep_wick = True
        elif i > 30 and (i - 2) % 80 == 0:
            ret = +0.006   # rebound continues
            vol_mult = 2.5
        else:
            vol_mult = 1.0

        new = base * (1 + ret)
        if deep_wick:
            # Force a wick well below the body — the cascade-exhaustion
            # detector keys on lower_wick / total_range >= 0.55.
            hi = max(base, new) * (1 + abs(rng.normal(0, 0.0003)))
            lo = min(base, new) * 0.985  # ~1.5% deep wick
        else:
            hi = max(base, new) * (1 + abs(rng.normal(0, 0.0003)))
            lo = min(base, new) * (1 - abs(rng.normal(0, 0.0003)))
        candles.append(OHLCV(
            symbol="SYNTH",
            timestamp=t0 + timedelta(minutes=i),
            open=base, high=hi, low=lo, close=new,
            volume=1000.0 * vol_mult,
        ))
        base = new
    return candles


def _build_eval_frame(candles: List[OHLCV], config: AppConfig) -> pd.DataFrame:
    """Replicates _build_per_symbol_frame from the sweep script."""
    frame = ohlcv_to_frame(candles)
    pipe = FeaturePipeline(config.features, config.indicators)
    feats = pipe.transform_frame(frame)
    stop_d, target_d = barrier_distances(frame, config)
    max_hold = max(1, config.risk.max_hold_bars)
    labels = triple_barrier_label(frame, stop_d, target_d, max_hold)
    close = frame["close"].astype(float)
    fwd_return_bps = (close.shift(-max_hold) - close) / close * 10_000.0
    joined = feats.join(labels, how="inner")
    joined["forward_return_bps"] = fwd_return_bps
    return joined.dropna()


# ---------------------------------------------------------------------------
# Cohesion test
# ---------------------------------------------------------------------------

def test_full_research_pipeline_produces_all_five_slices():
    """The pipeline FeaturePipeline → MetaLabelModel → analyzer must
    produce all five slices with the expected shape, even on a small
    synthetic dataset. This is the contract the sweep script relies on."""
    config = AppConfig()
    # 2000 bars so after dropna (loses ~max_hold_bars at the tail)
    # the test slice has enough rows for the analyzer to be meaningful.
    candles = _synthetic_market(n_bars=2000, seed=42)
    frame = _build_eval_frame(candles, config)

    # Sanity: the cascade columns the analyzer needs are present
    assert "cascade_exhaustion" in frame.columns
    assert "meta_label" in frame.columns
    assert "forward_return_bps" in frame.columns

    # 70/30 chronological split
    cut = int(len(frame) * 0.7)
    train_df = frame.iloc[:cut]
    test_df = frame.iloc[cut:]
    assert len(train_df) > 100 and len(test_df) > 100

    # Train the REAL meta-model (no mocks)
    model = MetaLabelModel()
    result = model.train([candles[:int(len(candles) * 0.7)]], config)
    assert result is not None
    assert model.is_trained

    # Score the held-out slice
    X = test_df[model.feature_names].to_numpy(dtype=float)
    classes = list(model._pipeline.classes_)
    if 1 in classes:
        proba_vec = model._pipeline.predict_proba(X)[:, classes.index(1)]
    else:
        proba_vec = np.full(len(X), 1.0 if classes[0] == 1 else 0.0)
    proba = pd.Series(proba_vec, index=test_df.index)

    # Plumb through the analyzer
    metrics = analyze_cascade_meta_interaction(
        cascade_exhaustion=test_df["cascade_exhaustion"].astype(int),
        meta_label=test_df["meta_label"].astype(int),
        meta_proba=proba,
        forward_return_bps=test_df["forward_return_bps"].astype(float),
        base_win_rate=result.base_win_rate,
        gate_multiple=2.0,
        round_trip_cost_bps=24.0,
    )

    # All 5 slices present
    assert set(metrics.keys()) == {
        "all", "cascade_exhaustion", "meta_gated",
        "cascade_and_gated", "cascade_or_gated",
    }
    # "all" slice covers the full held-out window
    assert metrics["all"].n == len(test_df)
    # Union is at least as large as either input
    assert metrics["cascade_or_gated"].n >= metrics["meta_gated"].n
    assert metrics["cascade_or_gated"].n >= metrics["cascade_exhaustion"].n


def test_cascade_detector_actually_fires_on_synthetic_fixture():
    """The synthetic fixture is supposed to have cascade events every
    80 bars. If a refactor breaks the detector or the fixture, this
    fires before any downstream test gets a chance to confuse us."""
    config = AppConfig()
    frame = _build_eval_frame(_synthetic_market(n_bars=2000, seed=42), config)
    n_exh = int(frame["cascade_exhaustion"].sum())
    assert n_exh >= 3, (
        f"expected at least 3 exhaustion bars in the 2000-bar fixture, "
        f"got {n_exh} — fixture or detector regressed"
    )


def test_db_retry_proxy_does_not_break_concurrent_observatory_use(tmp_path):
    """Cohesion check between two recently-added pieces:
    _RetryingConnection (P4-2) must not break the observatory's
    normal append-and-read flow used by the rest of the codebase.
    A bug in __getattr__ pass-through would break almost everything."""
    from daytrade.observatory.database import ObservatoryDB

    db = ObservatoryDB(path=tmp_path / "obs.db")
    pid = db.start_bot_run(pid=12345)
    db.heartbeat(pid, cycles=1)
    db.insert_snapshot(symbol="TEST", price=100.0, rsi=50.0)
    db.insert_symbol_health(symbol="TEST", price=100.0, healthy=1, rejections=[])
    db.insert_error(context="test", message="hello")
    # The proxy must pass through to executescript, fetchone, etc.
    row = db._conn.execute("SELECT COUNT(*) FROM errors").fetchone()
    assert row[0] == 1
