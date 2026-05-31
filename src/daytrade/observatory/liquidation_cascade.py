"""Liquidation-cascade detector — proxied from public OHLCV.

A liquidation cascade in crypto perp markets prints a recognisable
1-minute footprint, even without authenticated forceOrders data:

  1. A large bearish body (close - open) measured in units of recent ATR.
  2. A volume spike well above the trailing average.
  3. A subsequent bar with a long *lower* wick on similar volume —
     forced sellers exhaust, makers/contrarians buy the bottom.

We classify each bar as one of:

  * ``QUIET``                — nothing unusual.
  * ``CASCADE_ACTIVE``       — large bearish body + volume spike,
                               still falling. Strategy guidance: do NOT
                               buy a knife.
  * ``CASCADE_EXHAUSTION``   — long lower wick + volume spike AFTER a
                               cascade-active bar. Strategy guidance:
                               candidate mean-reversion entry.

This is a *proxy*. Without the futures forceOrders feed (auth-gated) we
cannot see liquidations directly. But the proxy is computable from data
the bot already has, costs no extra API calls, and is testable.

PAPER / SIMULATION ONLY. No orders. No wallets.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from ..models import OHLCV


class CascadeState(str, Enum):
    QUIET = "QUIET"
    CASCADE_ACTIVE = "CASCADE_ACTIVE"
    CASCADE_EXHAUSTION = "CASCADE_EXHAUSTION"


@dataclass(frozen=True)
class CascadeReading:
    state: CascadeState
    body_atr_mult: float        # bar body magnitude in ATR units (signed)
    volume_ratio: float          # bar volume / trailing mean volume
    lower_wick_ratio: float      # lower wick / total bar range
    reason: str                  # short human-readable explanation


def _atr(candles: List[OHLCV], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs: List[float] = []
    for i in range(len(candles) - period, len(candles)):
        prev_close = candles[i - 1].close
        c = candles[i]
        tr = max(c.high - c.low,
                 abs(c.high - prev_close),
                 abs(c.low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def _trailing_volume_mean(candles: List[OHLCV], window: int) -> Optional[float]:
    # Exclude the current bar so the ratio is a true z-against-history.
    if len(candles) < window + 1:
        return None
    tail = candles[-(window + 1):-1]
    if not tail:
        return None
    return sum(c.volume for c in tail) / len(tail)


def detect_cascade(
    candles: List[OHLCV],
    *,
    atr_period: int = 14,
    volume_window: int = 20,
    body_atr_threshold: float = 2.0,
    volume_spike_ratio: float = 3.0,
    exhaustion_wick_ratio: float = 0.55,
) -> CascadeReading:
    """Classify the most recent bar.

    Parameters reflect literature-typical defaults:

    - ``body_atr_threshold`` 2.0: a 2-ATR bar is roughly a 2.3-sigma move,
      uncommon enough to take seriously but frequent enough to study.
    - ``volume_spike_ratio`` 3.0: cascades typically print 3-10x volume.
    - ``exhaustion_wick_ratio`` 0.55: the lower wick must be more than half
      the bar's total range to be called exhaustion.
    """
    if len(candles) < max(atr_period + 1, volume_window + 1, 2):
        return CascadeReading(
            state=CascadeState.QUIET, body_atr_mult=0.0,
            volume_ratio=0.0, lower_wick_ratio=0.0,
            reason="insufficient history",
        )

    atr = _atr(candles, period=atr_period)
    vol_mean = _trailing_volume_mean(candles, window=volume_window)
    if atr is None or atr <= 0 or vol_mean is None or vol_mean <= 0:
        return CascadeReading(
            state=CascadeState.QUIET, body_atr_mult=0.0,
            volume_ratio=0.0, lower_wick_ratio=0.0,
            reason="atr or volume baseline unavailable",
        )

    last = candles[-1]
    prev = candles[-2]

    body = last.close - last.open
    body_atr_mult = body / atr
    rng = max(1e-12, last.high - last.low)
    lower_wick = min(last.open, last.close) - last.low
    lower_wick_ratio = max(0.0, lower_wick / rng)
    volume_ratio = last.volume / vol_mean

    # --- CASCADE_ACTIVE: heavy down body + volume spike ----------------
    if (body_atr_mult <= -body_atr_threshold
            and volume_ratio >= volume_spike_ratio):
        return CascadeReading(
            state=CascadeState.CASCADE_ACTIVE,
            body_atr_mult=body_atr_mult,
            volume_ratio=volume_ratio,
            lower_wick_ratio=lower_wick_ratio,
            reason=(f"down body {body_atr_mult:.2f} ATR with "
                    f"{volume_ratio:.1f}x volume"),
        )

    # --- CASCADE_EXHAUSTION: long lower wick + volume spike immediately
    # after a cascade-active bar.
    prev_body = prev.close - prev.open
    prev_atr_mult = prev_body / atr  # uses the same atr baseline
    if (prev_atr_mult <= -body_atr_threshold
            and lower_wick_ratio >= exhaustion_wick_ratio
            and volume_ratio >= max(1.0, volume_spike_ratio * 0.5)):
        return CascadeReading(
            state=CascadeState.CASCADE_EXHAUSTION,
            body_atr_mult=body_atr_mult,
            volume_ratio=volume_ratio,
            lower_wick_ratio=lower_wick_ratio,
            reason=(f"lower wick {lower_wick_ratio:.0%} of range after "
                    f"{prev_atr_mult:.2f}-ATR drop"),
        )

    return CascadeReading(
        state=CascadeState.QUIET,
        body_atr_mult=body_atr_mult,
        volume_ratio=volume_ratio,
        lower_wick_ratio=lower_wick_ratio,
        reason="no cascade footprint",
    )


def cascade_blocks_buy(reading: CascadeReading) -> bool:
    """During an active cascade, blocking new longs is the *minimum* prudence."""
    return reading.state is CascadeState.CASCADE_ACTIVE


def cascade_supports_mean_reversion_buy(reading: CascadeReading) -> bool:
    """Exhaustion bar is a candidate mean-reversion buy (not a guarantee)."""
    return reading.state is CascadeState.CASCADE_EXHAUSTION
