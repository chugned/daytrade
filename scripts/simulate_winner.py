#!/usr/bin/env python3
"""Equity-curve simulator for one (symbol, horizon, gate) cell.

Given a P5-3 winner cell, replays it: trains the meta-model on the
first 70% of the chosen window, then walks the held-out 30% and
"opens" a trade every time the gate fires, holds for the horizon,
records the forward return net of cost.

Outputs:
- A PNG equity curve (cumulative net PnL in bps over time)
- A summary printed to stdout: trades, hit rate, mean trade,
  cumulative PnL, max drawdown, Sharpe-like

Useful for sanity-checking the headline numbers from P5-3
visually before any config change is made.

Default cell: BNBUSDT, 240m horizon, gate=4.0, 90 days, retail cost.

Read-only — no live state touched.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from daytrade.config import AppConfig
from daytrade.ml.meta import MetaLabelModel
from daytrade.research.sweep_helpers import (
    load_or_build_frame,
    pull_candles,
    score_test_frame,
    train_test_split,
)


def _config_with_horizon(base: AppConfig, horizon_minutes: int) -> AppConfig:
    return base.model_copy(update={
        "risk": base.risk.model_copy(update={"max_hold_bars": horizon_minutes}),
    })


def _simulate(symbol: str, horizon: int, gate_multiple: float,
              days: int, cost_bps: float, train_frac: float = 0.7
              ) -> Optional[dict]:
    config = _config_with_horizon(AppConfig(), horizon)
    frame = load_or_build_frame(symbol, days, config, use_cache=True)
    if frame is None or len(frame) < 300:
        print(f"  insufficient frame for {symbol}", file=sys.stderr)
        return None

    train_df, test_df = train_test_split(frame, train_frac=train_frac)
    candles = pull_candles(symbol, days)
    cut = int(len(candles) * train_frac)
    model = MetaLabelModel()
    train_result = model.train([candles[:cut]], config)
    if train_result is None or not model.is_trained:
        return None

    proba = score_test_frame(model, test_df)
    if proba is None:
        return None

    floor = train_result.base_win_rate * gate_multiple
    fires = proba > floor
    trades = test_df.loc[fires].copy()
    trades["proba"] = proba[fires]
    trades["return_bps"] = trades["forward_return_bps"].astype(float)
    trades["net_bps"] = trades["return_bps"] - cost_bps
    trades["cum_pnl_bps"] = trades["net_bps"].cumsum()

    if trades.empty:
        return {
            "symbol": symbol,
            "horizon": horizon,
            "gate_multiple": gate_multiple,
            "n_trades": 0,
            "summary": "no trades fired",
        }

    cum = trades["cum_pnl_bps"].to_numpy()
    peaks = np.maximum.accumulate(cum)
    drawdowns = cum - peaks
    return {
        "symbol": symbol,
        "horizon": horizon,
        "gate_multiple": gate_multiple,
        "days": days,
        "cost_bps": cost_bps,
        "base_win_rate": train_result.base_win_rate,
        "gate_floor": floor,
        "n_test_bars": len(test_df),
        "n_trades": len(trades),
        "hit_rate": float(trades["return_bps"].gt(0).mean()),
        "mean_net_bps": float(trades["net_bps"].mean()),
        "median_net_bps": float(trades["net_bps"].median()),
        "cumulative_pnl_bps": float(trades["net_bps"].sum()),
        "max_drawdown_bps": float(drawdowns.min()),
        "sharpe_like": float(trades["net_bps"].mean() / trades["net_bps"].std(ddof=1))
                       if len(trades) > 1 else None,
        "trades": trades,
    }


def _plot_curve(sim: dict, out_path: Path) -> None:
    """Equity curve as a PNG. Lazy-imports matplotlib so the module
    is usable without it for the pure-numerics path."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    trades = sim["trades"]
    cum = trades["cum_pnl_bps"].to_numpy()
    x = np.arange(len(cum))

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(x, cum, color="#3b82f6", linewidth=1.5, label="cumulative net PnL (bps)")
    # Drawdown shading
    peaks = np.maximum.accumulate(cum)
    ax.fill_between(x, cum, peaks, color="#ef4444", alpha=0.15, label="drawdown")
    ax.axhline(0, color="#666", linewidth=0.5, linestyle="--")
    ax.set_xlabel("trade index (chronological in held-out window)")
    ax.set_ylabel("cumulative net PnL (bps)")
    ax.set_title(
        f"{sim['symbol']} {sim['horizon']}m, gate=×{sim['gate_multiple']:.1f}, "
        f"{sim['days']}d window, {sim['cost_bps']:.0f}bp round-trip\n"
        f"{sim['n_trades']} trades · hit {sim['hit_rate']*100:.0f}% · "
        f"net {sim['cumulative_pnl_bps']:+.0f} bp (mean {sim['mean_net_bps']:+.1f} bp/trade)"
    )
    ax.legend(loc="upper left", framealpha=0.9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="BNBUSDT")
    p.add_argument("--horizon", type=int, default=240, help="minutes")
    p.add_argument("--gate-multiple", type=float, default=4.0)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--cost-bps", type=float, default=24.0)
    p.add_argument("--out", default=None,
                   help="PNG output path (default: artifacts/equity_<sym>_<h>m_g<x>.png)")
    args = p.parse_args()

    sim = _simulate(args.symbol, args.horizon, args.gate_multiple,
                    args.days, args.cost_bps)
    if sim is None:
        print("simulation failed", file=sys.stderr)
        return 1

    # Text summary
    print(f"=== {sim['symbol']} {sim['horizon']}m gate=×{sim['gate_multiple']:.1f} "
          f"({sim['days']}d, {sim['cost_bps']:.0f}bp cost) ===")
    if sim["n_trades"] == 0:
        print(f"  no trades fired (gate floor={sim.get('gate_floor', '?'):.3f})")
        return 0
    print(f"  trades: {sim['n_trades']}")
    print(f"  hit rate: {sim['hit_rate'] * 100:.1f}%")
    print(f"  mean net: {sim['mean_net_bps']:+.2f} bp/trade")
    print(f"  median net: {sim['median_net_bps']:+.2f} bp/trade")
    print(f"  cumulative net: {sim['cumulative_pnl_bps']:+.1f} bp")
    print(f"  max drawdown: {sim['max_drawdown_bps']:+.1f} bp")
    sharpe = sim["sharpe_like"]
    print(f"  sharpe-like: {'—' if sharpe is None else f'{sharpe:+.2f}'}")

    out_path = Path(args.out) if args.out else Path(
        f"artifacts/equity_{args.symbol}_{args.horizon}m_g{args.gate_multiple}.png"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _plot_curve(sim, out_path)
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
