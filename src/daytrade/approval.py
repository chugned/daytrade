"""Manual approval mode.

Before any trade is executed — even a paper trade — the operator sees a full
trade card and must explicitly type the confirmation phrase. Nothing executes
on a default answer, a timeout, or an empty line. The intent is to make
"execute a trade" a deliberate, conscious act, never an accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config.schema import ApprovalConfig
from .models import Action


@dataclass(frozen=True)
class TradeProposal:
    """Everything the operator needs to approve or reject one trade."""

    symbol: str
    action: Action
    entry: float
    stop: float
    target: float
    confidence: float
    quantity: float
    risk_amount: float
    expected_slippage_cost: float
    expected_fee: float
    reasoning: List[str] = field(default_factory=list)
    liquidity_warning: Optional[str] = None
    kill_switch_active: bool = False
    kill_switch_reasons: List[str] = field(default_factory=list)
    execution_mode: str = "simulated"  # 'simulated' | 'testnet'

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry - self.stop)
        reward = abs(self.target - self.entry)
        return reward / risk if risk > 0 else 0.0

    @property
    def notional(self) -> float:
        return self.entry * self.quantity


@dataclass(frozen=True)
class ApprovalDecision:
    """The outcome of an approval request."""

    approved: bool
    reason: str


def render_approval_card(proposal: TradeProposal, console: Optional[Console] = None) -> None:
    """Print the full trade card the operator must review."""
    console = console or Console()
    mode = proposal.execution_mode.upper()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("k", style="bold")
    table.add_column("v")
    table.add_row("Asset", proposal.symbol)
    table.add_row("Action", f"[bold]{proposal.action.value.upper()}[/bold]")
    table.add_row("Execution", f"{mode} (no real money)")
    table.add_row("Entry", f"{proposal.entry:,.2f}")
    table.add_row("Stop", f"{proposal.stop:,.2f}")
    table.add_row("Target", f"{proposal.target:,.2f}")
    table.add_row("Risk / Reward", f"{proposal.risk_reward:.2f}")
    table.add_row("Confidence", f"{proposal.confidence:.2f}")
    table.add_row("Quantity", f"{proposal.quantity:.6f}")
    table.add_row("Notional", f"{proposal.notional:,.2f}")
    table.add_row("Risk amount", f"{proposal.risk_amount:,.2f}")
    table.add_row("Expected slippage", f"{proposal.expected_slippage_cost:,.2f}")
    table.add_row("Expected fee", f"{proposal.expected_fee:,.2f}")

    console.print(Panel(table, title="⚖  TRADE PROPOSAL — manual approval", border_style="cyan"))

    if proposal.reasoning:
        console.print(
            Panel(
                "\n".join(f"• {r}" for r in proposal.reasoning), title="Reason", border_style="blue"
            )
        )

    liq = proposal.liquidity_warning or "none"
    liq_style = "yellow" if proposal.liquidity_warning else "green"
    console.print(Panel(f"Liquidity warning: {liq}", border_style=liq_style))

    if proposal.kill_switch_active:
        console.print(
            Panel(
                "\n".join(f"• {r}" for r in proposal.kill_switch_reasons),
                title="⚠ KILL SWITCH ACTIVE — trade will be blocked",
                border_style="red",
            )
        )
    else:
        console.print(Panel("Kill switch: clear", border_style="green"))


def request_approval(
    proposal: TradeProposal,
    config: ApprovalConfig,
    console: Optional[Console] = None,
    *,
    input_fn: Callable[[str], str] = input,
) -> ApprovalDecision:
    """Render the trade card and require an explicit typed confirmation.

    Args:
        input_fn: the function used to read the operator's reply (injectable
            for testing). Defaults to the builtin ``input``.

    A trade is approved ONLY if the operator types the exact confirmation
    phrase. A kill-switch-active proposal is rejected before any prompt.
    """
    console = console or Console()
    render_approval_card(proposal, console)

    if proposal.kill_switch_active:
        return ApprovalDecision(False, "kill switch active — trade blocked")
    if proposal.action is Action.HOLD:
        return ApprovalDecision(False, "action is HOLD — nothing to execute")

    if not config.require_manual_approval:
        return ApprovalDecision(True, "manual approval disabled in config")

    phrase = config.confirmation_phrase
    console.print(
        f"\n[bold]TYPE {phrase} TO PAPER-EXECUTE[/bold] " f"(anything else cancels): ", end=""
    )
    try:
        reply = input_fn("")
    except (EOFError, KeyboardInterrupt):
        return ApprovalDecision(False, "no confirmation received — cancelled")

    if reply.strip() == phrase:
        return ApprovalDecision(True, f"operator confirmed with '{phrase}'")
    return ApprovalDecision(False, f"operator did not type '{phrase}' — cancelled")
