"""Kill-switch system.

Two independent circuit breakers veto new entries when conditions are
hazardous. A kill switch never *creates* a trade — it can only force HOLD. It
sits between analysis and execution as a last line of defence.

* **Macro kill switch** — systemic danger: war, exchange collapse, panic, or
  any macro read at/above the configured risk ceiling.
* **Micro kill switch** — local market hazards: chop zones, liquidity traps
  (thin books), and extreme spreads that would make any fill a bad fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..config.schema import KillSwitchConfig
from ..models import MacroSignal, MicrostructureSignal, RiskLevel

# Macro regimes that always trip the switch, independent of the risk ceiling.
_CRITICAL_REGIMES = {"war", "exchange_collapse", "panic"}


@dataclass(frozen=True)
class KillSwitchResult:
    """Outcome of a kill-switch evaluation."""

    active: bool
    macro_triggered: bool = False
    micro_triggered: bool = False
    reasons: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.active:
            return "kill switch clear"
        return "KILL SWITCH ACTIVE: " + "; ".join(self.reasons)


def evaluate_macro_kill_switch(
    macro: MacroSignal,
    config: KillSwitchConfig,
) -> tuple[bool, List[str]]:
    """Return ``(triggered, reasons)`` for systemic macro hazards."""
    reasons: List[str] = []
    block_rank = RiskLevel(config.macro_risk_block).rank

    if macro.regime_label in _CRITICAL_REGIMES:
        reasons.append(f"macro regime '{macro.regime_label}' is systemically critical")
    if macro.risk_level.rank >= block_rank:
        reasons.append(
            f"macro risk level '{macro.risk_level.value}' at/above ceiling "
            f"'{config.macro_risk_block}'"
        )
    return bool(reasons), reasons


def evaluate_micro_kill_switch(
    micro: MicrostructureSignal,
    config: KillSwitchConfig,
) -> tuple[bool, List[str]]:
    """Return ``(triggered, reasons)`` for local market-structure hazards."""
    reasons: List[str] = []

    if config.block_on_chop and micro.chop_zone:
        reasons.append("chop zone — directionless price action")
    if config.block_on_thin_liquidity and micro.thin_liquidity:
        reasons.append("thin liquidity — liquidity-trap / slippage risk")
    if micro.spread_bps is not None and micro.spread_bps > config.micro_max_spread_bps:
        reasons.append(
            f"extreme spread {micro.spread_bps:.1f} bps " f"> {config.micro_max_spread_bps} bps"
        )
    return bool(reasons), reasons


def evaluate_kill_switch(
    macro: MacroSignal,
    micro: MicrostructureSignal,
    config: KillSwitchConfig,
) -> KillSwitchResult:
    """Evaluate both kill switches and combine them."""
    macro_trig, macro_reasons = evaluate_macro_kill_switch(macro, config)
    micro_trig, micro_reasons = evaluate_micro_kill_switch(micro, config)
    reasons = [f"[macro] {r}" for r in macro_reasons] + [f"[micro] {r}" for r in micro_reasons]
    return KillSwitchResult(
        active=macro_trig or micro_trig,
        macro_triggered=macro_trig,
        micro_triggered=micro_trig,
        reasons=reasons,
    )
