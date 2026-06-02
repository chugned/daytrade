"""Meta-labelling tests — triple-barrier labels and the precision model."""

from __future__ import annotations

import pandas as pd

from daytrade.exchanges import generate_random_walk
from daytrade.labels import triple_barrier_label
from daytrade.ml.meta import MetaLabelModel


def _frame(closes):
    return pd.DataFrame(
        {
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": list(closes),
        }
    )


def test_triple_barrier_labels_a_rising_series_as_wins():
    """A steady uptrend hits the target before the stop."""
    frame = _frame([100.0 + i for i in range(40)])
    sd = pd.Series([0.5] * 40)
    td = pd.Series([0.5] * 40)
    labels = triple_barrier_label(frame, sd, td, max_hold=5).dropna()
    assert (labels == 1.0).mean() > 0.8


def test_triple_barrier_labels_a_falling_series_as_losses():
    """A steady downtrend hits the stop before the target."""
    frame = _frame([200.0 - i for i in range(40)])
    sd = pd.Series([0.5] * 40)
    td = pd.Series([0.5] * 40)
    labels = triple_barrier_label(frame, sd, td, max_hold=5).dropna()
    assert (labels == 0.0).mean() > 0.8


def test_triple_barrier_timeout_and_trailing_bars():
    """A flat series times out early bars (0) and leaves trailing bars NaN."""
    frame = _frame([100.0] * 40)
    sd = pd.Series([5.0] * 40)  # barriers far away — never hit
    td = pd.Series([5.0] * 40)
    labels = triple_barrier_label(frame, sd, td, max_hold=8)
    assert labels.iloc[0] == 0.0  # full vertical reached -> timeout
    assert pd.isna(labels.iloc[-1])  # no future -> unlabelled


def test_meta_model_trains_and_predicts(config):
    """The meta-model trains on triple-barrier outcomes and scores a bar."""
    candles = generate_random_walk(
        "BTCUSDT", n_bars=500, start_price=100.0, drift=0.0003, volatility=0.006, seed=5
    )
    model = MetaLabelModel()
    assert model.predict_win_proba(candles, config) is None  # untrained

    result = model.train([candles], config)
    assert result is not None
    assert result.samples > 0
    assert 0.0 <= result.base_win_rate <= 1.0
    assert model.is_trained
    # The base win rate is stored on the model — the meta-gate threshold is
    # set relative to it.
    assert model.base_win_rate == result.base_win_rate

    proba = model.predict_win_proba(candles, config)
    assert proba is None or 0.0 <= proba <= 1.0


def test_meta_model_untrained_on_too_little_history(config):
    """Too few candles -> training declines rather than guessing."""
    short = generate_random_walk("BTCUSDT", n_bars=80, start_price=100.0, seed=3)
    model = MetaLabelModel()
    assert model.train([short], config) is None
    assert not model.is_trained
    assert model.base_win_rate is None
