"""Tests for the cascade × meta-gate interaction analysis.

This module answers P4-1: given that ``cascade_active`` and
``cascade_exhaustion`` are already in the meta-model's feature set
(features/pipeline.py:170–171), do the meta-model's *gated*
predictions on cascade-exhaustion bars actually outperform the
gated predictions overall — by enough to clear retail-tier
round-trip costs (24 bps)?

The analysis function ``analyze_cascade_meta_interaction`` is the
testable unit. It takes pre-computed feature/label/proba/return
columns (so the test doesn't have to spin up an sklearn model) and
returns per-slice metrics that the runner script then aggregates
across symbols.

A slice is one of:
- ``all``                  — every bar in the held-out window
- ``cascade_exhaustion``   — bars where the detector flagged exhaustion
- ``meta_gated``           — bars the current meta-gate would allow
- ``cascade_and_gated``    — intersection of the two
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from daytrade.research.cascade_meta_interaction import (
    SliceMetrics,
    analyze_cascade_meta_interaction,
)


# ---------------------------------------------------------------------------
# Synthetic fixture
# ---------------------------------------------------------------------------

def _fixture(n_rows=200, base_win_rate=0.55, exhaustion_win_rate=0.75,
             cost_bps=24.0, seed=0):
    """Build a deterministic test frame.

    20% of rows have cascade_exhaustion=1. On those rows the actual
    win rate is ``exhaustion_win_rate``; on the other rows it's
    ``base_win_rate``. ``meta_proba`` is set to match the win rate
    of the row's slice so the gate cleanly separates winners.
    """
    rng = np.random.default_rng(seed)
    idx = pd.RangeIndex(n_rows)
    cascade = pd.Series(0, index=idx, dtype=int)
    n_exh = n_rows // 5
    exh_idx = rng.choice(n_rows, size=n_exh, replace=False)
    cascade.iloc[exh_idx] = 1

    labels = pd.Series(0, index=idx, dtype=int)
    proba = pd.Series(0.0, index=idx, dtype=float)
    returns = pd.Series(0.0, index=idx, dtype=float)
    for i in range(n_rows):
        if cascade.iloc[i] == 1:
            win = rng.random() < exhaustion_win_rate
            proba.iloc[i] = 0.80  # high proba — gate will allow
            returns.iloc[i] = 60.0 if win else -40.0
        else:
            win = rng.random() < base_win_rate
            proba.iloc[i] = 0.55
            returns.iloc[i] = 25.0 if win else -25.0
        labels.iloc[i] = 1 if win else 0
    return cascade, labels, proba, returns


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

def test_returns_slice_metrics_for_each_named_slice():
    cascade, labels, proba, returns = _fixture()
    result = analyze_cascade_meta_interaction(
        cascade_exhaustion=cascade,
        meta_label=labels,
        meta_proba=proba,
        forward_return_bps=returns,
        base_win_rate=0.55,
        gate_multiple=2.0,
        round_trip_cost_bps=24.0,
    )
    assert set(result.keys()) == {"all", "cascade_exhaustion",
                                  "meta_gated", "cascade_and_gated",
                                  "cascade_or_gated"}
    for slice_name, m in result.items():
        assert isinstance(m, SliceMetrics)


def test_all_slice_covers_every_row():
    cascade, labels, proba, returns = _fixture(n_rows=200)
    result = analyze_cascade_meta_interaction(
        cascade_exhaustion=cascade, meta_label=labels,
        meta_proba=proba, forward_return_bps=returns,
        base_win_rate=0.55, gate_multiple=2.0, round_trip_cost_bps=24.0,
    )
    assert result["all"].n == 200


def test_cascade_slice_size_matches_input():
    cascade, labels, proba, returns = _fixture(n_rows=200)
    # 1 in 5 rows have exhaustion flagged
    result = analyze_cascade_meta_interaction(
        cascade_exhaustion=cascade, meta_label=labels,
        meta_proba=proba, forward_return_bps=returns,
        base_win_rate=0.55, gate_multiple=2.0, round_trip_cost_bps=24.0,
    )
    assert result["cascade_exhaustion"].n == int(cascade.sum())


def test_gate_threshold_filters_by_proba():
    cascade, labels, proba, returns = _fixture(n_rows=200)
    # Gate threshold = 0.55 * 2.0 = 1.10 — above any proba (0.80 max)
    # so nothing passes
    result = analyze_cascade_meta_interaction(
        cascade_exhaustion=cascade, meta_label=labels,
        meta_proba=proba, forward_return_bps=returns,
        base_win_rate=0.55, gate_multiple=2.0, round_trip_cost_bps=24.0,
    )
    # With gate_multiple=2.0, floor = 1.10 -> NO row should be gated.
    assert result["meta_gated"].n == 0
    assert result["cascade_and_gated"].n == 0


def test_relaxed_gate_lets_cascade_rows_through():
    cascade, labels, proba, returns = _fixture(n_rows=200, seed=1)
    # With multiple = 1.0, floor = 0.55. Cascade rows have proba=0.80
    # (pass), base rows have proba=0.55 (fail — strictly greater).
    result = analyze_cascade_meta_interaction(
        cascade_exhaustion=cascade, meta_label=labels,
        meta_proba=proba, forward_return_bps=returns,
        base_win_rate=0.55, gate_multiple=1.0, round_trip_cost_bps=24.0,
    )
    n_cascade = int(cascade.sum())
    assert result["meta_gated"].n == n_cascade
    assert result["cascade_and_gated"].n == n_cascade


def test_exhaustion_slice_has_higher_win_rate_than_base():
    cascade, labels, proba, returns = _fixture(
        n_rows=400, base_win_rate=0.50, exhaustion_win_rate=0.75, seed=2
    )
    result = analyze_cascade_meta_interaction(
        cascade_exhaustion=cascade, meta_label=labels,
        meta_proba=proba, forward_return_bps=returns,
        base_win_rate=0.50, gate_multiple=2.0, round_trip_cost_bps=24.0,
    )
    exh_wr = result["cascade_exhaustion"].win_rate
    all_wr = result["all"].win_rate
    assert exh_wr > all_wr + 0.10, (
        f"cascade rows ({exh_wr:.2f}) should clearly beat baseline "
        f"({all_wr:.2f}) on this fixture"
    )


def test_net_of_cost_subtracts_round_trip():
    """``mean_return_net_bps`` is the gross mean minus the configured
    round-trip cost. Sign + magnitude must match arithmetic."""
    cascade, labels, proba, returns = _fixture(n_rows=200, seed=3)
    result = analyze_cascade_meta_interaction(
        cascade_exhaustion=cascade, meta_label=labels,
        meta_proba=proba, forward_return_bps=returns,
        base_win_rate=0.55, gate_multiple=2.0, round_trip_cost_bps=24.0,
    )
    all_slice = result["all"]
    assert all_slice.mean_return_net_bps == pytest.approx(
        all_slice.mean_return_bps - 24.0
    )


def test_zero_event_slice_returns_none_metrics_not_crash():
    """When a slice (e.g. ``meta_gated`` with too-strict threshold) has
    zero rows, win_rate / mean / median / sharpe must be ``None``,
    NOT NaN or a divide-by-zero. ``n`` is still reported as 0."""
    idx = pd.RangeIndex(50)
    cascade = pd.Series(0, index=idx, dtype=int)  # no exhaustion rows
    labels = pd.Series(0, index=idx, dtype=int)
    proba = pd.Series(0.30, index=idx, dtype=float)  # below any gate floor
    returns = pd.Series(0.0, index=idx, dtype=float)
    result = analyze_cascade_meta_interaction(
        cascade_exhaustion=cascade, meta_label=labels,
        meta_proba=proba, forward_return_bps=returns,
        base_win_rate=0.55, gate_multiple=2.0, round_trip_cost_bps=24.0,
    )
    for slice_name in ("cascade_exhaustion", "meta_gated", "cascade_and_gated"):
        m = result[slice_name]
        assert m.n == 0
        assert m.win_rate is None
        assert m.mean_return_bps is None
        assert m.mean_return_net_bps is None


def test_union_slice_size_equals_inclusion_exclusion():
    """|A ∪ B| = |A| + |B| − |A ∩ B|. Sanity check that the union slice
    is built the way the doc claims (admit cascade OR meta-gated)."""
    cascade, labels, proba, returns = _fixture(n_rows=200, seed=4)
    result = analyze_cascade_meta_interaction(
        cascade_exhaustion=cascade, meta_label=labels,
        meta_proba=proba, forward_return_bps=returns,
        base_win_rate=0.55, gate_multiple=1.0, round_trip_cost_bps=24.0,
    )
    expected = (result["cascade_exhaustion"].n
                + result["meta_gated"].n
                - result["cascade_and_gated"].n)
    assert result["cascade_or_gated"].n == expected


def test_union_dominates_meta_gated_when_cascade_lifts_baseline():
    """When cascade rows have a meaningfully higher win rate than the
    rest, ``cascade_or_gated`` should have a mean return ≥ ``meta_gated``
    — the override pulls more good bars in. This is exactly the
    pattern P5-2 is asking about."""
    cascade, labels, proba, returns = _fixture(
        n_rows=400,
        base_win_rate=0.50,
        exhaustion_win_rate=0.80,  # cascade rows clearly better
        seed=5,
    )
    # Gate so meta-gated includes some non-cascade rows
    result = analyze_cascade_meta_interaction(
        cascade_exhaustion=cascade, meta_label=labels,
        meta_proba=proba, forward_return_bps=returns,
        base_win_rate=0.50, gate_multiple=1.0, round_trip_cost_bps=24.0,
    )
    union_mean = result["cascade_or_gated"].mean_return_bps
    gated_mean = result["meta_gated"].mean_return_bps
    assert union_mean is not None and gated_mean is not None
    # Union should have at least as many events as meta-gated (it's a
    # superset by construction) and a mean return ≥ baseline.
    assert result["cascade_or_gated"].n >= result["meta_gated"].n
    # The union shouldn't be a pure wash either way — the cascade boost
    # should at minimum keep the mean above what the gate alone produces
    # when the cascade signal is strong.
    assert union_mean >= gated_mean - 5.0  # tolerance for fixture noise


def test_rejects_misaligned_series_lengths():
    with pytest.raises(ValueError, match="length"):
        analyze_cascade_meta_interaction(
            cascade_exhaustion=pd.Series([0, 1]),
            meta_label=pd.Series([0, 1, 0]),
            meta_proba=pd.Series([0.5, 0.5, 0.5]),
            forward_return_bps=pd.Series([1.0, 2.0, 3.0]),
            base_win_rate=0.55, gate_multiple=2.0, round_trip_cost_bps=24.0,
        )


def test_rejects_invalid_cost():
    cascade, labels, proba, returns = _fixture(n_rows=50)
    with pytest.raises(ValueError, match="cost"):
        analyze_cascade_meta_interaction(
            cascade_exhaustion=cascade, meta_label=labels,
            meta_proba=proba, forward_return_bps=returns,
            base_win_rate=0.55, gate_multiple=2.0, round_trip_cost_bps=-1.0,
        )
