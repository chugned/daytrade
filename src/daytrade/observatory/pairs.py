"""Cross-asset statistical-arbitrage (pairs) module.

A pair-trade signal is one of the few research-supported edges that works
on liquid crypto majors at intraday horizons (Gatev/Goetzmann/Rouwenhorst
2006; multiple subsequent crypto-specific replications). The pieces:

1. **Hedge ratio**: OLS β so that ``spread = y - β * x`` is approximately
   stationary. We use a simple closed-form OLS on log prices (no constant
   — the constant is absorbed into the spread mean and de-meaned later).

2. **Spread stationarity**: an Augmented Dickey–Fuller test on the spread.
   Below a configured p-value the pair is considered cointegrated enough
   to trade.

3. **Z-score**: ``(spread - rolling_mean) / rolling_std``. Entry when
   ``|z| >= entry_z``, exit when ``|z| <= exit_z``.

All math is pure numpy/scipy on numeric arrays — no I/O, no global state.
PAPER / SIMULATION ONLY. There is no trade-execution code anywhere in
this module; it is read-only research.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import numpy as np

try:
    # statsmodels is optional; if missing, fall back to a hand-rolled
    # ADF approximation so the rest of the project keeps working without
    # a heavyweight new dependency.
    from statsmodels.tsa.stattools import adfuller as _adfuller
    _HAVE_STATSMODELS = True
except ImportError:  # pragma: no cover - dependency-presence varies
    _HAVE_STATSMODELS = False


class PairSignal(str, Enum):
    HOLD = "HOLD"            # within band, do nothing
    LONG_SPREAD = "LONG_SPREAD"     # buy Y, sell X — spread expected to rise
    SHORT_SPREAD = "SHORT_SPREAD"   # sell Y, buy X — spread expected to fall
    EXIT = "EXIT"            # close — spread reverted to its mean


@dataclass(frozen=True)
class PairFit:
    """Result of fitting the hedge ratio + stationarity check on a pair."""

    beta: float                 # hedge ratio (y ≈ β · x in log-price)
    spread_mean: float
    spread_std: float
    adf_pvalue: float           # lower = more stationary
    is_cointegrated: bool       # ADF p < threshold AND beta finite


@dataclass(frozen=True)
class PairReading:
    """The latest pair-trade signal."""

    signal: PairSignal
    z: float
    fit: PairFit
    reason: str


def _ols_beta(y: np.ndarray, x: np.ndarray) -> float:
    """No-intercept OLS hedge ratio in log space."""
    denom = float(np.dot(x, x))
    if denom <= 0.0 or not np.isfinite(denom):
        return float("nan")
    return float(np.dot(x, y) / denom)


def _fallback_adf(series: np.ndarray) -> float:
    """Crude ADF-style p-value substitute when statsmodels is missing.

    Regresses ``Δs_t`` on ``s_{t-1}`` and uses the t-stat with critical
    values from MacKinnon's table — accurate enough for a 'is this
    spread cointegrated *enough*?' gate. NEVER used for publication."""
    if series.size < 30:
        return 1.0
    s = series - series.mean()
    ds = np.diff(s)
    lag = s[:-1]
    denom = float(np.dot(lag, lag))
    if denom <= 0.0:
        return 1.0
    rho = float(np.dot(lag, ds) / denom)
    # Residual sigma
    resid = ds - rho * lag
    n = len(ds)
    sigma2 = float(np.dot(resid, resid) / max(1, n - 1))
    se = (sigma2 / denom) ** 0.5 if denom > 0 else float("inf")
    if not np.isfinite(se) or se <= 0:
        return 1.0
    t = rho / se
    # MacKinnon-ish approximation for the no-trend case at n≥100:
    # crit values t ≈ -3.43 (1%), -2.86 (5%), -2.57 (10%).
    if t < -3.43:
        return 0.01
    if t < -2.86:
        return 0.05
    if t < -2.57:
        return 0.10
    if t < -1.95:
        return 0.20
    return 0.50


