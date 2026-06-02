"""ML model and dataset tests.

The expensive feature/label dataset is built once via the session-scoped
``ml_dataset`` fixture; individual model fits are cheap by comparison.
"""

from __future__ import annotations

import pytest

from daytrade.ml import PredictiveModel, build_dataset
from daytrade.models import ModelKind


def test_build_dataset_is_nan_free(ml_dataset):
    assert len(ml_dataset) > 0
    assert not ml_dataset.X.isna().any(axis=None)
    assert set(ml_dataset.y.unique()).issubset({0, 1})


def test_build_dataset_rejects_short_series(config):
    from daytrade.exchanges import generate_random_walk

    short = generate_random_walk("BTC", n_bars=20, seed=1)
    with pytest.raises(ValueError):
        build_dataset(short, config)


def test_untrained_model_emits_neutral_signal(long_candles, config):
    model = PredictiveModel()
    sig = model.predict_signal(long_candles, config)
    assert sig.score == 0.0
    assert sig.confidence == 0.0
    assert not model.is_trained


def test_model_trains_and_predicts(ml_dataset, long_candles, config):
    model = PredictiveModel(ModelKind.LOGISTIC_REGRESSION)
    result = model.fit(ml_dataset)
    assert model.is_trained
    assert 0.0 <= result.accuracy <= 1.0
    sig = model.predict_signal(long_candles, config)
    assert -1.0 <= sig.score <= 1.0
    assert abs(sig.prob_up + sig.prob_down - 1.0) < 1e-9


def test_model_save_and_load_roundtrip(ml_dataset, long_candles, config, tmp_path):
    model = PredictiveModel(ModelKind.RANDOM_FOREST)
    model.fit(ml_dataset)
    path = model.save(tmp_path / "m.pkl")
    loaded = PredictiveModel.load(path)
    assert loaded.is_trained
    assert loaded.feature_names == model.feature_names
    a = model.predict_signal(long_candles, config)
    b = loaded.predict_signal(long_candles, config)
    assert a.prob_up == pytest.approx(b.prob_up)


def test_untrained_model_refuses_to_save(tmp_path):
    with pytest.raises(RuntimeError):
        PredictiveModel().save(tmp_path / "m.pkl")


def test_fit_rejects_tiny_dataset(ml_dataset):
    tiny = type(ml_dataset)(
        X=ml_dataset.X.iloc[:5], y=ml_dataset.y.iloc[:5], feature_names=ml_dataset.feature_names
    )
    with pytest.raises(ValueError):
        PredictiveModel().fit(tiny)


@pytest.mark.parametrize("kind", list(ModelKind))
def test_all_model_kinds_train(kind, ml_dataset):
    model = PredictiveModel(kind)
    model.fit(ml_dataset)
    assert model.is_trained
