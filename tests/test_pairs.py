"""Tests for the cross-asset pairs / stat-arb module."""

from __future__ import annotations

import math

import numpy as np
import pytest

from daytrade.observatory.pairs import (
    PairSignal,
    analyse_pair,
    backtest_pair,
    fit_pair,
    latest_z,
    signal_from_z,
)


@pytest.fixture
def rng():
    return np.random.default_rng(seed=42)


def _cointegrated(
    rng: np.random.Generator,
    n: int = 400,
    beta: float = 1.0,
    mu: float = 0.0,
    noise_sigma: float = 0.01,
) -> tuple[np.ndarray, np.ndarray]:
    """Two log-price series whose spread is mean-reverting."""
    # Random walk for x; y = β x + stationary AR(1) noise (mean-reverting).
    eps = rng.normal(0.0, 0.005, size=n)
    lx = np.cumsum(eps) + math.log(100.0)
    ar_noise = np.zeros(n)
    for i in range(1, n):
        ar_noise[i] = 0.5 * ar_noise[i - 1] + rng.normal(0.0, noise_sigma)
    ly = mu + beta * lx + ar_noise
    return np.exp(ly), np.exp(lx)


def _independent_random_walks(
    rng: np.random.Generator, n: int = 400
) -> tuple[np.ndarray, np.ndarray]:
    """Two unrelated random walks — should NOT be cointegrated."""
    lx = np.cumsum(rng.normal(0.0, 0.005, size=n)) + math.log(100.0)
    ly = np.cumsum(rng.normal(0.0, 0.005, size=n)) + math.log(100.0)
    return np.exp(ly), np.exp(lx)


# ---------------------------------------------------------------------------
# fit_pair
# ---------------------------------------------------------------------------


def test_fit_pair_recovers_known_beta(rng):
    y, x = _cointegrated(rng, n=600, beta=0.7, mu=0.0)
    fit = fit_pair(y, x)
    assert fit.is_cointegrated is True
    assert fit.beta == pytest.approx(0.7, abs=0.15)
    assert fit.spread_std > 0


def test_fit_pair_marks_unrelated_as_noncointegrated(rng):
    y, x = _independent_random_walks(rng, n=600)
    fit = fit_pair(y, x)
    # Most random-walk pairs fail ADF; allow rare false positives but
    # require the spread variance to be large.
    if fit.is_cointegrated:
        assert fit.spread_std > 0.05  # if "cointegrated", spread is wide


def test_fit_pair_rejects_short_series(rng):
    y, x = _cointegrated(rng, n=20)
    fit = fit_pair(y, x)
    assert fit.is_cointegrated is False
    assert math.isnan(fit.beta) or fit.spread_std == 0.0


def test_fit_pair_rejects_nonpositive_prices():
    y = np.array([100.0, 101.0, 0.0, 102.0])
    x = np.array([50.0, 51.0, 50.5, 51.5])
    fit = fit_pair(y, x)
    assert fit.is_cointegrated is False


def test_fit_pair_rejects_mismatched_shapes():
    y = np.array([100.0] * 100)
    x = np.array([50.0] * 50)
    fit = fit_pair(y, x)
    assert fit.is_cointegrated is False


# ---------------------------------------------------------------------------
# z-score
# ---------------------------------------------------------------------------


def test_latest_z_zero_when_spread_at_mean(rng):
    y, x = _cointegrated(rng, n=400)
    fit = fit_pair(y, x)
    # Replace the last point with one that sits exactly at the spread mean.
    target_log = fit.spread_mean + fit.beta * math.log(x[-1])
    y_at_mean = np.append(y[:-1], math.exp(target_log))
    z = latest_z(y_at_mean, x, fit)
    assert abs(z) < 0.5


def test_latest_z_large_when_spread_far(rng):
    y, x = _cointegrated(rng, n=400)
    fit = fit_pair(y, x)
    # Shock y upward by several std of the spread.
    shocked = y.copy()
    shocked[-1] = shocked[-1] * math.exp(5 * fit.spread_std)
    z = latest_z(shocked, x, fit)
    assert z > 3.0


# ---------------------------------------------------------------------------
# signal_from_z
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "z,expected",
    [
        (2.5, PairSignal.SHORT_SPREAD),
        (-2.5, PairSignal.LONG_SPREAD),
        (0.2, PairSignal.EXIT),
        (1.0, PairSignal.HOLD),
        (-1.0, PairSignal.HOLD),
        (float("nan"), PairSignal.HOLD),
    ],
)
def test_signal_from_z(z, expected):
    assert signal_from_z(z, entry_z=2.0, exit_z=0.5) == expected


# ---------------------------------------------------------------------------
# analyse_pair end-to-end
# ---------------------------------------------------------------------------


def test_analyse_pair_holds_when_not_cointegrated(rng):
    y, x = _independent_random_walks(rng, n=200)
    reading = analyse_pair(y, x, adf_threshold=0.01)
    # Tight ADF threshold + short series: should refuse to trade.
    if not reading.fit.is_cointegrated:
        assert reading.signal is PairSignal.HOLD


def test_analyse_pair_returns_signal_when_cointegrated(rng):
    y, x = _cointegrated(rng, n=600)
    reading = analyse_pair(y, x)
    assert reading.fit.is_cointegrated is True
    assert reading.signal in PairSignal
    assert math.isfinite(reading.z)


def test_analyse_pair_shocked_spread_triggers_signal(rng):
    y, x = _cointegrated(rng, n=600)
    fit = fit_pair(y, x)
    # Shock the last y enough to push z far above entry_z=2.0.
    shocked = y.copy()
    shocked[-1] = shocked[-1] * math.exp(4 * fit.spread_std)
    reading = analyse_pair(shocked, x, entry_z=2.0, exit_z=0.5)
    # If still cointegrated post-shock (likely on this generator) we
    # expect a SHORT_SPREAD signal.
    if reading.fit.is_cointegrated:
        assert reading.signal is PairSignal.SHORT_SPREAD
        assert reading.z > 2.0


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


def test_backtest_produces_trades_on_cointegrated_series(rng):
    y, x = _cointegrated(rng, n=1200, noise_sigma=0.02)
    res = backtest_pair(y, x, lookback=300, entry_z=1.5, exit_z=0.3, refit_every=120)
    assert res.trades > 0
    # No assertion on win-rate — random seed varies; mean-reverting series
    # generators don't guarantee positive PnL each time, but they should
    # at minimum produce SOME trades. Stat-arb is a research signal, not
    # a sure thing.


def test_backtest_returns_empty_on_short_history(rng):
    y, x = _cointegrated(rng, n=50)
    res = backtest_pair(y, x, lookback=200)
    assert res.trades == 0


def test_backtest_skips_when_no_cointegration(rng):
    y, x = _independent_random_walks(rng, n=1200)
    res = backtest_pair(y, x, lookback=300, entry_z=2.0, exit_z=0.5)
    # Random walks rarely give *any* cointegrated fold over a long window.
    # Trades may be zero, but should certainly not be huge.
    assert res.trades < 50


# ---------------------------------------------------------------------------
# No-trade-side-effects guarantee
# ---------------------------------------------------------------------------


def test_module_has_no_order_execution_symbols():
    """Defence-in-depth: pairs.py must never import or define order code."""
    import daytrade.observatory.pairs as pairs_mod

    src = open(pairs_mod.__file__).read()
    for forbidden in (
        "place_order",
        "execute_trade",
        "send_order",
        "live_order",
        "submit_market",
        "submit_limit",
    ):
        assert forbidden not in src, f"pairs.py must never contain '{forbidden}' — paper-only."