def fit_pair(
    y_prices: List[float] | np.ndarray,
    x_prices: List[float] | np.ndarray,
    *,
    adf_threshold: float = 0.05,
) -> PairFit:
    """Fit the hedge ratio + ADF on the log-price spread.

    ``y_prices`` and ``x_prices`` must be aligned, same-length, strictly
    positive close-price arrays.
    """
    y = np.asarray(y_prices, dtype=float)
    x = np.asarray(x_prices, dtype=float)
    if y.shape != x.shape or y.size < 30:
        return PairFit(beta=float("nan"), spread_mean=0.0, spread_std=0.0,
                       adf_pvalue=1.0, is_cointegrated=False)
    if np.any(y <= 0) or np.any(x <= 0):
        return PairFit(beta=float("nan"), spread_mean=0.0, spread_std=0.0,
                       adf_pvalue=1.0, is_cointegrated=False)
    ly = np.log(y)
    lx = np.log(x)
    # De-mean before fitting so β is independent of the level of each leg.
    ly_c = ly - ly.mean()
    lx_c = lx - lx.mean()
    beta = _ols_beta(ly_c, lx_c)
    if not np.isfinite(beta):
        return PairFit(beta=float("nan"), spread_mean=0.0, spread_std=0.0,
                       adf_pvalue=1.0, is_cointegrated=False)
    spread = ly - beta * lx
    spread_mean = float(spread.mean())
    spread_std = float(spread.std(ddof=1)) if spread.size > 1 else 0.0
    if spread_std <= 0.0:
        return PairFit(beta=beta, spread_mean=spread_mean, spread_std=0.0,
                       adf_pvalue=1.0, is_cointegrated=False)
    if _HAVE_STATSMODELS:
        try:
            adf_stat, p, *_ = _adfuller(spread, autolag="AIC")
            pvalue = float(p)
        except (ValueError, np.linalg.LinAlgError):
            pvalue = _fallback_adf(spread)
    else:
        pvalue = _fallback_adf(spread)
    return PairFit(
        beta=beta, spread_mean=spread_mean, spread_std=spread_std,
        adf_pvalue=pvalue, is_cointegrated=pvalue < adf_threshold,
    )


def latest_z(
    y_prices: List[float] | np.ndarray,
    x_prices: List[float] | np.ndarray,
    fit: PairFit,
) -> float:
    """Most recent z-score of the spread under a previously-fit β/μ/σ."""
    y = np.asarray(y_prices, dtype=float)
    x = np.asarray(x_prices, dtype=float)
    if y.size == 0 or x.size == 0 or not np.isfinite(fit.beta) or fit.spread_std <= 0:
        return float("nan")
    last_y = float(y[-1])
    last_x = float(x[-1])
    if last_y <= 0 or last_x <= 0:
        return float("nan")
    spread = np.log(last_y) - fit.beta * np.log(last_x)
    return (spread - fit.spread_mean) / fit.spread_std


def signal_from_z(
    z: float,
    *,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
) -> PairSignal:
    """Map a z-score to a discrete pair-trade decision."""
    if not np.isfinite(z):
        return PairSignal.HOLD
    if z >= entry_z:
        # Spread is unusually wide on the upside → expect mean-revert down
        # → short the spread (sell Y, buy X).
        return PairSignal.SHORT_SPREAD
    if z <= -entry_z:
        return PairSignal.LONG_SPREAD
    if abs(z) <= exit_z:
        return PairSignal.EXIT
    return PairSignal.HOLD


def analyse_pair(
    y_prices: List[float] | np.ndarray,
    x_prices: List[float] | np.ndarray,
    *,
    adf_threshold: float = 0.05,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
) -> PairReading:
    """End-to-end: fit + z-score + signal, with a human-readable reason.

    Returns ``PairSignal.HOLD`` if the pair fails the cointegration check
    — we never trade a non-stationary spread.
    """
    fit = fit_pair(y_prices, x_prices, adf_threshold=adf_threshold)
    if not fit.is_cointegrated:
        return PairReading(
            signal=PairSignal.HOLD, z=float("nan"), fit=fit,
            reason=f"not cointegrated (adf p={fit.adf_pvalue:.3f})",
        )
    z = latest_z(y_prices, x_prices, fit)
    sig = signal_from_z(z, entry_z=entry_z, exit_z=exit_z)
    reason = f"z={z:+.2f} (entry ±{entry_z}, exit ±{exit_z})"
    return PairReading(signal=sig, z=z, fit=fit, reason=reason)


