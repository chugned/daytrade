"""Position sizing — risk-based, with a hard notional cap.

Size is chosen so that the loss taken if price travels from entry to stop
equals a fixed fraction of equity (``risk_per_trade``). A separate cap
(``max_position_pct``) limits how much of the account a single position may
represent, regardless of how tight the stop is.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config.schema import RiskConfig

_EPS = 1e-12


@dataclass(frozen=True)
class SizingResult:
    """Outcome of a sizing calculation."""

    quantity: float
    risk_amount: float  # currency risked between entry and stop
    notional: float  # entry_price * quantity
    capped_by_notional: bool  # True if the notional cap bound the size
    reason: str = ""

    @property
    def is_tradeable(self) -> bool:
        return self.quantity > 0


def position_size(
    equity: float,
    entry: float,
    stop: float,
    config: RiskConfig,
) -> SizingResult:
    """Compute a risk-based position size.

    Args:
        equity: current account equity.
        entry: planned entry price.
        stop: planned stop price (must differ from entry).

    Returns:
        A :class:`SizingResult`; ``quantity`` is 0 when the trade cannot be
        sized (no equity, or entry == stop).
    """
    if equity <= 0:
        return SizingResult(0.0, 0.0, 0.0, False, "no equity")
    if entry <= 0:
        return SizingResult(0.0, 0.0, 0.0, False, "invalid entry price")

    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= _EPS:
        return SizingResult(0.0, 0.0, 0.0, False, "entry and stop are equal")

    # Units such that (entry - stop) * units == equity * risk_per_trade.
    risk_budget = equity * config.risk_per_trade
    qty = risk_budget / risk_per_unit

    # Hard cap: position notional must not exceed max_position_pct of equity.
    max_notional = equity * config.max_position_pct
    capped = False
    if qty * entry > max_notional:
        qty = max_notional / entry
        capped = True

    return SizingResult(
        quantity=round(qty, 10),
        risk_amount=round(qty * risk_per_unit, 8),
        notional=round(qty * entry, 8),
        capped_by_notional=capped,
        reason="notional cap applied" if capped else "risk-based size",
    )
