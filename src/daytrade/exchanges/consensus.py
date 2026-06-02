"""Consensus price engine.

Combines price ticks from several exchanges into one robust consensus price,
rejecting flash-crash / bad-print outliers along the way. Robustness matters:
a single exchange printing a spurious price must not move the whole pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import numpy as np

from ..config.schema import ConsensusConfig
from ..models import ConsensusPrice, ExchangeStatus, PriceTick
from ..runtime import get_logger

_log = get_logger("consensus")

# Scale factor making MAD a consistent estimator of std-dev for normal data.
_MAD_TO_SIGMA = 1.4826


def _mad_outlier_mask(prices: np.ndarray, z_threshold: float) -> np.ndarray:
    """Return a boolean mask of *inliers* using a median/MAD robust z-score.

    Unlike a mean/std z-score, this does not let the outlier inflate the
    spread it is being measured against — so a flash-crash print cannot hide
    behind the variance it itself created.
    """
    if prices.size <= 2:
        return np.ones(prices.size, dtype=bool)
    median = float(np.median(prices))
    mad = float(np.median(np.abs(prices - median)))
    if mad == 0.0:
        # All-but-noise identical: flag only exact gross deviations.
        return np.abs(prices - median) <= max(median * 1e-6, 1e-9)
    robust_z = _MAD_TO_SIGMA * np.abs(prices - median) / mad
    return robust_z <= z_threshold


def compute_consensus(
    ticks: List[PriceTick],
    symbol: str,
    config: ConsensusConfig | None = None,
) -> ConsensusPrice:
    """Fuse ticks into a :class:`ConsensusPrice`.

    Steps:
      1. drop sources reporting ``DOWN``
      2. reject price outliers via the MAD robust z-score
      3. average the survivors
      4. flag ``degraded`` if too few sources remain or dispersion is high
    """
    config = config or ConsensusConfig()
    if not ticks:
        raise ValueError("compute_consensus requires at least one tick")

    healthy = [t for t in ticks if t.status is not ExchangeStatus.DOWN]
    down = [t.exchange for t in ticks if t.status is ExchangeStatus.DOWN]

    if not healthy:
        raise ValueError("all sources are DOWN — no consensus possible")

    prices = np.array([t.price for t in healthy], dtype=float)
    inlier_mask = _mad_outlier_mask(prices, config.outlier_z_threshold)

    used = [healthy[i] for i in range(len(healthy)) if inlier_mask[i]]
    rejected_outliers = [healthy[i].exchange for i in range(len(healthy)) if not inlier_mask[i]]
    if not used:  # pathological — keep everything rather than crash
        used = healthy
        rejected_outliers = []

    accepted_prices = np.array([t.price for t in used], dtype=float)
    consensus = float(np.mean(accepted_prices))

    lo, hi = float(accepted_prices.min()), float(accepted_prices.max())
    median = float(np.median(accepted_prices))
    dispersion = (hi - lo) / median if median > 0 else 0.0

    degraded = len(used) < config.min_sources or dispersion > config.max_dispersion or bool(down)
    if rejected_outliers:
        _log.warning("consensus rejected outlier sources: %s", rejected_outliers)
    if degraded:
        _log.warning(
            "consensus DEGRADED (sources=%d dispersion=%.4f down=%s)",
            len(used),
            dispersion,
            down,
        )

    timestamps = [t.timestamp for t in used]
    ts = max(timestamps) if timestamps else datetime.now(timezone.utc)

    return ConsensusPrice(
        symbol=symbol,
        price=round(consensus, 2),
        timestamp=ts,
        sources_used=[t.exchange for t in used],
        sources_rejected=rejected_outliers + down,
        dispersion=round(dispersion, 6),
        degraded=degraded,
    )
