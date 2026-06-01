"""Iteration 10: swing labels + RF + ETH/BNB × 1h × 730d (regime check).

Every prior 'WEAK SIGNAL' on 365d collapsed at 730d. Does the new
swing-labels result (ETH WF 67%, BNB 60%) survive the same test?
Also prints class balance so we know whether the accuracy is real or
just majority-class prediction."""

from __future__ import annotations

import numpy as np

from daytrade.config import load_config
from daytrade.indicators.frame import ohlcv_to_frame
from daytrade.labels.generate import breakout_label
from daytrade.models.enums import ModelKind
from daytrade.research import render_research, run_research
from daytrade.research.history import download_history
from rich.console import Console


def _class_balance(symbol: str, days: int, horizon: int, threshold: float) -> None:
    print(f"\nLabel diagnostic for {symbol} × {days}d × 1h:")
    candles = download_history(symbol, interval="1h", days=days)
    frame = ohlcv_to_frame(candles)
    labels = breakout_label(frame["close"], horizon=horizon, threshold=threshold)
    valid = labels.dropna()
    if len(valid) == 0:
        print("  no labels generated")
        return
    counts = valid.value_counts()
    up = int(counts.get(1, 0))
    down = int(counts.get(0, 0))
    total = up + down
    print(f"  labelled bars: {total}/{len(labels)}  "
          f"(up={up} = {up / total:.1%}, down={down} = {down / total:.1%})")
    print(f"  majority-class baseline accuracy: {max(up, down) / total:.1%}")


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

    for sym in ("ETHUSDT", "BNBUSDT"):
        _class_balance(sym, days=365, horizon=20, threshold=0.01)
        _class_balance(sym, days=730, horizon=20, threshold=0.01)

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
