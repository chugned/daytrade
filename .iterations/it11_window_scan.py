"""Iteration 11: ETH 1h + swing-labels + RF, scanned across window lengths.

Window-length sensitivity test:
  90d  → 180d → 365d → 730d
  How does walk-forward accuracy scale? If WF acc declines monotonically
  with longer history, the 'edge' lives in the most-recent regime only —
  the bot would have to be perpetually retrained on a short window."""

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
    print(f"swing labels: horizon={cfg.labels.horizon} "
          f"threshold={cfg.labels.breakout_threshold} model={cfg.ml.model_kind}")

    cons = Console()
    for d in (90, 180, 365, 730):
        print(f"\n=== window = {d} days ===")
        results = run_research(symbols=["ETHUSDT"], interval="1h",
                               days=d, config=cfg)
        render_research(results, cons)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
