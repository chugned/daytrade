"""Iteration 8: RandomForest at 730 days (regime-robustness check).

GB at 730d collapsed to 44%/48%. RF at 365d showed 57%/55% with less
overfit. Does RF survive the same stress test that broke GB?"""

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
        days=730,
        config=cfg,
    )
    render_research(results, Console())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
