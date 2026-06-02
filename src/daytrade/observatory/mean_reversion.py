"""Mean-reversion entry detector — the contrarian sibling of the trend follower.

The existing fusion engine is trend-following: it BUYs into established
direction and rides the move. Mean-reversion is the *opposite* archetype:
it BUYs into a sharp drop, expecting the move to revert toward a mean
within a short window. Both archetypes are documented to coexist in
intraday crypto (multiple SSRN papers; see QuantPedia summary linked
from docs/10X-RESEARCH-PLAN.md).

This module ships an entry-setup detector:

* :func:`detect_mean_reversion_setup` — given a recent candle window,
  returns a :class:`MeanReversionSetup` if and only if the canonical
  oversold-reversal pattern is present:

      1. Short-term sharp drop (e.g. ≥ 0.8% in last 15 min)
      2. RSI oversold (e.g. < 30)
      3. Volume spike (recent bar > rolling avg × multiplier)

The setup ships its own entry / stop / target geometry — tighter than
the trend follower's, because MR trades are short-horizon and don't
benefit from wide stops.

No live execution; this is just signal detection. The observer can
optionally route the setup into the paper broker — same path the
trend follower uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from ..models import OHLCV


@dataclass(frozen=True)
class MeanReversionSetup:
    """A long mean-reversion entry candidate."""

    entry: float
    stop: float
    target: float
    confidence: float  # 0..1, scales with how extreme the drop was
    reason: str
    drop_pct: float
    rsi: float
    volume_ratio: float


@dataclass
class MeanReversionConfig:
    """Tunable thresholds for the detector — sweep-validated before use."""

    # The drop the setup requires over the last `drop_lookback` bars.
    drop_pct: float = 0.008  # 0.8%
    drop_lookback: int = 15  # bars (minutes)

    # RSI gate.
    rsi_period: int = 14
    rsi_max: float = 30.0

    # Volume gate.
    volume_mult: float = 1.5
    volume_window: int = 20

    # Stop / target geometry (in fractions of current price).
    stop_buffer_frac: float = 0.002  # 0.2% below recent local low
    stop_lookback: int = 10
    target_lookback: int = 15  # midpoint to the recent high

    # How short the position is held (in bars / minutes).
    max_hold_bars: int = 30


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    """Simple RSI on a 1-D array. Returns NaN if not enough data."""
    if len(closes) < period + 1:
        return float("nan")
    diffs = np.diff(closes[-(period + 1) :])
    gains = np.where(diffs > 0, diffs, 0.0).mean()
    losses = np.where(diffs < 0, -diffs, 0.0).mean()
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def detect_mean_reversion_setup(
    candles: List[OHLCV],
    cfg: Optional[MeanReversionConfig] = None,
) -> Optional[MeanReversionSetup]:
    """Return a :class:`MeanReversionSetup` iff the oversold-reversal pattern fires.

    Returns ``None`` if any of the three conditions are not met.
    """
    cfg = cfg or MeanReversionConfig()
    need = (
        max(
            cfg.drop_lookback,
            cfg.volume_window,
            cfg.rsi_period + 1,
            cfg.target_lookback,
            cfg.stop_lookback,
        )
        + 1
    )
    if len(candles) < need:
        return None

    closes = np.array([c.close for c in candles], dtype=float)
    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    vols = np.array([c.volume for c in candles], dtype=float)

    last = closes[-1]
    ago = closes[-cfg.drop_lookback - 1]
    if ago <= 0:
        return None
    drop_pct = (last / ago) - 1.0
    if drop_pct > -cfg.drop_pct:
        return None  # not a big enough drop

    rsi = _rsi(closes, cfg.rsi_period)
    if not np.isfinite(rsi) or rsi > cfg.rsi_max:
        return None  # not oversold

    avg_vol = vols[-cfg.volume_window - 1 : -1].mean()
    if avg_vol <= 0:
        return None
    vol_ratio = vols[-1] / avg_vol
    if vol_ratio < cfg.volume_mult:
        return None  # no volume confirmation

    # Setup confirmed — build entry / stop / target.
    entry = last
    local_low = lows[-cfg.stop_lookback :].min()
    stop = local_low * (1.0 - cfg.stop_buffer_frac)
    target_level = highs[-cfg.target_lookback :].max()
    target = (entry + target_level) / 2.0

    # Confidence scales with the drop magnitude vs the threshold.
    confidence = float(min(1.0, abs(drop_pct) / (cfg.drop_pct * 2.0)))

    return MeanReversionSetup(
        entry=entry,
        stop=stop,
        target=target,
        confidence=confidence,
        reason=(
            f"oversold reversal: {drop_pct*100:+.2f}% in {cfg.drop_lookback}m, "
            f"RSI {rsi:.1f}, vol x{vol_ratio:.2f}"
        ),
        drop_pct=drop_pct,
        rsi=rsi,
        volume_ratio=vol_ratio,
    )
