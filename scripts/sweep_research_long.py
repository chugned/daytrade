#!/usr/bin/env python3
"""Multi-day sweep of the research-branch detectors on real 1m data.

Re-evaluates the cascade detector and the cross-asset pair signal over
multiple days of Binance 1m klines (paginated + cached via
research.history). Sample sizes from this script are 5-20x what the
older single-page sweeps could hit, so the verdicts are noticeably more
trustworthy.

Paper / research only.
"""

from __future__ import annotations

import argparse
from typing import List

from daytrade.models import OHLCV
from daytrade.research.history import download_history

# The cascade + pairs modules live on their own branches. The long sweep
# is most useful once those branches are merged together, but we degrade
# gracefully so the script also works on the foundational branch alone.
try:
    from daytrade.observatory.liquidation_cascade import (  # type: ignore[import-not-found]
        CascadeState, detect_cascade,
    )
    _HAVE_CASCADE = True
except ImportError:
    _HAVE_CASCADE = False

try:
    from daytrade.observatory.pairs import (  # type: ignore[import-not-found]
        backtest_pair, fit_pair,
    )
    _HAVE_PAIRS = True
except ImportError:
    _HAVE_PAIRS = False


def _forward_return(candles: List[OHLCV], i: int, horizon: int) -> float | None:
    j = i + horizon
    if j >= len(candles):
        return None
    px = candles[i].close
    if px <= 0:
        return None
    return (candles[j].close - px) / px


def cascade_sweep(symbol: str, days: int, horizons: List[int]) -> None:
    if not _HAVE_CASCADE:
        print(f"\n== Cascade sweep: SKIPPED (Liquidation-Cascade branch not "
              "merged into this branch yet) ==")
        return
    print(f"\n== Cascade sweep: {symbol} × {days}d × 1m ==")
    candles = download_history(symbol, interval="1m", days=days)
    if len(candles) < 200:
        print(f"  insufficient candles ({len(candles)})")
        return
    print(f"  bars={len(candles)}")
    for horizon in horizons:
        active = []
        exhaust = []
        baseline = []
        for i in range(30, len(candles) - horizon):
            window = candles[: i + 1]
            r = detect_cascade(window)
            fwd = _forward_return(candles, i, horizon)
            if fwd is None:
                continue
            baseline.append(fwd)
            if r.state is CascadeState.CASCADE_ACTIVE:
                active.append(fwd)
            elif r.state is CascadeState.CASCADE_EXHAUSTION:
                exhaust.append(fwd)
        bmean = sum(baseline) / len(baseline) if baseline else 0.0
        amean = sum(active) / len(active) if active else 0.0
        emean = sum(exhaust) / len(exhaust) if exhaust else 0.0
        print(f"  h={horizon:>3}m  baseline n={len(baseline)} "
              f"mean={bmean * 100:+.3f}%  | active n={len(active)} "
              f"mean={amean * 100:+.3f}%  | exhaust n={len(exhaust)} "
              f"mean={emean * 100:+.3f}%")


def pairs_sweep(y_sym: str, x_sym: str, days: int) -> None:
    if not _HAVE_PAIRS:
        print(f"\n== Pairs sweep: SKIPPED (Cross-Asset-Pairs branch not "
              "merged into this branch yet) ==")
        return
    print(f"\n== Pairs sweep: {y_sym} vs {x_sym} × {days}d × 1m ==")
    y = download_history(y_sym, interval="1m", days=days)
    x = download_history(x_sym, interval="1m", days=days)
    # Align by timestamp.
    y_by_ts = {c.timestamp: c.close for c in y}
    aligned_y: List[float] = []
    aligned_x: List[float] = []
    for c in x:
        if c.timestamp in y_by_ts:
            aligned_x.append(c.close)
            aligned_y.append(y_by_ts[c.timestamp])
    print(f"  aligned bars={len(aligned_y)}")
    if len(aligned_y) < 1000:
        print("  insufficient aligned bars")
        return

    fit = fit_pair(aligned_y, aligned_x)
    print(f"  full-sample fit: β={fit.beta:.4f}  ADF p={fit.adf_pvalue:.4f}  "
          f"(cointegrated? {fit.is_cointegrated})")
    print(f"  {'lookback':>8} {'entry':>5} {'exit':>4} "
          f"{'trades':>6} {'win%':>5} {'totalPnL':>9} {'maxDD':>7}")
    for lookback in (480, 720, 1440):
        for entry_z, exit_z in ((1.5, 0.3), (2.0, 0.5), (2.5, 0.5)):
            r = backtest_pair(aligned_y, aligned_x, lookback=lookback,
                              entry_z=entry_z, exit_z=exit_z,
                              refit_every=120)
            print(f"  {lookback:>8} {entry_z:>5.1f} {exit_z:>4.1f} "
                  f"{r.trades:>6} {r.win_rate * 100:>4.1f} "
                  f"{r.total_pnl:>+9.4f} {r.max_drawdown:>7.4f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14,
                        help="Days of 1m history to fetch")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    for sym in symbols:
        cascade_sweep(sym, days=args.days, horizons=[5, 15, 30])

    if "ETHUSDT" in symbols and "BTCUSDT" in symbols:
        pairs_sweep("ETHUSDT", "BTCUSDT", days=args.days)
    if "SOLUSDT" in symbols and "BTCUSDT" in symbols:
        pairs_sweep("SOLUSDT", "BTCUSDT", days=args.days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
