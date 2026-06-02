"""Multi-timeframe trend filter — only trade with the higher-TF trend.

Background: the bot evaluates on 1-minute candles. The literature on retail
algo trading is unusually consistent on one finding — adding a *higher*
timeframe trend gate filters ~80% of false 1-minute signals. The classic
formulation is "trade in the direction of the daily / 4h trend on a 15m
setup"; for our 1m primary, we use 15m + 1h as the higher timeframes.

We do not need additional data fetches: the observer already pulls 240
one-minute candles per symbol per cycle (4 hours), which is enough to
resample into 16 fifteen-minute bars and 4 one-hour bars. The gate
computes the trend slope on each higher timeframe and refuses BUYs
whose direction disagrees with either.

Paper / simulation only — like every other gate this can only suppress
a trade, never create one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from ..models import OHLCV


@dataclass(frozen=True)
class MTFAlignmentResult:
    """Outcome of a multi-timeframe alignment check."""

    aligned: bool
    reason: str
    slope_15m: Optional[float] = None
    slope_1h: Optional[float] = None


def _resample(candles: List[OHLCV], rule: str) -> pd.DataFrame:
    """Resample a 1m-candle list into the requested timeframe (pandas rule)."""
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(
        {
            "ts": [c.timestamp for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    ).set_index("ts")
    out = (
        df.resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    return out


def _trend_slope(closes: pd.Series, window: int) -> float:
    """OLS slope of the last ``window`` closes, normalized by price.

    Same units as the rest of the codebase (see indicators.core.trend_slope):
    fractional change per bar.
    """
    if len(closes) < window:
        return 0.0
    seg = closes.iloc[-window:].to_numpy(dtype=float)
    if seg[-1] == 0 or not np.isfinite(seg[-1]):
        return 0.0
    x = np.arange(window, dtype=float)
    x_dev = x - x.mean()
    x_var = float((x_dev**2).sum())
    cov = float(((seg - seg.mean()) * x_dev).sum())
    slope = cov / x_var
    return slope / seg[-1]


def check_higher_tf_alignment(
    candles_1m: List[OHLCV],
    direction: str,
    min_slope: float = 0.0,
    window_15m: int = 12,
    window_1h: int = 4,
) -> MTFAlignmentResult:
    """Verify the 15m and 1h trends align with ``direction``.

    Defaults are sized for the observer's 240-bar / 4-hour 1m window: 12
    fifteen-minute bars (3 hours of context) and 4 hourly bars (the full
    window). The shorter 1h window is intentional — with only 4 hours of
    fetched history there is no longer one to give it.

    Args:
        candles_1m: list of 1-minute OHLCV candles. ~240 bars is plenty.
        direction: ``"buy"`` (require positive higher-TF slope) or
            ``"sell"`` (negative). Other values → ``aligned=True`` (no opinion).
        min_slope: absolute slope a higher TF must clear. 0.0 = sign-only.
        window_15m: number of 15-minute bars to slope over (>= 2).
        window_1h:  number of 1-hour bars to slope over (>= 2).

    Returns:
        :class:`MTFAlignmentResult` with the per-TF slopes and a reason.
        Insufficient data is reported as ``aligned=True`` so an under-warm
        bot is not artificially silenced — same logic as the regime gate's
        ``min_samples`` policy.
    """
    direction = (direction or "").lower()
    if direction not in ("buy", "sell"):
        return MTFAlignmentResult(True, f"no opinion on direction={direction!r}")

    bars_15m = _resample(candles_1m, "15min")
    bars_1h = _resample(candles_1m, "1h")
    if len(bars_15m) < window_15m or len(bars_1h) < window_1h:
        return MTFAlignmentResult(
            True,
            f"insufficient higher-TF history " f"({len(bars_15m)} 15m / {len(bars_1h)} 1h bars)",
        )

    slope_15m = _trend_slope(bars_15m["close"], window_15m)
    slope_1h = _trend_slope(bars_1h["close"], window_1h)

    if direction == "buy":
        want_15 = slope_15m > min_slope
        want_1h = slope_1h > min_slope
    else:  # sell
        want_15 = slope_15m < -min_slope
        want_1h = slope_1h < -min_slope

    aligned = bool(want_15 and want_1h)
    sign = "+" if direction == "buy" else "-"
    reason = (
        f"{sign} {direction}: 15m slope {slope_15m:+.5f}, "
        f"1h slope {slope_1h:+.5f} "
        f"(need both {sign if min_slope == 0 else f'{sign}{min_slope:.5f}'})"
    )
    return MTFAlignmentResult(aligned, reason, slope_15m, slope_1h)
