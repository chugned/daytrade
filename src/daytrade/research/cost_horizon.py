"""Cost × horizon sweep helpers — P5-3.

The cascade × meta-gate research (P4-1, P5-2) found:
- The signal direction is real (5/6 symbols positive gross at 30m).
- All net values are negative against the 24 bp retail cost.
- The union (cascade-override) gate beats meta-gate alone by 0.06-1.11 bps —
  consistent, but too small to flip net-positive.

P5-3 asks the strategic question those negative results expose:
**is there ANY combination of symbol, horizon, gate strictness, and
cost tier that produces a net-positive trade strategy on held-out data?**

This module is pure helpers around ``SliceMetrics``:
- ``recompute_net``: post-hoc swap the cost-tier subtraction (no
  retraining needed — gross is the only thing the model produces).
- ``find_winners``: filter cells by ``net > min_net`` and ``n >= min_n``,
  ordered by net descending.

The runner script ``scripts/sweep_cost_horizon.py`` iterates the matrix
and feeds cells through these. Read-only.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, List, Optional, Tuple

from .cascade_meta_interaction import SliceMetrics


@dataclass(frozen=True)
class Winner:
    """One cell of the sweep matrix that cleared the winners filter."""

    symbol: str
    horizon_minutes: int
    gate_multiple: float
    cost_bps: float
    slice: str
    n: int
    gross_bps: float
    net_bps: float


# (symbol, horizon, gate_multiple, cost_bps, slice_name, metrics)
Cell = Tuple[str, int, float, float, str, SliceMetrics]


def recompute_net(metric: SliceMetrics, *,
                  round_trip_cost_bps: float) -> SliceMetrics:
    """Return a copy of ``metric`` with ``mean_return_net_bps`` reset to
    ``mean_return_bps - round_trip_cost_bps``. Useful for slicing the
    cost dimension post-hoc without re-running the analyzer.

    Zero-event slices (where ``mean_return_bps`` is ``None``) pass
    through unchanged — there's no gross to subtract from.
    """
    if round_trip_cost_bps < 0:
        raise ValueError(
            f"round_trip_cost_bps must be >= 0, got {round_trip_cost_bps}"
        )
    if metric.mean_return_bps is None:
        return metric
    new_net = metric.mean_return_bps - round_trip_cost_bps
    return replace(metric, mean_return_net_bps=new_net)


def find_winners(cells: Iterable[Cell], *,
                 min_n: int = 30,
                 min_net: float = 0.0) -> List[Winner]:
    """Filter sweep cells to those clearing ``net > min_net`` with
    ``n >= min_n`` events. Sorted by net descending (best first).

    ``min_net`` defaults to 0 ("net-positive winners"). Pass a negative
    value to get the "near-winners" view — useful when zero cells are
    actually positive and you want to know how close the strategy got.
    """
    out: List[Winner] = []
    for symbol, horizon, gate_mult, cost, slice_name, m in cells:
        if m.n < min_n:
            continue
        if m.mean_return_net_bps is None:
            continue
        # Inclusive threshold: net == min_net qualifies. So min_net=0
        # treats break-even as a winner; min_net=-5 includes cells with
        # net in [-5, ∞) — useful for the "how close are we?" view.
        if m.mean_return_net_bps < min_net:
            continue
        out.append(Winner(
            symbol=symbol,
            horizon_minutes=horizon,
            gate_multiple=gate_mult,
            cost_bps=cost,
            slice=slice_name,
            n=m.n,
            gross_bps=m.mean_return_bps if m.mean_return_bps is not None else 0.0,
            net_bps=m.mean_return_net_bps,
        ))
    out.sort(key=lambda w: w.net_bps, reverse=True)
    return out
