"""Iteration 9: ETH+BNB 1h × 365d × RF with SWING labels (20-bar, 1%).

Default label config is horizon=5 bars × 0.004 (0.4%) threshold — a
short-term scalp at 1h = '5-hour 0.4% move'. The signal-to-noise on
that is small. Swing labels (20-bar 1% move = 'will price move 1% within
20 hours') are coarser, and literature suggests work better on crypto."""

from __future__ import annotations

from daytrade.config import load_config
from daytrade.models.enums import ModelKind
from daytrade.research import render_research, run_research
from rich.console import Console


def main() -> int:
    cfg = load_config(load_dotenv_file=False)
    cfg = cfg.model_copy(update={
        "ml": cfg.ml.model_copy(update={"model_kind": ModelKind.RANDOM_FOREST}),
        "labels": cfg.labels.model_copy(update={
            "horizon": 20,
            "breakout_threshold": 0.01,
        }),
    })
    print(f"labels.horizon={cfg.labels.horizon} "
          f"labels.breakout_threshold={cfg.labels.breakout_threshold} "
          f"ml.model_kind={cfg.ml.model_kind}")
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
