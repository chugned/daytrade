"""Walk-forward validation.

The only honest way to estimate how a model generalizes: train on a block of
*past* bars, test on the *next* block of unseen bars, roll the window forward,
repeat. Never shuffle — shuffling a time series leaks the future into the
training set.

This module also actively looks for trouble:

* **Overfitting** — a large train-vs-test accuracy gap.
* **Leakage** — test accuracy so high it is not credible for noisy markets;
  almost always a sign that future information reached the features/labels.
"""

from __future__ import annotations

from typing import List

import numpy as np
from sklearn.metrics import accuracy_score, roc_auc_score

from ..config.schema import AppConfig
from ..ml.dataset import Dataset, build_dataset
from ..ml.model import build_estimator
from ..models import OHLCV, ModelKind, WalkForwardFold, WalkForwardReport
from ..runtime import get_logger

_log = get_logger("validation.walkforward")


def _resolve_windows(n: int, train_window: int, test_window: int, n_folds: int) -> tuple[int, int]:
    """Shrink the configured windows if the dataset cannot accommodate them."""
    needed = train_window + test_window * n_folds
    if needed <= n:
        return train_window, test_window
    # Scale both windows down proportionally to fit the data we actually have.
    scale = n / float(needed)
    tw = max(40, int(train_window * scale))
    sw = max(15, int(test_window * scale))
    _log.warning("walk-forward windows shrunk to fit %d samples: train=%d test=%d", n, tw, sw)
    return tw, sw


def walk_forward_validate(
    candles: List[OHLCV],
    config: AppConfig,
) -> WalkForwardReport:
    """Run walk-forward validation and return an aggregated report."""
    dataset: Dataset = build_dataset(candles, config)
    X = dataset.X.values
    y = dataset.y.values
    index = dataset.X.index
    n = len(dataset)

    wf = config.walkforward
    train_window, test_window = _resolve_windows(n, wf.train_window, wf.test_window, wf.n_folds)

    folds: List[WalkForwardFold] = []
    warnings: List[str] = []
    start = 0
    fold_id = 0

    while fold_id < wf.n_folds and start + train_window + test_window <= n:
        tr_lo, tr_hi = start, start + train_window
        te_lo, te_hi = tr_hi, tr_hi + test_window

        X_tr, y_tr = X[tr_lo:tr_hi], y[tr_lo:tr_hi]
        X_te, y_te = X[te_lo:te_hi], y[te_lo:te_hi]

        if len(np.unique(y_tr)) < 2:
            warnings.append(f"fold {fold_id}: single-class training window skipped")
            start += test_window
            fold_id += 1
            continue

        estimator = build_estimator(ModelKind(config.ml.model_kind), config.runtime.random_seed)
        estimator.fit(X_tr, y_tr)

        train_acc = float(accuracy_score(y_tr, estimator.predict(X_tr)))
        test_pred = estimator.predict(X_te)
        test_acc = float(accuracy_score(y_te, test_pred))
        try:
            test_proba = estimator.predict_proba(X_te)[:, 1]
            test_auc = float(roc_auc_score(y_te, test_proba))
            if not np.isfinite(test_auc):
                # roc_auc_score returns NaN (not raises) when y_te is
                # single-class — treat as "no information" rather than
                # propagating NaN into the validated WalkForwardFold.
                test_auc = 0.5
        except (ValueError, IndexError):
            test_auc = 0.5

        folds.append(
            WalkForwardFold(
                fold=fold_id,
                train_start=index[tr_lo],
                train_end=index[tr_hi - 1],
                test_start=index[te_lo],
                test_end=index[te_hi - 1],
                train_samples=len(y_tr),
                test_samples=len(y_te),
                train_accuracy=train_acc,
                test_accuracy=test_acc,
                test_auc=test_auc,
            )
        )
        start += test_window
        fold_id += 1

    if not folds:
        warnings.append("no valid walk-forward folds — dataset too small")
        return WalkForwardReport(
            model_kind=config.ml.model_kind,
            folds=[],
            mean_test_accuracy=0.0,
            mean_overfit_gap=0.0,
            leakage_suspected=False,
            warnings=warnings,
        )

    mean_test_acc = float(np.mean([f.test_accuracy for f in folds]))
    mean_gap = float(np.mean([f.overfit_gap for f in folds]))

    leakage = mean_test_acc >= wf.suspicious_accuracy
    if leakage:
        warnings.append(
            f"Mean test accuracy {mean_test_acc:.3f} >= {wf.suspicious_accuracy} "
            "— implausibly high; suspect data leakage / lookahead bias."
        )
    if mean_gap >= wf.overfit_gap_warn:
        warnings.append(
            f"Mean train-test gap {mean_gap:.3f} >= {wf.overfit_gap_warn} "
            "— model is overfitting."
        )
    if mean_test_acc < 0.5:
        warnings.append(
            f"Mean test accuracy {mean_test_acc:.3f} < 0.50 — model has no "
            "predictive edge (worse than a coin flip)."
        )

    return WalkForwardReport(
        model_kind=config.ml.model_kind,
        folds=folds,
        mean_test_accuracy=mean_test_acc,
        mean_overfit_gap=mean_gap,
        leakage_suspected=leakage,
        warnings=warnings,
    )
