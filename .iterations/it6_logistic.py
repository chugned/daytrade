"""Iteration 6: ETH 1h × 365d with LOGISTIC_REGRESSION instead of GB.

If the WF accuracy stays near 56% (the GB result from iteration 2) the
overfit was model complexity. If it drops to noise floor, the features
themselves aren't predictive enough."""

from __future__ import annotations
import sys

from daytrade.config import load_config
from daytrade.models.enums import ModelKind
from daytrade.research import render_research, run_research
from rich.console import Console


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    # Pydantic 2 frozen sections — recreate with override:
    cfg = cfg.model_copy(update={
        "ml": cfg.ml.model_copy(update={"model_kind": ModelKind.LOGISTIC_REGRESSION}),
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
