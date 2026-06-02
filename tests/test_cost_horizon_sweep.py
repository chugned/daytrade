"""Tests for the cost × horizon sweep helpers (P5-3).

The strategic question is: given the cascade-exhaustion signal is real
but tiny (10-22 bps gross), is there ANY combination of symbol,
forward horizon, gate strictness, and cost tier that gets the strategy
to net-positive on held-out data?

The sweep iterates (symbol × horizon × gate_multiple × cost_tier) and
emits a table of winners (net > 0 with non-trivial n). The pure helpers
are:

- ``recompute_net`` — post-hoc swap of the cost-tier subtraction
  (no retraining needed for cost changes).
- ``find_winners`` — filter slice metrics by (net > 0, n >= min_n).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from daytrade.research.cascade_meta_interaction import SliceMetrics
from daytrade.research.cost_horizon import (
    Winner,
    find_winners,
    recompute_net,
)


def _metric(n: int, gross: float, net: float, win_rate: float = 0.55) -> SliceMetrics:
    return SliceMetrics(
        n=n, win_rate=win_rate,
        mean_return_bps=gross, median_return_bps=gross,
        mean_return_net_bps=net, sharpe_like=0.1,
    )


# ---------------------------------------------------------------------------
# recompute_net
# ---------------------------------------------------------------------------

def test_recompute_net_subtracts_new_cost_from_gross():
    m = _metric(n=100, gross=20.0, net=-4.0)  # net was 24bp cost
    repriced = recompute_net(m, round_trip_cost_bps=10.0)
    assert repriced.mean_return_net_bps == pytest.approx(10.0)
    # Gross is preserved
    assert repriced.mean_return_bps == pytest.approx(20.0)


def test_recompute_net_passes_through_zero_event_slices():
    m = SliceMetrics(n=0, win_rate=None, mean_return_bps=None,
                     median_return_bps=None, mean_return_net_bps=None,
                     sharpe_like=None)
    repriced = recompute_net(m, round_trip_cost_bps=10.0)
    assert repriced.mean_return_net_bps is None
    assert repriced.n == 0


def test_recompute_net_with_zero_cost_is_identity_for_gross():
    m = _metric(n=50, gross=15.0, net=-9.0)
    repriced = recompute_net(m, round_trip_cost_bps=0.0)
    assert repriced.mean_return_net_bps == pytest.approx(15.0)


def test_recompute_net_rejects_negative_cost():
    m = _metric(n=10, gross=5.0, net=-19.0)
    with pytest.raises(ValueError, match="cost"):
        recompute_net(m, round_trip_cost_bps=-1.0)


# ---------------------------------------------------------------------------
# find_winners
# ---------------------------------------------------------------------------

def test_find_winners_returns_only_net_positive_cells():
    cells = [
        # (symbol, horizon, gate_mult, cost, slice, metrics)
        ("BTC", 30, 2.0, 24.0, "meta_gated", _metric(100, 25, +1.0)),
        ("BTC", 30, 2.0, 24.0, "cascade_exhaustion", _metric(50, 20, -4.0)),
        ("SOL", 60, 3.0, 14.0, "cascade_or_gated", _metric(80, 18, +4.0)),
        ("ETH", 30, 2.0, 24.0, "meta_gated", _metric(100, 10, -14.0)),
    ]
    winners = find_winners(cells, min_n=30)
    assert len(winners) == 2
    names = {(w.symbol, w.slice) for w in winners}
    assert names == {("BTC", "meta_gated"), ("SOL", "cascade_or_gated")}
    # Each winner has the metadata
    for w in winners:
        assert isinstance(w, Winner)
        assert w.net_bps > 0
        assert w.n >= 30


def test_find_winners_filters_low_event_count():
    cells = [
        ("X", 30, 2.0, 24.0, "cascade_and_gated", _metric(5, 100, +76.0)),
        ("Y", 30, 2.0, 24.0, "meta_gated", _metric(50, 25, +1.0)),
    ]
    winners = find_winners(cells, min_n=30)
    assert len(winners) == 1
    assert winners[0].symbol == "Y"


def test_find_winners_skips_none_metrics():
    cells = [
        ("X", 30, 2.0, 24.0, "cascade_and_gated",
         SliceMetrics(n=0, win_rate=None, mean_return_bps=None,
                      median_return_bps=None, mean_return_net_bps=None,
                      sharpe_like=None)),
    ]
    assert find_winners(cells, min_n=30) == []


def test_find_winners_orders_by_net_descending():
    cells = [
        ("A", 30, 2.0, 24.0, "meta_gated", _metric(50, 25, +1.0)),
        ("B", 30, 2.0, 24.0, "meta_gated", _metric(50, 30, +6.0)),
        ("C", 30, 2.0, 24.0, "meta_gated", _metric(50, 27, +3.0)),
    ]
    winners = find_winners(cells, min_n=30)
    nets = [w.net_bps for w in winners]
    assert nets == sorted(nets, reverse=True)
    assert winners[0].symbol == "B"


# ---------------------------------------------------------------------------
# Closest-to-breakeven (still useful when zero winners exist)
# ---------------------------------------------------------------------------

def test_find_winners_with_negative_threshold_reports_closest():
    """Allows ``min_net=-5`` to find 'closest cells' when nothing is
    truly positive — answering 'how close is the strategy to viable?'"""
    cells = [
        ("A", 30, 2.0, 24.0, "meta_gated", _metric(50, 20, -4.0)),
        ("B", 30, 2.0, 24.0, "meta_gated", _metric(50, 19, -5.0)),
        ("C", 30, 2.0, 24.0, "meta_gated", _metric(50, 15, -9.0)),
    ]
    near = find_winners(cells, min_n=30, min_net=-5.0)
    assert {w.symbol for w in near} == {"A", "B"}
