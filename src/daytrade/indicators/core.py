"""Vectorized technical indicators.

Design rules:

* **No lookahead bias.** Every value at index ``t`` is a function of data at
  indices ``<= t`` only. Indicators use causal (``adjust=False``) smoothing
  and trailing rolling windows — never centered windows, never ``.shift(-k)``.
* **Vectorized.** Operations run over whole pandas Series; no Python loops in
  hot paths.
* **Numerically stable.** Divisions guard against zero denominators.

All functions accept and return ``pandas.Series`` aligned to the input index.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_EPS = 1e-12


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (causal, ``adjust=False``)."""
    if period < 1:
        raise ValueError("period must be >= 1")
    return series.ewm(span=period, adjust=False, min_periods=1).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index in [0, 100].

    Uses Wilder smoothing (``ewm`` with ``alpha = 1/period``). The first
    ``period`` values are NaN because RSI is undefined before then — that NaN
    is deliberate and must not be forward-filled with a fake value.
    """
    if period < 2:
        raise ValueError("rsi period must be >= 2")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / (avg_loss + _EPS)
    out = 100.0 - 100.0 / (1.0 + rs)
    # When there are no losses at all, RSI is 100 by definition.
    out = out.where(avg_loss > _EPS, 100.0)
    out = out.where(~avg_gain.isna(), np.nan)
    return out


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line and histogram.

    Returns a DataFrame with columns ``macd``, ``signal``, ``histogram``.
    """
    if fast >= slow:
        raise ValueError("fast period must be < slow period")
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
        }
    )


def returns(close: pd.Series, periods: int = 1) -> pd.Series:
    """Simple return over ``periods`` bars (causal)."""
    return close.pct_change(periods=periods)


def log_returns(close: pd.Series, periods: int = 1) -> pd.Series:
    """Log return over ``periods`` bars (causal)."""
    return np.log(close / close.shift(periods))


def volatility(close: pd.Series, window: int = 20) -> pd.Series:
    """Rolling standard deviation of 1-bar simple returns (trailing window)."""
    if window < 2:
        raise ValueError("volatility window must be >= 2")
    return close.pct_change().rolling(window=window, min_periods=window).std()


def momentum(close: pd.Series, window: int = 10) -> pd.Series:
    """Rate-of-change momentum: price now vs ``window`` bars ago."""
    if window < 1:
        raise ValueError("momentum window must be >= 1")
    return close / close.shift(window) - 1.0


def trend_slope(close: pd.Series, window: int = 20) -> pd.Series:
    """Slope of an OLS line fit over a trailing window, normalized by price.

    The slope is expressed as fractional price change per bar, so it is
    comparable across instruments of different absolute price.

    Vectorized in pure NumPy: the OLS slope is ``cov(x, y) / var(x)``; with
    ``x`` a fixed ``0..window-1`` ramp, ``var(x)`` is constant and the
    covariance numerator is a rolling weighted sum — a correlation of the
    price series with the fixed ``x``-deviation kernel (``np.correlate``).
    The first ``window-1`` values are NaN (window not yet full).
    """
    if window < 2:
        raise ValueError("trend window must be >= 2")
    x = np.arange(window, dtype=float)
    x_dev = x - x.mean()
    x_var = float((x_dev**2).sum())

    y = close.to_numpy(dtype=float)
    out = np.full(y.shape[0], np.nan)
    if y.shape[0] >= window:
        # Rolling covariance numerator: corr of y with the x-deviation kernel.
        numerator = np.correlate(y, x_dev, mode="valid")
        # Rolling window mean via a cumulative-sum difference.
        csum = np.cumsum(np.insert(y, 0, 0.0))
        y_mean = (csum[window:] - csum[:-window]) / window
        beta = numerator / (x_var + _EPS)
        # Match the denominator floor: window mean, floored in magnitude.
        denom = np.where(np.abs(y_mean) > _EPS, y_mean, _EPS)
        out[window - 1 :] = beta / denom
    return pd.Series(out, index=close.index)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True range — the building block of ATR."""
    prev_close = close.shift(1)
    ranges = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average True Range via Wilder smoothing — a volatility unit in price."""
    if period < 1:
        raise ValueError("atr period must be >= 1")
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rolling_std(series: pd.Series, window: int) -> pd.Series:
    """Trailing rolling standard deviation."""
    return series.rolling(window=window, min_periods=window).std()


def rolling_skew(series: pd.Series, window: int) -> pd.Series:
    """Trailing rolling skewness."""
    return series.rolling(window=window, min_periods=window).skew()


def rolling_kurtosis(series: pd.Series, window: int) -> pd.Series:
    """Trailing rolling excess kurtosis."""
    return series.rolling(window=window, min_periods=window).kurt()
