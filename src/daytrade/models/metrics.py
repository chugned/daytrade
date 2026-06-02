"""Performance-metric models for backtests and walk-forward validation."""

from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import Field, field_validator

from ._base import DaytradeModel, normalize_timestamp


class BacktestMetrics(DaytradeModel):
    """Summary statistics for a completed backtest / simulation.

    These numbers describe a *simulation*. Backtests are NOT reality — they
    omit competition, your own market impact at scale, and the future.
    """

    symbol: str
    start: datetime
    end: datetime
    bars: int = Field(ge=0)

    starting_equity: float = Field(gt=0)
    ending_equity: float = Field(ge=0)

    total_trades: int = Field(default=0, ge=0)
    winning_trades: int = Field(default=0, ge=0)
    losing_trades: int = Field(default=0, ge=0)

    total_return_pct: float = 0.0
    win_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = Field(default=0.0, ge=0.0)
    max_drawdown_pct: float = Field(default=0.0, ge=0.0)
    sharpe_like: float = Field(
        default=0.0,
        description="Mean/std of per-bar returns * sqrt(bars). NOT annualized.",
    )
    exposure_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of bars holding a position.",
    )
    total_fees: float = Field(default=0.0, ge=0.0)
    total_slippage: float = Field(default=0.0, ge=0.0)

    warnings: List[str] = Field(
        default_factory=list,
        description="Realism / overfitting red flags raised during the run.",
    )

    @field_validator("start", "end", mode="before")
    @classmethod
    def _ts(cls, v: object) -> datetime:
        return normalize_timestamp(v)

    @property
    def is_profitable(self) -> bool:
        return self.ending_equity > self.starting_equity


class WalkForwardFold(DaytradeModel):
    """Metrics for one fold of walk-forward validation."""

    fold: int = Field(ge=0)
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_samples: int = Field(ge=0)
    test_samples: int = Field(ge=0)
    train_accuracy: float = Field(ge=0.0, le=1.0)
    test_accuracy: float = Field(ge=0.0, le=1.0)
    test_auc: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("train_start", "train_end", "test_start", "test_end", mode="before")
    @classmethod
    def _ts(cls, v: object) -> datetime:
        return normalize_timestamp(v)

    @property
    def overfit_gap(self) -> float:
        """train_accuracy - test_accuracy. Large positive => overfitting."""
        return self.train_accuracy - self.test_accuracy


class WalkForwardReport(DaytradeModel):
    """Aggregate result of a walk-forward validation run."""

    model_kind: str
    folds: List[WalkForwardFold] = Field(default_factory=list)
    mean_test_accuracy: float = Field(default=0.0, ge=0.0, le=1.0)
    mean_overfit_gap: float = 0.0
    leakage_suspected: bool = False
    warnings: List[str] = Field(default_factory=list)

    model_config = DaytradeModel.model_config | {"protected_namespaces": ()}

    @property
    def n_folds(self) -> int:
        return len(self.folds)
