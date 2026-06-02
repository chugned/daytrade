"""The historical research lab.

Runs the *same* backtest + walk-forward engines the live observatory uses,
but over years of real downloaded history instead of one slow 30-day window.
This collapses the feedback loop from a month per experiment to minutes.

The lab is deliberately ruthless: its job is to tell you a strategy has no
edge — quickly, on real data, after costs — so the rare one that does is
worth a live 30-day confirmation. It is research only; it places no orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..backtest import Backtester
from ..config.schema import AppConfig
from ..models import BacktestMetrics, WalkForwardReport
from ..runtime import get_logger
from ..validation import walk_forward_validate
from .history import download_history

_log = get_logger("research.lab")


@dataclass
class ResearchResult:
    """The outcome of evaluating one symbol over real history."""

    symbol: str
    interval: str
    bars: int
    start: str
    end: str
    backtest: Optional[BacktestMetrics] = None
    walkforward: Optional[WalkForwardReport] = None
    verdict: str = "INSUFFICIENT DATA"
    notes: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _verdict(
    bt: BacktestMetrics, wf: WalkForwardReport, config: AppConfig
) -> tuple[str, List[str]]:
    """Turn backtest + walk-forward results into one honest verdict.

    The default — and most common, correct — answer is 'no edge'.
    """
    notes: List[str] = []
    acc = wf.mean_test_accuracy
    sharpe_cap = config.backtest.sharpe_warn_threshold

    if wf.n_folds == 0:
        return "INSUFFICIENT DATA", ["not enough history for walk-forward"]
    if wf.leakage_suspected:
        notes.append(f"walk-forward test accuracy {acc:.0%} is implausibly high")
        return "SUSPECT — likely lookahead / leakage, not a real edge", notes
    if bt.sharpe_like > sharpe_cap:
        notes.append(
            f"backtest Sharpe-like {bt.sharpe_like:.1f} exceeds the "
            f"{sharpe_cap} realism ceiling"
        )
        return "OVERFIT — backtest too good to be real", notes
    if acc < 0.50:
        notes.append(f"walk-forward accuracy {acc:.0%} is below a coin flip")
        return "NO EDGE — worse than a coin flip out-of-sample", notes
    if acc < 0.53:
        notes.append(f"walk-forward accuracy {acc:.0%} is within noise of 50%")
        return "NO MEANINGFUL EDGE — indistinguishable from chance", notes
    if bt.total_return_pct <= 0:
        notes.append(f"backtest return {bt.total_return_pct:+.1f}% after costs")
        return "NO EDGE — strategy loses money after fees & slippage", notes
    notes.append(
        f"out-of-sample accuracy {acc:.0%}, backtest " f"{bt.total_return_pct:+.1f}% after costs"
    )
    return ("WEAK SIGNAL — promising but unproven; needs live " "out-of-sample confirmation"), notes


def run_research(
    symbols: List[str],
    interval: str = "1h",
    days: int = 365,
    config: Optional[AppConfig] = None,
    model=None,
) -> List[ResearchResult]:
    """Evaluate each symbol over real downloaded history."""
    from ..config import load_config

    config = config or load_config(load_dotenv_file=False)
    results: List[ResearchResult] = []

    for symbol in symbols:
        symbol = symbol.upper().strip()
        try:
            candles = download_history(symbol, interval=interval, days=days)
            if len(candles) < config.backtest.warmup_bars + 60:
                results.append(
                    ResearchResult(
                        symbol=symbol,
                        interval=interval,
                        bars=len(candles),
                        start="",
                        end="",
                        verdict="INSUFFICIENT DATA",
                        notes=[f"only {len(candles)} bars of history"],
                    )
                )
                continue

            _log.info("research: backtesting %s on %d real %s bars", symbol, len(candles), interval)
            bt = Backtester(config, model).run(candles)
            wf = walk_forward_validate(candles, config)
            verdict, notes = _verdict(bt.metrics, wf, config)
            results.append(
                ResearchResult(
                    symbol=symbol,
                    interval=interval,
                    bars=len(candles),
                    start=candles[0].timestamp.isoformat(),
                    end=candles[-1].timestamp.isoformat(),
                    backtest=bt.metrics,
                    walkforward=wf,
                    verdict=verdict,
                    notes=notes,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one symbol must not abort the lab
            _log.exception("research failed for %s", symbol)
            results.append(
                ResearchResult(
                    symbol=symbol,
                    interval=interval,
                    bars=0,
                    start="",
                    end="",
                    verdict="ERROR",
                    error=repr(exc),
                )
            )
    return results


def render_research(results: List[ResearchResult], console: Optional[Console] = None) -> None:
    """Print a research-lab report for a set of results."""
    console = console or Console()
    table = Table(title="Historical research — real data, realistic costs", header_style="bold")
    for col in (
        "Symbol",
        "Bars",
        "Backtest return",
        "Win rate",
        "Sharpe~",
        "Max DD",
        "WF test acc.",
        "Overfit gap",
        "Verdict",
    ):
        table.add_column(col)

    for r in results:
        if r.error:
            table.add_row(r.symbol, "—", "—", "—", "—", "—", "—", "—", "[red]ERROR[/red]")
            continue
        bt, wf = r.backtest, r.walkforward
        if bt is None or wf is None:
            table.add_row(r.symbol, str(r.bars), "—", "—", "—", "—", "—", "—", r.verdict)
            continue
        edge = "WEAK SIGNAL" in r.verdict
        vstyle = "yellow" if edge else "red"
        table.add_row(
            r.symbol,
            str(r.bars),
            f"{bt.total_return_pct:+.1f}%",
            f"{bt.win_rate:.0%}",
            f"{bt.sharpe_like:.2f}",
            f"{bt.max_drawdown_pct:.1f}%",
            f"{wf.mean_test_accuracy:.0%}",
            f"{wf.mean_overfit_gap:+.2f}",
            f"[{vstyle}]{r.verdict.split(' — ')[0]}[/{vstyle}]",
        )
    console.print(table)

    for r in results:
        if r.error:
            console.print(f"[red]{r.symbol}: {r.error}[/red]")
            continue
        body = f"[bold]{r.verdict}[/bold]\n" + "\n".join(f"• {n}" for n in r.notes)
        edge = "WEAK SIGNAL" in r.verdict
        console.print(
            Panel(
                body,
                title=f"{r.symbol} — {r.interval} · " f"{r.bars} bars",
                border_style="yellow" if edge else "red",
            )
        )

    console.print(
        Panel(
            "Research over real history with modeled fees & slippage. Backtests "
            "are NOT reality — they omit competition, your own market impact, and "
            "the future. A 'weak signal' is a candidate for live observation, not "
            "a green light to trade. Most honest verdicts are 'no edge'.",
            title="How to read this",
            border_style="magenta",
        )
    )
