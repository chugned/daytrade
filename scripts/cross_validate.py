#!/usr/bin/env python3
"""Cross-validation: daytrade backtest vs freqtrade backtest.

Item #12 of the engineering plan — verify the freqtrade port produces
results that track daytrade's own backtester on the SAME data. A large
divergence is a porting bug that must be fixed before the live path.

Usage::

    PYTHONPATH=src python3 scripts/cross_validate.py \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT \\
        --days 30 \\
        --freqtrade-result /opt/freqtrade-data/backtest_results/.last_result.json

Produces a side-by-side metrics table and writes the comparison to
``reports/cross_validation_<timestamp>.md``.

If you only want daytrade's number (e.g. before freqtrade is installed),
omit ``--freqtrade-result``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Path bootstrap so the script runs from a fresh shell.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from daytrade.backtest import Backtester  # noqa: E402
from daytrade.config import load_config  # noqa: E402
from daytrade.research.history import download_history  # noqa: E402


@dataclass
class Metrics:
    """The headline numbers we compare across engines."""
    engine: str
    symbol: str
    bars: int
    total_return_pct: float
    win_rate: float
    sharpe_like: float
    max_drawdown_pct: float
    total_trades: int
    avg_win: float = 0.0
    avg_loss: float = 0.0

    @classmethod
    def from_daytrade(cls, symbol: str, m) -> "Metrics":
        return cls(
            engine="daytrade",
            symbol=symbol,
            bars=m.bars,
            total_return_pct=m.total_return_pct,
            win_rate=m.win_rate * 100,
            sharpe_like=m.sharpe_like,
            max_drawdown_pct=m.max_drawdown_pct,
            total_trades=m.total_trades,
            avg_win=m.avg_win,
            avg_loss=m.avg_loss,
        )

    @classmethod
    def from_freqtrade(cls, symbol: str, row: Dict[str, Any]) -> "Metrics":
        """Map a freqtrade backtest result row (per-pair) onto our Metrics."""
        trades = int(row.get("trades", 0) or 0)
        return cls(
            engine="freqtrade",
            symbol=symbol,
            bars=int(row.get("bars_held", row.get("bars", 0)) or 0),
            total_return_pct=float(row.get("profit_total_pct",
                                           row.get("profit_total", 0)) or 0),
            win_rate=float(row.get("winrate", 0) or 0) * 100,
            # freqtrade reports sharpe in some report dialects; fall back to 0.
            sharpe_like=float(row.get("sharpe", 0) or 0),
            max_drawdown_pct=float(row.get("max_drawdown", 0) or 0) * 100,
            total_trades=trades,
            avg_win=float(row.get("profit_mean", 0) or 0),
            avg_loss=float(row.get("profit_min", 0) or 0),
        )


def daytrade_metrics(symbols: List[str], days: int,
                     interval: str = "1h") -> List[Metrics]:
    """Run daytrade's Backtester on each symbol and collect headline metrics."""
    cfg = load_config(load_dotenv_file=False)
    out: List[Metrics] = []
    for s in symbols:
        try:
            candles = download_history(s, interval=interval, days=days)
            if not candles:
                print(f"  [skip] {s}: no candles", flush=True)
                continue
            m = Backtester(cfg).run(candles).metrics
            out.append(Metrics.from_daytrade(s, m))
            print(f"  [ok]   {s}: ret {m.total_return_pct:+.2f}% "
                  f"win {m.win_rate * 100:.1f}% trades {m.total_trades}",
                  flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [err]  {s}: {exc!r}", flush=True)
    return out


def freqtrade_metrics(path: Path, symbols: List[str]) -> List[Metrics]:
    """Parse freqtrade's backtest JSON and extract per-pair metrics.

    Freqtrade writes its backtest results to ``backtest_results/*.json``
    with a ``strategy_comparison`` and a ``results_per_pair`` section.
    We use the per-pair section.
    """
    if not path.exists():
        print(f"  [warn] freqtrade result not found at {path}")
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Freqtrade's structure: { "strategy": { "<StratName>": { "results_per_pair": [...] } } }
    out: List[Metrics] = []
    strategies = payload.get("strategy", {})
    for _name, st in strategies.items():
        for row in st.get("results_per_pair", []):
            pair = row.get("key") or row.get("pair") or ""
            symbol = pair.replace("/", "")     # BTC/USDT -> BTCUSDT
            if symbols and symbol not in symbols:
                continue
            out.append(Metrics.from_freqtrade(symbol, row))
    return out


def render_table(daytrade: List[Metrics],
                 freqtrade: List[Metrics]) -> str:
    """Side-by-side markdown table for the report."""
    ft_by_sym = {m.symbol: m for m in freqtrade}
    lines = [
        "| Symbol | Engine | Return % | Win % | Trades | Sharpe~ | Max DD % |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for dm in daytrade:
        lines.append(
            f"| {dm.symbol} | daytrade | {dm.total_return_pct:+.2f} | "
            f"{dm.win_rate:.1f} | {dm.total_trades} | "
            f"{dm.sharpe_like:.2f} | {dm.max_drawdown_pct:.2f} |")
        fm = ft_by_sym.get(dm.symbol)
        if fm:
            delta = fm.total_return_pct - dm.total_return_pct
            lines.append(
                f"| {fm.symbol} | freqtrade | {fm.total_return_pct:+.2f} | "
                f"{fm.win_rate:.1f} | {fm.total_trades} | "
                f"{fm.sharpe_like:.2f} | {fm.max_drawdown_pct:.2f} |")
            lines.append(
                f"| | **Δ return** | **{delta:+.2f}** | | | | |")
    return "\n".join(lines)


def verdict(daytrade: List[Metrics],
            freqtrade: List[Metrics]) -> str:
    """One-line opinion on whether the two engines agree closely enough."""
    if not freqtrade:
        return ("ℹ️  freqtrade result not supplied — daytrade-only snapshot. "
                "Re-run with --freqtrade-result once the freqtrade backtest "
                "is available.")
    ft_by_sym = {m.symbol: m for m in freqtrade}
    deltas: List[float] = []
    for dm in daytrade:
        fm = ft_by_sym.get(dm.symbol)
        if fm:
            deltas.append(abs(fm.total_return_pct - dm.total_return_pct))
    if not deltas:
        return "⚠️  no overlapping symbols between the two reports."
    mean_delta = sum(deltas) / len(deltas)
    if mean_delta < 5.0:
        return (f"✅  agreement: mean |Δreturn| = {mean_delta:.2f} pp "
                "across overlapping symbols — within reasonable tolerance.")
    if mean_delta < 20.0:
        return (f"⚠️  divergence: mean |Δreturn| = {mean_delta:.2f} pp — "
                "investigate before relying on the freqtrade port.")
    return (f"❌  SHARP DIVERGENCE: mean |Δreturn| = {mean_delta:.2f} pp — "
            "freqtrade port likely has a porting bug; do NOT proceed.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT",
        help="Comma-separated symbols (e.g. BTCUSDT,ETHUSDT).")
    parser.add_argument("--days", type=int, default=30,
        help="Days of history to backtest.")
    parser.add_argument("--interval", default="1h",
        help="Candle interval (1h is the cached default for the research lab).")
    parser.add_argument("--freqtrade-result", type=Path, default=None,
        help="Path to freqtrade's backtest_results/*.json (optional).")
    parser.add_argument("--out", type=Path, default=None,
        help="Where to write the markdown report.")
    args = parser.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    print(f"\n# Cross-validation — {len(symbols)} symbols, {args.days}d {args.interval}")
    print(f"\nDaytrade backtester:")
    dt = daytrade_metrics(symbols, days=args.days, interval=args.interval)

    ft: List[Metrics] = []
    if args.freqtrade_result:
        print(f"\nFreqtrade results: {args.freqtrade_result}")
        ft = freqtrade_metrics(args.freqtrade_result, symbols=symbols)

    table = render_table(dt, ft)
    verdict_line = verdict(dt, ft)

    report = "\n".join([
        "# Cross-validation report",
        f"_Generated: {datetime.now(timezone.utc).isoformat()}_",
        "",
        f"Symbols: {', '.join(symbols)}",
        f"Window: {args.days} days at {args.interval}",
        "",
        "## Side-by-side metrics",
        "",
        table,
        "",
        "## Verdict",
        "",
        verdict_line,
    ])

    out_path = args.out
    if out_path is None:
        out_path = _REPO / "reports" / f"cross_validation_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    print("\n" + "=" * 64)
    print(table)
    print("=" * 64)
    print("\n" + verdict_line)
    print(f"\nReport written to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
