"""Label generation — OFFLINE TRAINING ONLY.

⚠️  Labels look into the FUTURE. Every function here uses ``close.shift(-h)``,
which references bars *after* the labeled bar. That is correct and necessary
for supervised training — and it is exactly why labels must NEVER touch the
online inference path.

Two structural safeguards:

1. The last ``horizon`` rows have no future and therefore get a NaN label.
   Training code drops them. The model is never trained on a bar whose
   outcome is unknown.
2. Nothing in ``daytrade.fusion`` / ``daytrade.cli`` imports this module for
   live decisions — the leakage test suite asserts that.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def future_return(close: pd.Series, horizon: int) -> pd.Series:
    """Forward return over ``horizon`` bars: ``close[t+h] / close[t] - 1``.

    The trailing ``horizon`` entries are NaN — their future is not yet known.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    return close.shift(-horizon) / close - 1.0


def directional_label(close: pd.Series, horizon: int) -> pd.Series:
    """Binary direction label: 1 if the forward return is positive else 0.

    Returns a float Series (NaN where the future is unknown) so callers can
    ``dropna()`` before casting to int.
    """
    fwd = future_return(close, horizon)
    label = (fwd > 0).astype(float)
    return label.where(fwd.notna(), np.nan)


def breakout_label(
    close: pd.Series,
    horizon: int,
    threshold: float,
) -> pd.Series:
    """Three-way breakout label, keeping only decisive moves.

    * forward return ``> +threshold``  -> 1 (up breakout)
    * forward return ``< -threshold``  -> 0 (down breakout)
    * otherwise                        -> NaN (ambiguous; dropped in training)

    Filtering out the indecisive middle gives the classifier a cleaner,
    better-separated target — at the cost of fewer samples.
    """
    if threshold <= 0:
        raise ValueError("threshold must be > 0")
    fwd = future_return(close, horizon)
    label = pd.Series(np.nan, index=close.index, dtype=float)
    label[fwd > threshold] = 1.0
    label[fwd < -threshold] = 0.0
    # Where the future is unknown the label must stay NaN regardless.
    label = label.where(fwd.notna(), np.nan)
    return label


def triple_barrier_label(
    frame: pd.DataFrame,
    stop_distance: pd.Series,
    target_distance: pd.Series,
    max_hold: int,
) -> pd.Series:
    """Triple-barrier label for a LONG entry (Lopez de Prado).

    For each bar: enter at the close, place a stop ``stop_distance`` below and
    a target ``target_distance`` above, then walk forward up to ``max_hold``
    bars (the vertical barrier).

    * ``1``   — the target was reached first (a winning trade)
    * ``0``   — the stop was reached first, or the vertical barrier expired
    * ``NaN`` — not enough future bars to resolve the outcome (dropped in
      training)

    If a single bar touches both barriers the stop is assumed first — the
    same pessimistic convention the backtester uses.
    """
    if max_hold < 1:
        raise ValueError("max_hold must be >= 1")
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    sd = np.asarray(stop_distance, dtype=float)
    td = np.asarray(target_distance, dtype=float)
    n = len(close)
    out = np.full(n, np.nan)

    for i in range(n - 1):
        if not (np.isfinite(sd[i]) and np.isfinite(td[i])):
            continue
        stop = close[i] - sd[i]
        target = close[i] + td[i]
        end = min(i + max_hold, n - 1)
        resolved: float | None = None
        for j in range(i + 1, end + 1):
            if low[j] <= stop:
                resolved = 0.0
                break
            if high[j] >= target:
                resolved = 1.0
                break
        if resolved is not None:
            out[i] = resolved
        elif end - i >= max_hold:
            out[i] = 0.0  # full vertical barrier reached, never hit -> timeout

    return pd.Series(out, index=frame.index, name="meta_label")


def make_labels(
    frame: pd.DataFrame,
    horizon: int,
    threshold: float,
    kind: str = "breakout",
) -> pd.Series:
    """Build the training label Series from an OHLCV frame.

    Args:
        kind: ``"breakout"`` (decisive moves only) or ``"directional"``
            (every bar labeled up/down).
    """
    close = frame["close"]
    if kind == "breakout":
        labels = breakout_label(close, horizon, threshold)
    elif kind == "directional":
        labels = directional_label(close, horizon)
    else:
        raise ValueError(f"unknown label kind: {kind}")
    labels.name = "label"
    return labels