# ---------------------------------------------------------------------------
# Paper backtest for sweeps. NOT used by the live loop.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PairBacktestResult:
    trades: int
    win_rate: float
    mean_pnl: float
    total_pnl: float
    max_drawdown: float


def backtest_pair(
    y_prices: List[float] | np.ndarray,
    x_prices: List[float] | np.ndarray,
    *,
    lookback: int = 240,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    refit_every: int = 60,
) -> PairBacktestResult:
    """Rolling out-of-sample backtest. PAPER ONLY — never enters orders."""
    y = np.asarray(y_prices, dtype=float)
    x = np.asarray(x_prices, dtype=float)
    n = min(len(y), len(x))
    if n < lookback + refit_every + 10:
        return PairBacktestResult(0, 0.0, 0.0, 0.0, 0.0)

    fit: Optional[PairFit] = None
    in_pos: int = 0  # +1 long spread, -1 short spread, 0 flat
    # We freeze the entry-time β + μ + σ so the round-trip PnL is computed
    # in the SAME coordinate system. Without this, a refit mid-trade
    # shifts the spread reference frame and inflates PnL spuriously.
    entry_beta: float = 0.0
    entry_spread_value: float = 0.0
    entry_sigma: float = 0.0
    pnls: List[float] = []

    for i in range(lookback, n):
        if fit is None or (i - lookback) % refit_every == 0:
            fit = fit_pair(y[i - lookback: i], x[i - lookback: i])
        if fit is None or not fit.is_cointegrated:
            continue
        last_y = float(y[i])
        last_x = float(x[i])
        if last_y <= 0 or last_x <= 0:
            continue
        # While flat, use the latest fit's β/μ/σ to decide on entry.
        live_spread = np.log(last_y) - fit.beta * np.log(last_x)
        if fit.spread_std <= 0:
            continue
        z = (live_spread - fit.spread_mean) / fit.spread_std
        if in_pos == 0:
            if z >= entry_z:
                in_pos = -1
                entry_beta = fit.beta
                entry_spread_value = live_spread
                entry_sigma = fit.spread_std
            elif z <= -entry_z:
                in_pos = +1
                entry_beta = fit.beta
                entry_spread_value = live_spread
                entry_sigma = fit.spread_std
        else:
            # While in a position, hold the spread DEFINITION constant:
            # always re-compute with entry-time β and judge exit against
            # entry-time σ. Different fit -> different spread; we're
            # trading the spread we opened, not whatever the latest fit
            # now calls a spread.
            held_spread = np.log(last_y) - entry_beta * np.log(last_x)
            held_z = ((held_spread - entry_spread_value) / entry_sigma
                      if entry_sigma > 0 else 0.0)
            # Exit when the spread has reverted by entry_z - exit_z
            # standard deviations toward the entry-time mean (which
            # the position was opened ±entry_z away from).
            target_revert = entry_z - exit_z
            if (in_pos == -1 and held_z <= -target_revert) or \
               (in_pos == +1 and held_z >= target_revert):
                pnl = in_pos * (entry_spread_value - held_spread)
                pnls.append(pnl)
                in_pos = 0

    if not pnls:
        return PairBacktestResult(0, 0.0, 0.0, 0.0, 0.0)
    arr = np.asarray(pnls)
    cum = np.cumsum(arr)
    drawdown = float(np.max(np.maximum.accumulate(cum) - cum))
    return PairBacktestResult(
        trades=len(pnls),
        win_rate=float(np.mean(arr > 0)),
        mean_pnl=float(arr.mean()),
        total_pnl=float(arr.sum()),
        max_drawdown=drawdown,
    )
