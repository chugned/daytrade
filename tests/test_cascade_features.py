"""Tests for the cascade-as-feature additions to the FeaturePipeline.

These verify three things:

1. The features actually appear in the pipeline output.
2. The cascade_active / cascade_exhaustion 0/1 flags fire on the same
   synthetic price footprints the dedicated detector recognises — i.e.
   the feature and the gate-module are computing the same thing.
3. The features are CAUSAL — truncating the input series does not change
   the feature value at any retained bar. (The leakage test in
   tests/test_leakage.py covers the whole pipeline; this file gives the
   cascade columns their own targeted check.)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np

from daytrade.features.pipeline import (
    FeaturePipeline,
    feature_columns,
)
from daytrade.models import OHLCV

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candle(
    i: int, o: float, c: float, vol: float, h: float | None = None, l: float | None = None
) -> OHLCV:
    hi = h if h is not None else max(o, c) * 1.0005
    lo = l if l is not None else min(o, c) * 0.9995
    return OHLCV(
        symbol="BTCUSDT",
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
        open=o,
        high=hi,
        low=lo,
        close=c,
        volume=vol,
    )


def _calm(n: int = 40, *, price: float = 100.0, volume: float = 1000.0) -> List[OHLCV]:
    out: List[OHLCV] = []
    p = price
    for i in range(n):
        p_next = p * (1.0 + (0.0002 if i % 2 == 0 else -0.0002))
        out.append(_candle(i, p, p_next, volume))
        p = p_next
    return out


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_cascade_columns_present_in_feature_columns():
    cols = feature_columns()
    for name in (
        "cascade_body_atr",
        "cascade_vol_spike",
        "cascade_lower_wick",
        "cascade_active",
        "cascade_exhaustion",
    ):
        assert name in cols, f"{name} missing from feature_columns()"


def test_pipeline_emits_cascade_columns():
    pipe = FeaturePipeline()
    feats = pipe.transform(_calm(80))
    for name in (
        "cascade_body_atr",
        "cascade_vol_spike",
        "cascade_lower_wick",
        "cascade_active",
        "cascade_exhaustion",
    ):
        assert name in feats.columns


# ---------------------------------------------------------------------------
# Quiet baseline: flags are zero (or NaN during warmup) but never 1.
# ---------------------------------------------------------------------------


def test_quiet_baseline_has_no_active_or_exhaustion_flags():
    pipe = FeaturePipeline()
    feats = pipe.transform(_calm(100))
    # After warmup (ATR + vol baseline both need ~20 bars) the active /
    # exhaustion flags must be 0, never 1.
    tail = feats.iloc[30:]
    assert (tail["cascade_active"] == 0.0).all()
    assert (tail["cascade_exhaustion"] == 0.0).all()


# ---------------------------------------------------------------------------
# Active cascade footprint
# ---------------------------------------------------------------------------


def test_large_down_body_with_volume_spike_fires_active_flag():
    base = _calm(40, price=100.0, volume=1000.0)
    last = base[-1].close
    cascade_bar = _candle(
        len(base),
        o=last,
        c=last * 0.97,
        vol=8000.0,
        h=last * 1.0005,
        l=last * 0.969,
    )
    pipe = FeaturePipeline()
    feats = pipe.transform(base + [cascade_bar])
    assert feats["cascade_active"].iloc[-1] == 1.0
    assert feats["cascade_body_atr"].iloc[-1] < -2.0
    assert feats["cascade_vol_spike"].iloc[-1] >= 3.0


def test_active_flag_off_when_only_volume_spikes():
    base = _calm(40, price=100.0, volume=1000.0)
    last = base[-1].close
    bar = _candle(  # big volume, tiny body
        len(base),
        o=last,
        c=last * 0.9998,
        vol=20000.0,
    )
    pipe = FeaturePipeline()
    feats = pipe.transform(base + [bar])
    assert feats["cascade_active"].iloc[-1] == 0.0


def test_active_flag_off_when_only_body_drops():
    base = _calm(40, price=100.0, volume=1000.0)
    last = base[-1].close
    bar = _candle(  # big body, baseline volume
        len(base),
        o=last,
        c=last * 0.97,
        vol=1100.0,
        h=last * 1.0005,
        l=last * 0.969,
    )
    pipe = FeaturePipeline()
    feats = pipe.transform(base + [bar])
    assert feats["cascade_active"].iloc[-1] == 0.0


# ---------------------------------------------------------------------------
# Exhaustion footprint (long lower wick AFTER a cascade-active bar)
# ---------------------------------------------------------------------------


def test_exhaustion_fires_when_long_wick_follows_cascade():
    base = _calm(40, price=100.0, volume=1000.0)
    last = base[-1].close
    cascade_bar = _candle(
        len(base),
        o=last,
        c=last * 0.97,
        vol=8000.0,
        h=last * 1.0005,
        l=last * 0.969,
    )
    px = cascade_bar.close
    exhaustion_bar = _candle(
        len(base) + 1,
        o=px * 1.000,
        c=px * 1.002,
        vol=6000.0,
        h=px * 1.003,
        l=px * 0.980,
    )
    pipe = FeaturePipeline()
    feats = pipe.transform(base + [cascade_bar, exhaustion_bar])
    assert feats["cascade_exhaustion"].iloc[-1] == 1.0


def test_exhaustion_does_not_fire_without_prior_cascade():
    base = _calm(40, price=100.0, volume=1000.0)
    last = base[-1].close
    # A long-wick bar without a prior cascade — must NOT flag exhaustion.
    bar = _candle(
        len(base),
        o=last,
        c=last * 1.001,
        vol=6000.0,
        h=last * 1.002,
        l=last * 0.985,
    )
    pipe = FeaturePipeline()
    feats = pipe.transform(base + [bar])
    assert feats["cascade_exhaustion"].iloc[-1] == 0.0


# ---------------------------------------------------------------------------
# Causality (no leakage on the cascade columns specifically)
# ---------------------------------------------------------------------------


def _continuing_calm(start_i: int, n: int, *, price: float, volume: float = 1000.0) -> List[OHLCV]:
    """Calm bars whose timestamps CONTINUE from ``start_i`` (no overlap)."""
    out: List[OHLCV] = []
    p = price
    for j in range(n):
        p_next = p * (1.0 + (0.0002 if j % 2 == 0 else -0.0002))
        out.append(_candle(start_i + j, p, p_next, volume))
        p = p_next
    return out


def test_cascade_columns_are_causal():
    """Truncating the candle history must not change cascade-feature
    values at retained bars."""
    base = _calm(80, price=100.0, volume=1000.0)
    last_px = base[-1].close
    cascade_bar = _candle(
        len(base),
        o=last_px,
        c=last_px * 0.97,
        vol=8000.0,
        h=last_px * 1.0005,
        l=last_px * 0.969,
    )
    after = _continuing_calm(len(base) + 1, n=20, price=cascade_bar.close, volume=1000.0)
    full = base + [cascade_bar] + after
    pipe = FeaturePipeline()
    full_feats = pipe.transform(full)
    truncate_len = len(base) + 1  # base + cascade bar only
    partial_feats = pipe.transform(full[:truncate_len])

    cols = [
        "cascade_body_atr",
        "cascade_vol_spike",
        "cascade_lower_wick",
        "cascade_active",
        "cascade_exhaustion",
    ]
    common_full = full_feats[cols].iloc[:truncate_len].dropna()
    common_partial = partial_feats[cols].loc[common_full.index].dropna()
    assert len(common_full) > 0
    assert np.array_equal(common_full.values, common_partial.values, equal_nan=False)
