"""Accounting ledger for the paper portfolio.

This module produces an *accounting report* — simulated profit, simulated
loss, fees and per-asset PnL.

IT CONTAINS NO BANK-TRANSFER, WITHDRAWAL, OR PAYMENT CODE. By design there is
no function here that moves money. The platform never touches a bank, a card,
or a real exchange balance. ``grep -ri "bank\\|withdraw\\|wire\\|payout"`` over
this package returns only this notice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from ..paper.broker import TradeRecord


@dataclass(frozen=True)
class AssetPnL:
    """Per-asset PnL summary."""

    symbol: str
    trades: int
    wins: int
    losses: int
    gross_profit: float
    gross_loss: float
    fees: float

    @property
    def net_pnl(self) -> float:
        return self.gross_profit + self.gross_loss


@dataclass(frozen=True)
class AccountingReport:
    """Simulated accounting summary — educational, not financial advice."""

    starting_equity: float
    ending_equity: float
    total_trades: int
    simulated_profit: float  # sum of winning-trade PnL
    simulated_loss: float  # sum of losing-trade PnL (<= 0)
    estimated_fees: float
    per_asset: Dict[str, AssetPnL] = field(default_factory=dict)

    @property
    def net_pnl(self) -> float:
        return self.simulated_profit + self.simulated_loss

    @property
    def return_pct(self) -> float:
        if self.starting_equity <= 0:
            return 0.0
        return (self.ending_equity - self.starting_equity) / self.starting_equity * 100.0


def build_accounting_report(
    trades: List[TradeRecord],
    starting_equity: float,
    ending_equity: float,
) -> AccountingReport:
    """Build an :class:`AccountingReport` from closed paper trades."""
    by_symbol: Dict[str, List[TradeRecord]] = {}
    for trade in trades:
        by_symbol.setdefault(trade.symbol, []).append(trade)

    per_asset: Dict[str, AssetPnL] = {}
    total_profit = 0.0
    total_loss = 0.0
    total_fees = 0.0
    for symbol, sym_trades in by_symbol.items():
        wins = [t for t in sym_trades if t.pnl > 0]
        losses = [t for t in sym_trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = sum(t.pnl for t in losses)
        fees = sum(t.fees for t in sym_trades)
        per_asset[symbol] = AssetPnL(
            symbol=symbol,
            trades=len(sym_trades),
            wins=len(wins),
            losses=len(losses),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            fees=round(fees, 2),
        )
        total_profit += gross_profit
        total_loss += gross_loss
        total_fees += fees

    return AccountingReport(
        starting_equity=starting_equity,
        ending_equity=ending_equity,
        total_trades=len(trades),
        simulated_profit=round(total_profit, 2),
        simulated_loss=round(total_loss, 2),
        estimated_fees=round(total_fees, 2),
        per_asset=per_asset,
    )
