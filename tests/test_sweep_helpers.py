"""Tests for the shared sweep helpers used by all cascade sweep scripts.

These helpers were previously inline in ``scripts/sweep_cascade_meta_interaction.py``
and consumed via ``sys.path`` hacks from sibling scripts. Now in
``daytrade.research.sweep_helpers`` as a proper package import.

The tests cover the pure-logic helpers (cache-path keying, split shape,
score round-trip). The frame-builder is exercised end-to-end against
synthetic candles to keep the test fast + deterministic without
touching the Binance cache.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest

from daytrade.config import AppConfig
from daytrade.models import OHLCV
from daytrade.research.sweep_helpers import (
    FRAME_CACHE_DIR,
    build_per_symbol_frame,
    frame_cache_path,
    load_or_build_frame,
    score_test_frame,
    train_test_split,
)


def _candles(n: int, seed: int = 0) -> List[OHLCV]:
    """Deterministic small market with enough variability that:
    - price moves far enough for triple-barrier to resolve in the
      default max_hold_bars window (noise=0.005);
    - volume has non-zero variance so ``volume_z`` isn't NaN in
      every row (constant volume = zero stdev = NaN z-score, which
      empties the dropna'd frame downstream)."""
    rng = np.random.default_rng(seed)
    base = 100.0
    out = []
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        ret = rng.normal(0, 0.005)
        new = base * (1 + ret)
        out.append(OHLCV(
            symbol="SYNTH",
            timestamp=t0 + timedelta(minutes=i),
            open=base,
            high=max(base, new) * 1.0008,
            low=min(base, new) * 0.9992,
            close=new,
            volume=float(rng.lognormal(7.0, 0.5)),
        ))
        base = new
    return out


# ---------------------------------------------------------------------------
# frame_cache_path — key MUST include max_hold
# ---------------------------------------------------------------------------

def test_cache_path_includes_symbol_days_max_hold():
    p = frame_cache_path("BTCUSDT", 30, 48)
    assert "BTCUSDT" in str(p)
    assert "30d" in str(p)
    assert "h48" in str(p)


def test_cache_path_different_horizons_different_files():
    """Two horizons → two different files. A regression here would
    cause stale labels to be served across horizons."""
    p_short = frame_cache_path("BTCUSDT", 30, 30)
    p_long = frame_cache_path("BTCUSDT", 30, 240)
    assert p_short != p_long


def test_cache_path_uses_canonical_cache_dir():
    p = frame_cache_path("X", 1, 1)
    assert p.parent == FRAME_CACHE_DIR


# ---------------------------------------------------------------------------
# train_test_split — chronological, no shuffle
# ---------------------------------------------------------------------------

def test_split_chronological_preserves_order():
    df = pd.DataFrame({"x": list(range(100))})
    train, test = train_test_split(df, train_frac=0.7)
    assert len(train) == 70 and len(test) == 30
    # Order preserved — train rows are all before test rows
    assert train.iloc[-1]["x"] == 69
    assert test.iloc[0]["x"] == 70


def test_split_default_fraction_is_seventy_percent():
    df = pd.DataFrame({"x": list(range(1000))})
    train, test = train_test_split(df)
    assert len(train) == 700
    assert len(test) == 300


# ---------------------------------------------------------------------------
# build_per_symbol_frame
# ---------------------------------------------------------------------------

def test_build_returns_none_for_too_short_input():
    assert build_per_symbol_frame(_candles(50), AppConfig()) is None


def test_build_produces_required_columns():
    """The sweep analyzer expects cascade_exhaustion + meta_label +
    forward_return_bps. A regression here would break every sweep."""
    frame = build_per_symbol_frame(_candles(800), AppConfig())
    assert frame is not None
    # The fixture is sized so the frame is non-empty post-dropna —
    # a bug that empties the frame would still let the column-name
    # check pass, so assert length too.
    assert len(frame) > 100, f"frame was {len(frame)} rows — too few to be useful"
    for col in ("cascade_exhaustion", "meta_label", "forward_return_bps"):
        assert col in frame.columns, f"missing required column {col}"


def test_build_drops_unresolved_label_rows():
    """Triple-barrier labels are NaN near the end (no future bars to
    resolve). The joined frame must drop those rows so downstream
    metrics aren't divided by missing labels."""
    frame = build_per_symbol_frame(_candles(800), AppConfig())
    assert frame is not None
    # No NaN in any of the analyzer-required columns
    assert frame["meta_label"].notna().all()
    assert frame["forward_return_bps"].notna().all()


# ---------------------------------------------------------------------------
# load_or_build_frame — parquet round-trip
# ---------------------------------------------------------------------------

def test_load_or_build_writes_then_reads_cache(tmp_path, monkeypatch):
    """First call builds + writes, second call reads. Both return
    frames with the same row count + column set."""
    monkeypatch.setattr(
        "daytrade.research.sweep_helpers.FRAME_CACHE_DIR", tmp_path / "cache"
    )

    # Stub the candle pull so we don't hit the network or real cache
    monkeypatch.setattr(
        "daytrade.research.sweep_helpers.pull_candles",
        lambda sym, days, interval="1m": _candles(800),
    )

    cfg = AppConfig()
    # First call: cold, builds
    f1 = load_or_build_frame("SYNTH", 1, cfg, use_cache=True)
    assert f1 is not None and len(f1) > 0
    # Cache file should now exist (under tmp_path/cache, the patched dir)
    assert any((tmp_path / "cache").glob("*.parquet"))

    # Second call: hot, reads
    f2 = load_or_build_frame("SYNTH", 1, cfg, use_cache=True)
    assert f2 is not None
    assert len(f1) == len(f2)
    assert list(f1.columns) == list(f2.columns)


def test_load_or_build_no_cache_path_bypasses_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "daytrade.research.sweep_helpers.FRAME_CACHE_DIR", tmp_path / "cache"
    )
    monkeypatch.setattr(
        "daytrade.research.sweep_helpers.pull_candles",
        lambda sym, days, interval="1m": _candles(800),
    )
    cfg = AppConfig()
    f = load_or_build_frame("X", 1, cfg, use_cache=False)
    assert f is not None
    # use_cache=False — NO parquet file should have been written
    assert not (tmp_path / "cache").exists() or not any(
        (tmp_path / "cache").glob("*.parquet")
    )


# ---------------------------------------------------------------------------
# score_test_frame
# ---------------------------------------------------------------------------

def test_score_returns_none_for_untrained_model():
    from daytrade.ml.meta import MetaLabelModel
    model = MetaLabelModel()
    # untrained — no feature_names, no pipeline
    out = score_test_frame(model, pd.DataFrame({"x": [1, 2]}))
    assert out is None
