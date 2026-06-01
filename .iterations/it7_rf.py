"""Iteration 7: ETH+BNB 1h × 365d with RANDOM_FOREST.

Completes the model-complexity sweep:
  - Iteration 2 (GB):       WF 56-57% with overfit gap +0.42-0.43
  - Iteration 6 (logistic): WF 47-52% with overfit gap +0.16-0.17

RandomForest (depth=6, min_samples_leaf=5) sits between them. If the
WF accuracy sits near logistic's, we confirm the GB signal was almost
entirely overfit. If it sits near GB's, GB's overfit is mostly an
illusion of higher train accuracy not test."""

from __future__ import annotations

from daytrade.config import load_config
from daytrade.models.enums import ModelKind
from daytrade.research import render_research, run_research
from rich.console import Console


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    cfg = cfg.model_copy(update={
        "ml": cfg.ml.model_copy(update={"model_kind": ModelKind.RANDOM_FOREST}),
    })
    print(f"Using model_kind = {cfg.ml.model_kind}")
    results = run_research(
        symbols=["ETHUSDT", "BNBUSDT"],
        interval="1h",
        days=365,
        config=cfg,
    )
    render_research(results, Console())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
