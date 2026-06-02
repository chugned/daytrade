"""Does the meta-gate, when combined with CASCADE_EXHAUSTION, clear cost?

The CASCADE_EXHAUSTION fingerprint is already in the meta-model's
feature set (``features/pipeline.py:170–171``). What this module
quantifies is the *interaction*: among the bars the live gate would
*allow* a trade, are the cascade-exhaustion ones systematically
better — by enough to clear the 24 bp round-trip retail cost that
the cross-asset edge sweep showed is the binding constraint?

Pure analysis, no live state, no orders. Takes pre-computed
columns and returns per-slice metrics so the runner script and the
unit tests share one implementation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd


@dataclass(frozen=True)
class SliceMetrics:
    """Summary statistics for one slice of the held-out window."""

    n: int
    win_rate: Optional[float]            # fraction of trades with meta_label == 1
    mean_return_bps: Optional[float]     # gross
    median_return_bps: Optional[float]
    mean_return_net_bps: Optional[float] # gross minus configured round-trip cost
    sharpe_like: Optional[float]         # mean / std (sqrt(n) NOT applied)


_SLICES = ("all", "cascade_exhaustion", "meta_gated", "cascade_and_gated")


def analyze_cascade_meta_interaction(
    *,
    cascade_exhaustion: pd.Series,
    meta_label: pd.Series,
    meta_proba: pd.Series,
    forward_return_bps: pd.Series,
    base_win_rate: float,
    gate_multiple: float,
    round_trip_cost_bps: float,
) -> Dict[str, SliceMetrics]:
    """Compute per-slice metrics on a held-out evaluation window.

    Parameters
    ----------
    cascade_exhaustion
        0/1 column from the FeaturePipeline (``features/pipeline.py``).
    meta_label
        0/1 ground truth from ``triple_barrier_label`` (the meta-target).
    meta_proba
        The held-out model's predicted P(win) per row.
    forward_return_bps
        Forward return per row over the same horizon as the label.
    base_win_rate
        Training-set base rate. ``floor = base_win_rate * gate_multiple``.
    gate_multiple
        The live ``meta_label_edge_multiple`` (default 2.0).
    round_trip_cost_bps
        Retail-tier fees + slippage round-trip cost; subtracted from
        the gross mean to give ``mean_return_net_bps``.

    Returns
    -------
    Dict[str, SliceMetrics]
        Keys: ``all``, ``cascade_exhaustion``, ``meta_gated``,
        ``cascade_and_gated``.
    """
    # ----- input validation -----------------------------------------------
    n = len(cascade_exhaustion)
    for name, series in (
        ("meta_label", meta_label),
        ("meta_proba", meta_proba),
        ("forward_return_bps", forward_return_bps),
    ):
        if len(series) != n:
            raise ValueError(
                f"length mismatch: cascade_exhaustion has {n} rows, "
                f"{name} has {len(series)}"
            )
    if round_trip_cost_bps < 0:
        raise ValueError(
            f"round_trip_cost_bps must be >= 0, got {round_trip_cost_bps}"
        )

    # ----- slice masks ----------------------------------------------------
    gate_floor = base_win_rate * gate_multiple
    cascade_mask = cascade_exhaustion.astype(bool)
    gated_mask = meta_proba > gate_floor
    masks = {
        "all": pd.Series(True, index=cascade_exhaustion.index),
        "cascade_exhaustion": cascade_mask,
        "meta_gated": gated_mask,
        "cascade_and_gated": cascade_mask & gated_mask,
    }

    out: Dict[str, SliceMetrics] = {}
    for slice_name in _SLICES:
        mask = masks[slice_name]
        out[slice_name] = _slice_metrics(
            mask=mask,
            meta_label=meta_label,
            forward_return_bps=forward_return_bps,
            round_trip_cost_bps=round_trip_cost_bps,
        )
    return out


def _slice_metrics(
    *,
    mask: pd.Series,
    meta_label: pd.Series,
    forward_return_bps: pd.Series,
    round_trip_cost_bps: float,
) -> SliceMetrics:
    sub_labels = meta_label[mask]
    sub_returns = forward_return_bps[mask]
    n = len(sub_returns)
    if n == 0:
        return SliceMetrics(
            n=0,
            win_rate=None,
            mean_return_bps=None,
            median_return_bps=None,
            mean_return_net_bps=None,
            sharpe_like=None,
        )
    win_rate = float(sub_labels.mean()) if len(sub_labels) else None
    mean = float(sub_returns.mean())
    median = float(sub_returns.median())
    net = mean - round_trip_cost_bps
    std = float(sub_returns.std(ddof=1)) if n > 1 else None
    sharpe_like = (mean / std) if (std is not None and std > 0 and not math.isnan(std)) else None
    return SliceMetrics(
        n=n,
        win_rate=win_rate,
        mean_return_bps=mean,
        median_return_bps=median,
        mean_return_net_bps=net,
        sharpe_like=sharpe_like,
    )
