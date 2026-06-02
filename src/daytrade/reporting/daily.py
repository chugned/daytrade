"""Daily operations report.

Summarizes a paper/sandbox session the way an operator wants to review it at
the end of a day: what traded, what was skipped and why, the PnL and drawdown,
the best and worst decisions, and any overfitting or liquidity warnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..paper.broker import TradeRecord

if TYPE_CHECKING:
    from ..backtest.engine import BacktestResult

_SAFETY_NOTE = (
    "EDUCATIONAL ONLY — simulated paper/sandbox results. "
    "No real orders. Backtests are NOT reality."
)

# Skip-reason fragments that count as liquidity-related warnings.
_LIQUIDITY_FRAGMENTS = ("liquidity", "kill switch", "spread", "thin")


@dataclass(frozen=True)
class DailyReport:
    """A reviewable end-of-session operations summary."""

    label: str
    trades_taken: int
    skipped_total: int
    skipped_reasons: Dict[str, int] = field(default_factory=dict)
    net_pnl: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    best_trade: Optional[TradeRecord] = None
    worst_trade: Optional[TradeRecord] = None
    overfitting_warnings: List[str] = field(default_factory=list)
    liquidity_warnings: List[str] = field(default_factory=list)


def build_daily_report(result: BacktestResult, label: str = "session") -> DailyReport:
    """Build a :class:`DailyReport` from a completed backtest/paper run."""
    trades = list(result.trades)
    metrics = result.metrics

    best = max(trades, key=lambda t: t.pnl) if trades else None
    worst = min(trades, key=lambda t: t.pnl) if trades else None

    overfitting = [
        w
        for w in metrics.warnings
        if any(k in w.lower() for k in ("overfit", "sharpe", "win rate", "reality"))
    ]
    liquidity = [
        f"{reason} (x{count})"
        for reason, count in result.skipped_reasons.items()
        if any(frag in reason.lower() for frag in _LIQUIDITY_FRAGMENTS)
    ]

    return DailyReport(
        label=label,
        trades_taken=metrics.total_trades,
        skipped_total=sum(result.skipped_reasons.values()),
        skipped_reasons=dict(result.skipped_reasons),
        net_pnl=round(metrics.ending_equity - metrics.starting_equity, 2),
        total_return_pct=metrics.total_return_pct,
        max_drawdown_pct=metrics.max_drawdown_pct,
        win_rate=metrics.win_rate,
        best_trade=best,
        worst_trade=worst,
        overfitting_warnings=overfitting,
        liquidity_warnings=liquidity,
    )


def _trade_line(trade: Optional[TradeRecord]) -> str:
    if trade is None:
        return "—"
    return (
        f"{trade.symbol} {trade.quantity:.4f} @ {trade.entry_price:,.2f} "
        f"-> {trade.exit_price:,.2f}  PnL {trade.pnl:+,.2f} "
        f"({trade.return_pct * 100:+.2f}%)"
    )


def render_daily_report(report: DailyReport, console: Optional[Console] = None) -> None:
    """Render a :class:`DailyReport` to the console."""
    console = console or Console()
    console.rule(f"[bold]Daily report — {report.label}")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("k", style="bold")
    table.add_column("v")
    table.add_row("Trades taken", str(report.trades_taken))
    table.add_row("Skipped opportunities", str(report.skipped_total))
    table.add_row("Net PnL", f"{report.net_pnl:+,.2f}")
    table.add_row("Total return", f"{report.total_return_pct:+.2f}%")
    table.add_row("Max drawdown", f"{report.max_drawdown_pct:.2f}%")
    table.add_row("Win rate", f"{report.win_rate:.1%}")
    console.print(Panel(table, title="Session summary", border_style="cyan"))

    if report.skipped_reasons:
        skip = Table(header_style="bold")
        skip.add_column("Skipped trade reason")
        skip.add_column("Count", justify="right")
        for reason, count in sorted(report.skipped_reasons.items(), key=lambda kv: -kv[1]):
            skip.add_row(reason, str(count))
        console.print(skip)

    console.print(
        Panel(
            f"Best decision:  {_trade_line(report.best_trade)}\n"
            f"Worst decision: {_trade_line(report.worst_trade)}",
            title="Best / worst decision",
            border_style="blue",
        )
    )

    if report.overfitting_warnings:
        console.print(
            Panel(
                "\n".join(f"• {w}" for w in report.overfitting_warnings),
                title="⚠ Overfitting / realism warnings",
                border_style="yellow",
            )
        )
    if report.liquidity_warnings:
        console.print(
            Panel(
                "\n".join(f"• {w}" for w in report.liquidity_warnings),
                title="⚠ Liquidity warnings",
                border_style="yellow",
            )
        )
    console.print(f"[dim]{_SAFETY_NOTE}[/dim]")


def daily_report_dict(report: DailyReport) -> Dict[str, Any]:
    """Serialize a daily report to a JSON-ready dict."""

    def _trade(t: Optional[TradeRecord]) -> Optional[Dict[str, Any]]:
        if t is None:
            return None
        return {
            "symbol": t.symbol,
            "quantity": t.quantity,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl": t.pnl,
            "return_pct": t.return_pct,
        }

    return {
        "safety_note": _SAFETY_NOTE,
        "label": report.label,
        "trades_taken": report.trades_taken,
        "skipped_total": report.skipped_total,
        "skipped_reasons": report.skipped_reasons,
        "net_pnl": report.net_pnl,
        "total_return_pct": report.total_return_pct,
        "max_drawdown_pct": report.max_drawdown_pct,
        "win_rate": report.win_rate,
        "best_trade": _trade(report.best_trade),
        "worst_trade": _trade(report.worst_trade),
        "overfitting_warnings": report.overfitting_warnings,
        "liquidity_warnings": report.liquidity_warnings,
    }


def daily_report_markdown(report: DailyReport) -> str:
    """Render a daily report as Markdown."""
    lines = [
        f"# Daily report — {report.label}",
        "",
        f"> {_SAFETY_NOTE}",
        "",
        f"- **Trades taken:** {report.trades_taken}",
        f"- **Skipped opportunities:** {report.skipped_total}",
        f"- **Net PnL:** {report.net_pnl:+,.2f}",
        f"- **Total return:** {report.total_return_pct:+.2f}%",
        f"- **Max drawdown:** {report.max_drawdown_pct:.2f}%",
        f"- **Win rate:** {report.win_rate:.1%}",
        "",
        "## Skipped trades",
        "",
    ]
    for reason, count in sorted(report.skipped_reasons.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {reason}: {count}")
    lines += [
        "",
        "## Best / worst decision",
        "",
        f"- Best: {_trade_line(report.best_trade)}",
        f"- Worst: {_trade_line(report.worst_trade)}",
    ]
    if report.overfitting_warnings:
        lines += ["", "## Overfitting / realism warnings", ""]
        lines += [f"- {w}" for w in report.overfitting_warnings]
    if report.liquidity_warnings:
        lines += ["", "## Liquidity warnings", ""]
        lines += [f"- {w}" for w in report.liquidity_warnings]
    return "\n".join(lines) + "\n"
