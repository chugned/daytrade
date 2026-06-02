"""Rich console reporting.

Renders pipeline results, backtests and validation reports as readable
terminal output. Every rendering surfaces the *assumptions and warnings*
alongside the numbers — a report that hides its caveats is misleading.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..models import Action, BacktestMetrics, WalkForwardReport

if TYPE_CHECKING:  # avoid import cycles at runtime
    from ..backtest.engine import BacktestResult
    from ..pipeline import PipelineResult

_ACTION_STYLE = {Action.BUY: "bold green", Action.SELL: "bold red", Action.HOLD: "bold yellow"}


def _signal_table(result: PipelineResult) -> Table:
    table = Table(title="Analysis layers", title_style="bold cyan", header_style="bold")
    table.add_column("Layer")
    table.add_column("Bias")
    table.add_column("Score", justify="right")
    table.add_column("Confidence", justify="right")
    for name, sig in (
        ("Technical", result.technical),
        ("Microstructure", result.microstructure),
        ("Macro", result.macro),
        ("ML", result.ml),
    ):
        table.add_row(name, str(sig.bias.value), f"{sig.score:+.3f}", f"{sig.confidence:.2f}")
    return table


def render_decision(result: PipelineResult, console: Console | None = None) -> None:
    """Render a full decision report for one pipeline pass."""
    console = console or Console()
    d = result.decision

    # --- market state ---
    console.print(
        Panel(
            (
                f"Symbol: [bold]{d.symbol}[/bold]    "
                f"Reference price: [bold]{result.reference_price:,.2f}[/bold]    "
                f"ATR: {result.atr:,.2f}"
                if result.atr
                else f"Symbol: [bold]{d.symbol}[/bold]    "
                f"Reference price: [bold]{result.reference_price:,.2f}[/bold]"
            ),
            title="Market state",
            border_style="cyan",
        )
    )

    console.print(_signal_table(result))

    # --- decision ---
    style = _ACTION_STYLE.get(d.action, "white")
    body = Text()
    body.append(f"{d.action.value.upper()}", style=style)
    body.append(f"   confidence {d.confidence:.2f}   fused score {d.fused_score:+.3f}\n")
    if d.is_actionable:
        body.append(
            f"entry {d.entry:,.2f}   stop {d.stop:,.2f}   "
            f"target {d.target:,.2f}   R:R {d.risk_reward:.2f}\n"
        )
    console.print(Panel(body, title="Decision", border_style=style.split()[-1]))

    # --- kill switch / warnings ---
    if result.kill_switch.active:
        console.print(
            Panel(
                "\n".join(f"• {r}" for r in result.kill_switch.reasons),
                title="⚠ KILL SWITCH ACTIVE",
                border_style="red",
            )
        )

    # --- reasoning ---
    console.print(
        Panel(
            "\n".join(f"• {r}" for r in d.reasoning),
            title="Reasoning",
            border_style="blue",
        )
    )

    console.print(
        Panel(
            "Execution assumptions: simulated fills only — adverse slippage, "
            "fees on both sides, partial fills capped by liquidity, modeled "
            "latency. This platform CANNOT place real trades.\n"
            "Backtests are NOT reality.",
            title="Assumptions & safety",
            border_style="magenta",
        )
    )


def render_backtest(result: BacktestResult, console: Console | None = None) -> None:
    """Render backtest metrics."""
    console = console or Console()
    m: BacktestMetrics = result.metrics

    table = Table(title=f"Backtest — {m.symbol}", title_style="bold cyan", header_style="bold")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    rows = [
        ("Bars", f"{m.bars}"),
        ("Decisions / Holds", f"{result.decisions} / {result.holds}"),
        ("Trades", f"{m.total_trades} ({m.winning_trades}W / {m.losing_trades}L)"),
        ("Win rate", f"{m.win_rate:.1%}"),
        ("Total return", f"{m.total_return_pct:+.2f}%"),
        ("Starting / ending equity", f"{m.starting_equity:,.2f} -> {m.ending_equity:,.2f}"),
        ("Profit factor", f"{m.profit_factor:.2f}"),
        ("Max drawdown", f"{m.max_drawdown_pct:.2f}%"),
        ("Sharpe-like", f"{m.sharpe_like:.2f}"),
        ("Exposure", f"{m.exposure_pct:.1%}"),
        ("Total fees / slippage", f"{m.total_fees:,.2f} / {m.total_slippage:,.2f}"),
    ]
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)

    if m.warnings:
        console.print(
            Panel(
                "\n".join(f"• {w}" for w in m.warnings),
                title="⚠ Realism warnings",
                border_style="yellow",
            )
        )


def render_walkforward(report: WalkForwardReport, console: Console | None = None) -> None:
    """Render a walk-forward validation report."""
    console = console or Console()
    table = Table(
        title=f"Walk-forward validation — {report.model_kind}",
        title_style="bold cyan",
        header_style="bold",
    )
    for col in ("Fold", "Train", "Test", "Train acc", "Test acc", "Test AUC", "Overfit gap"):
        table.add_column(col, justify="right")
    for f in report.folds:
        table.add_row(
            str(f.fold),
            str(f.train_samples),
            str(f.test_samples),
            f"{f.train_accuracy:.3f}",
            f"{f.test_accuracy:.3f}",
            f"{f.test_auc:.3f}",
            f"{f.overfit_gap:+.3f}",
        )
    console.print(table)
    console.print(
        f"Mean test accuracy: [bold]{report.mean_test_accuracy:.3f}[/bold]   "
        f"Mean overfit gap: [bold]{report.mean_overfit_gap:+.3f}[/bold]   "
        f"Leakage suspected: [bold]{report.leakage_suspected}[/bold]"
    )
    if report.warnings:
        console.print(
            Panel(
                "\n".join(f"• {w}" for w in report.warnings),
                title="⚠ Validation warnings",
                border_style="yellow",
            )
        )
