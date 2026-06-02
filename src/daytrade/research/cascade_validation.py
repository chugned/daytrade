"""Cross-asset validation of the CASCADE_EXHAUSTION forward-return edge.

The overnight research (``docs/RESEARCH-90D-FINDINGS.md``) found that
CASCADE_EXHAUSTION bars on SOL at the 30-min forward-return horizon had
a +9.7 bp mean return on n=101 events vs a −0.1 bp baseline. Other
symbols showed mixed signs. This module quantifies the edge per symbol
on whatever candle history the caller provides.

**Read-only research code.** Imports nothing that could place an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..models import OHLCV
from ..observatory.liquidation_cascade import CascadeState, detect_cascade


@dataclass(frozen=True)
class SymbolValidation:
    """Per-symbol result of one validation run."""

    symbol: str
    n_bars: int
    exhaustion_count: int
    mean_forward_return_bps: Optional[float]   # None if no events
    median_forward_return_bps: Optional[float]
    win_rate: Optional[float]                  # fraction with positive return
    baseline_mean_forward_return_bps: Optional[float]  # all bars, for compare
    horizon_minutes: int


def _forward_return_bps(candles: List[OHLCV], i: int,
                        horizon_bars: int) -> Optional[float]:
    """Forward return over the next ``horizon_bars`` bars, in bps."""
    j = i + horizon_bars
    if j >= len(candles):
        return None
    px_now = candles[i].close
    px_then = candles[j].close
    if px_now <= 0:
        return None
    return float((px_then - px_now) / px_now * 10_000.0)


#: detect_cascade only needs ~22 bars of context (max of ATR-14 and
#: vol-baseline window=20, +1 for the prior bar in exhaustion check).
#: A 60-bar trailing window is comfortable headroom and keeps the sweep
#: O(N · 60) instead of O(N²).
_DETECTOR_LOOKBACK_BARS = 60


def _validate_one(symbol: str, candles: List[OHLCV],
                  horizon_minutes: int) -> SymbolValidation:
    """Walk the bar series and aggregate forward returns at exhaustion bars."""
    horizon_bars = max(1, horizon_minutes)  # 1m bars assumed
    n = len(candles)
    exhaustion_returns: List[float] = []
    baseline_returns: List[float] = []
    lookback = _DETECTOR_LOOKBACK_BARS

    # Need enough lookback for the cascade detector (~22 bars warmup).
    for i in range(30, n - horizon_bars):
        # O(1) window slice instead of O(n) cumulative — see comment on
        # _DETECTOR_LOOKBACK_BARS for why 60 is enough.
        window = candles[max(0, i + 1 - lookback): i + 1]
        reading = detect_cascade(window)
        fwd = _forward_return_bps(candles, i, horizon_bars)
        if fwd is None:
            continue
        baseline_returns.append(fwd)
        if reading.state is CascadeState.CASCADE_EXHAUSTION:
            exhaustion_returns.append(fwd)

    def _mean(xs: List[float]) -> Optional[float]:
        return sum(xs) / len(xs) if xs else None

    def _median(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        ordered = sorted(xs)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[mid])
        return float((ordered[mid - 1] + ordered[mid]) / 2)

    def _win_rate(xs: List[float]) -> Optional[float]:
        return (sum(1 for x in xs if x > 0) / len(xs)) if xs else None

    return SymbolValidation(
        symbol=symbol,
        n_bars=n,
        exhaustion_count=len(exhaustion_returns),
        mean_forward_return_bps=_mean(exhaustion_returns),
        median_forward_return_bps=_median(exhaustion_returns),
        win_rate=_win_rate(exhaustion_returns),
        baseline_mean_forward_return_bps=_mean(baseline_returns),
        horizon_minutes=horizon_minutes,
    )


def validate_cascade_edge(
    candles_by_symbol: Dict[str, List[OHLCV]],
    *,
    horizon_minutes: int = 30,
) -> List[SymbolValidation]:
    """Validate the CASCADE_EXHAUSTION edge per symbol.

    Parameters
    ----------
    candles_by_symbol
        Mapping of ``symbol → list[OHLCV]`` (1-minute bars expected).
    horizon_minutes
        Forward-return horizon, in minutes (== number of 1m bars).

    Returns
    -------
    list[SymbolValidation]
        One entry per input symbol, in input order.
    """
    if not candles_by_symbol:
        raise ValueError("validate_cascade_edge: no symbols provided")
    if horizon_minutes <= 0:
        raise ValueError(
            f"horizon_minutes must be > 0, got {horizon_minutes}"
        )
    return [
        _validate_one(sym, candles, horizon_minutes)
        for sym, candles in candles_by_symbol.items()
    ]
