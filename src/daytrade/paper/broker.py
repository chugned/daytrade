"""Paper-trading broker.

``PaperBroker`` is the ONLY broker in this codebase. It matches orders against
simulated liquidity and tracks an in-memory portfolio. It has no network, no
credentials and no order-entry endpoint.

Spot, long-only, no leverage: you can only sell what you hold. The class
exposes a deliberately-named live boundary, :meth:`connect_live`, whose sole
job is to raise — so the "real trading is disabled" contract is visible right
where someone might look for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from ..config.schema import RiskConfig
from ..models import Fill, PortfolioSnapshot, Position, Side
from ..risk.execution import simulate_fill
from ..runtime import get_logger
from ..safety.guard import assert_paper_only, forbid_real_trading

_log = get_logger("paper.broker")
_EPS = 1e-12


@dataclass(frozen=True)
class TradeRecord:
    """A completed round-trip (open -> fully closed) trade."""

    symbol: str
    quantity: float
    entry_price: float
    exit_price: float
    opened_at: datetime
    closed_at: datetime
    pnl: float  # net of all fees
    fees: float

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    @property
    def return_pct(self) -> float:
        cost = self.entry_price * self.quantity
        return self.pnl / cost if cost > _EPS else 0.0


@dataclass
class _OpenLot:
    """Mutable bookkeeping for the currently-open position in one symbol."""

    quantity: float = 0.0
    avg_price: float = 0.0
    fees_paid: float = 0.0
    opened_at: Optional[datetime] = None


class PaperBroker:
    """An in-memory, simulation-only broker."""

    def __init__(self, starting_cash: float, base_currency: str = "USDT") -> None:
        assert_paper_only()
        if starting_cash <= 0:
            raise ValueError("starting_cash must be > 0")
        self.base_currency = base_currency
        self._starting_cash = starting_cash
        self._cash = starting_cash
        self._realized_pnl = 0.0
        self._lots: Dict[str, _OpenLot] = {}
        self._fills: List[Fill] = []
        self._trades: List[TradeRecord] = []

    # -- accessors -----------------------------------------------------------

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def starting_cash(self) -> float:
        return self._starting_cash

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def fills(self) -> List[Fill]:
        return list(self._fills)

    @property
    def closed_trades(self) -> List[TradeRecord]:
        return list(self._trades)

    def position(self, symbol: str) -> Position:
        lot = self._lots.get(symbol)
        if lot is None or lot.quantity <= _EPS:
            return Position(symbol=symbol, quantity=0.0, avg_entry_price=0.0)
        return Position(
            symbol=symbol,
            quantity=round(lot.quantity, 10),
            avg_entry_price=round(lot.avg_price, 8),
        )

    def has_position(self, symbol: str) -> bool:
        lot = self._lots.get(symbol)
        return lot is not None and lot.quantity > _EPS

    # -- order entry ---------------------------------------------------------

    def submit_market_order(
        self,
        order_id: str,
        symbol: str,
        side: Side,
        quantity: float,
        reference_price: float,
        available_liquidity: float,
        risk_config: RiskConfig,
        timestamp: datetime,
    ) -> Fill:
        """Submit a simulated market order and apply the resulting fill.

        SELL quantity is clamped to the held position — this is spot,
        long-only; you cannot sell what you do not own.
        """
        if side is Side.SELL:
            held = self._lots.get(symbol)
            held_qty = held.quantity if held else 0.0
            if held_qty <= _EPS:
                raise ValueError(f"cannot SELL {symbol}: no open position")
            quantity = min(quantity, held_qty)

        fill = simulate_fill(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            reference_price=reference_price,
            available_liquidity=available_liquidity,
            config=risk_config,
            timestamp=timestamp,
        )
        self._apply_fill(fill)
        return fill

    def apply_external_fill(self, fill: Fill) -> None:
        """Apply a fill produced elsewhere (e.g. the backtester) to the book."""
        self._apply_fill(fill)

    # -- portfolio state -----------------------------------------------------

    def snapshot(
        self,
        timestamp: datetime,
        mark_prices: Dict[str, float],
    ) -> PortfolioSnapshot:
        """Return an immutable portfolio snapshot marked at ``mark_prices``."""
        positions = {
            sym: self.position(sym) for sym, lot in self._lots.items() if lot.quantity > _EPS
        }
        return PortfolioSnapshot(
            timestamp=timestamp,
            cash=round(self._cash, 8),
            positions=positions,
            mark_prices=dict(mark_prices),
            realized_pnl=round(self._realized_pnl, 8),
        )

    def equity(self, mark_prices: Dict[str, float]) -> float:
        """Total account value: cash + marked-to-market positions."""
        total = self._cash
        for sym, lot in self._lots.items():
            if lot.quantity > _EPS:
                total += lot.quantity * mark_prices.get(sym, lot.avg_price)
        return total

    # -- the live boundary (always raises) -----------------------------------

    def connect_live(self, *args, **kwargs) -> None:
        """The real-broker connection boundary. It does not exist — by design.

        Raises:
            NotImplementedError: always.
        """
        forbid_real_trading("PaperBroker.connect_live")

    # -- internals -----------------------------------------------------------

    def _apply_fill(self, fill: Fill) -> None:
        lot = self._lots.setdefault(fill.symbol, _OpenLot())
        if fill.side is Side.BUY:
            self._cash -= fill.notional + fill.fee
            new_qty = lot.quantity + fill.quantity
            lot.avg_price = (lot.avg_price * lot.quantity + fill.price * fill.quantity) / new_qty
            lot.quantity = new_qty
            lot.fees_paid += fill.fee
            if lot.opened_at is None:
                lot.opened_at = fill.timestamp
        else:  # SELL — reduce / close the long
            sold = min(fill.quantity, lot.quantity)
            self._cash += fill.price * sold - fill.fee
            realized = (fill.price - lot.avg_price) * sold
            self._realized_pnl += realized
            lot.fees_paid += fill.fee
            lot.quantity -= sold
            if lot.quantity <= _EPS:
                # Position fully closed -> record the round-trip trade.
                self._trades.append(
                    TradeRecord(
                        symbol=fill.symbol,
                        quantity=round(sold, 10),
                        entry_price=round(lot.avg_price, 8),
                        exit_price=round(fill.price, 8),
                        opened_at=lot.opened_at or fill.timestamp,
                        closed_at=fill.timestamp,
                        pnl=round(realized - lot.fees_paid, 8),
                        fees=round(lot.fees_paid, 8),
                    )
                )
                self._lots[fill.symbol] = _OpenLot()
        self._fills.append(fill)
        _log.debug(
            "fill applied: %s %s %.6f @ %.2f",
            fill.side.value,
            fill.symbol,
            fill.quantity,
            fill.price,
        )
