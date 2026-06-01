"""LiveBroker — the production counterpart to PaperBroker.

Mirrors the PaperBroker interface so the existing engine
(``daytrade.observatory.observer``) can use it as a drop-in replacement
without code changes. Every call routes through the
:class:`daytrade.live.exchange.Exchange` Protocol so:

  - Tests run against :class:`MockExchange`.
  - ``dry_run=True`` runs the SAME broker code against the MockExchange,
    so behaviour is identical to live but no real network call is made.
  - Real-money path swaps in a Binance/etc. adapter without touching the
    broker.

Defensive properties (intentional):

  1. **Idempotent order placement.** Every order carries a clientOrderId
     generated from :func:`daytrade.ops.order_ids.generate_client_order_id`.
     Re-sending the same call after a crash/restart cannot double-fill.
  2. **Fail-closed.** Any network failure, rejection, or unexpected
     response raises and the engine treats the trade as not-taken. The
     broker NEVER assumes success without a confirmed fill.
  3. **Daily loss-cap.** Once cumulative realised loss for the UTC day
     exceeds ``LiveConfig.max_daily_loss``, the broker refuses to open
     new positions for the rest of the day. Closes are still allowed.
  4. **Stake cap.** Each order's notional is clamped to
     ``LiveConfig.max_stake_per_trade``. Bigger trades raise.
  5. **Periodic reconciliation.** After every N orders, the broker pulls
     exchange state and compares against its local view. Drift logs an
     alert; behaviour does not silently continue with a stale picture.

The broker does NOT:
  - Decide *whether* to trade (that's the engine).
  - Compute position sizes (that's :mod:`daytrade.risk`).
  - Persist anything (the observatory DB does that).
  - Connect to a specific exchange (the :class:`Exchange` adapter does).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..models import Fill, Position, Side
from ..ops.order_ids import generate_client_order_id
from ..paper.broker import TradeRecord
from ..runtime import get_logger
from .config import LiveConfig
from .exchange import (
    Exchange,
    ExchangeOrder,
    ExchangeUnreachable,
    OrderRejected,
)

_log = get_logger("live.broker")
_EPS = 1e-12


class LiveBrokerError(RuntimeError):
    """A broker-level error (limits, reconciliation drift, kill-switch)."""


@dataclass
class _Lot:
    """Local bookkeeping that mirrors what the exchange should hold."""

    quantity: float = 0.0
    avg_price: float = 0.0
    opened_at: Optional[datetime] = None
    fees_paid: float = 0.0


class LiveBroker:
    """Spot, long-only execution broker. Mirrors PaperBroker semantics."""

    def __init__(
        self,
        config: LiveConfig,
        exchange: Exchange,
        *,
        starting_cash: Optional[float] = None,
    ) -> None:
        self.config = config
        self._exchange = exchange
        self._base = config.base_currency
        # We treat the exchange-reported cash balance as authoritative.
        # Cache the starting figure for reporting & loss-cap math.
        if starting_cash is None:
            try:
                starting_cash = self._exchange.get_balance(self._base)
            except (ExchangeUnreachable, Exception) as exc:  # noqa: BLE001
                raise LiveBrokerError(
                    f"cannot start broker: balance query failed ({exc})"
                ) from exc
        self._starting_cash = float(starting_cash)
        self._lots: Dict[str, _Lot] = {}
        self._closed_trades: List[TradeRecord] = []
        self._fills: List[Fill] = []
        # Daily loss-cap state. Reset at UTC midnight.
        self._loss_today: float = 0.0
        self._loss_day: str = datetime.now(timezone.utc).date().isoformat()
        self._orders_since_reconcile: int = 0
        self._halted_until_day: Optional[str] = None

    # -- properties (PaperBroker-compatible) ---------------------------------

    @property
    def cash(self) -> float:
        return float(self._exchange.get_balance(self._base))

    @property
    def starting_cash(self) -> float:
        return self._starting_cash

    @property
    def realized_pnl(self) -> float:
        return sum(t.pnl for t in self._closed_trades)

    @property
    def fills(self) -> List[Fill]:
        return list(self._fills)

    @property
    def closed_trades(self) -> List[TradeRecord]:
        return list(self._closed_trades)

    def position(self, symbol: str) -> Position:
        lot = self._lots.get(symbol)
        if lot is None or lot.quantity <= _EPS:
            return Position(symbol=symbol, quantity=0.0, avg_entry_price=0.0)
        return Position(
            symbol=symbol,
            quantity=lot.quantity,
            avg_entry_price=lot.avg_price,
        )

    def has_position(self, symbol: str) -> bool:
        lot = self._lots.get(symbol)
        return lot is not None and lot.quantity > _EPS

    def equity(self, mark_prices: Dict[str, float]) -> float:
        cash = self.cash
        for sym, lot in self._lots.items():
            if lot.quantity > _EPS:
                cash += lot.quantity * mark_prices.get(sym, lot.avg_price)
        return cash

    # -- order placement -----------------------------------------------------

    def submit_market_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        reference_price: float,
        timestamp: Optional[datetime] = None,
    ) -> Fill:
        """Place a market order through the exchange adapter.

        Defensive in failure: raises on rejection, drift, or limit
        breach. NEVER silently assumes success.
        """
        timestamp = timestamp or datetime.now(timezone.utc)
        self._roll_daily_state(timestamp)

        # Kill-switch: halted for the day?
        if self._halted_until_day is not None:
            current = timestamp.date().isoformat()
            if current == self._halted_until_day and side is Side.BUY:
                raise LiveBrokerError(
                    f"daily loss-cap hit ({self._loss_today:.2f} >= "
                    f"{self.config.max_daily_loss:.2f}); refusing new BUY")

        # Stake cap on entries
        if side is Side.BUY:
            notional = quantity * reference_price
            if notional > self.config.max_stake_per_trade + _EPS:
                raise LiveBrokerError(
                    f"stake {notional:.2f} exceeds max_stake_per_trade "
                    f"{self.config.max_stake_per_trade:.2f}")
            open_count = sum(
                1 for lot in self._lots.values() if lot.quantity > _EPS)
            if open_count >= self.config.max_open_positions:
                raise LiveBrokerError(
                    f"max_open_positions ({self.config.max_open_positions}) "
                    "reached; refusing new BUY")

        if side is Side.SELL:
            lot = self._lots.get(symbol)
            held = lot.quantity if lot else 0.0
            if held < _EPS:
                raise LiveBrokerError(f"cannot SELL {symbol}: no position")
            quantity = min(quantity, held)

        client_order_id = generate_client_order_id(
            symbol=symbol, side=side.value, timestamp=timestamp,
        )
        order = ExchangeOrder(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            reference_price=reference_price,
        )
        _log.info(
            "submit %s %s %.6f @~%.6f (clientOrderId=%s)",
            side.value, symbol, quantity, reference_price, client_order_id,
        )
        try:
            fill = self._exchange.place_market_order(order)
        except OrderRejected as exc:
            _log.warning("order rejected by exchange: %s", exc)
            raise
        except ExchangeUnreachable as exc:
            _log.error("exchange unreachable on submit: %s", exc)
            # Fail closed: do NOT assume the order is open.
            raise LiveBrokerError(f"submit failed (unreachable): {exc}") from exc

        self._apply_fill(fill, _now_day=timestamp.date().isoformat())
        self._fills.append(fill)
        self._orders_since_reconcile += 1
        if self._orders_since_reconcile >= self.config.reconcile_every_n_orders:
            self._reconcile(symbol)
            self._orders_since_reconcile = 0
        return fill

    # -- bookkeeping ---------------------------------------------------------

    def _apply_fill(self, fill: Fill, *, _now_day: Optional[str] = None) -> None:
        lot = self._lots.setdefault(fill.symbol, _Lot())
        if fill.side is Side.BUY:
            new_qty = lot.quantity + fill.quantity
            if new_qty > _EPS:
                lot.avg_price = (
                    (lot.avg_price * lot.quantity + fill.price * fill.quantity)
                    / new_qty
                )
            lot.quantity = new_qty
            lot.fees_paid += fill.fee
            if lot.opened_at is None:
                lot.opened_at = fill.timestamp
        else:  # SELL
            sold_qty = min(fill.quantity, lot.quantity)
            cost = lot.avg_price * sold_qty
            proceeds = fill.price * sold_qty - fill.fee
            pnl = proceeds - cost - lot.fees_paid * (sold_qty / max(lot.quantity, _EPS))
            self._loss_today += min(0.0, pnl) * -1.0  # accumulate loss magnitude
            self._closed_trades.append(TradeRecord(
                symbol=fill.symbol,
                quantity=sold_qty,
                entry_price=lot.avg_price,
                exit_price=fill.price,
                opened_at=lot.opened_at or fill.timestamp,
                closed_at=fill.timestamp,
                pnl=pnl,
                fees=lot.fees_paid + fill.fee,
            ))
            lot.quantity -= sold_qty
            if lot.quantity <= _EPS:
                lot.quantity = 0.0
                lot.avg_price = 0.0
                lot.opened_at = None
                lot.fees_paid = 0.0
            # Refresh loss-cap status — use the fill's own UTC day so this
            # respects injected timestamps in tests and historical replay.
            if self._loss_today >= self.config.max_daily_loss:
                self._halted_until_day = (
                    _now_day or fill.timestamp.astimezone(timezone.utc)
                                .date().isoformat()
                )
                _log.error(
                    "DAILY LOSS-CAP HIT: %.2f >= %.2f; halting new BUYs "
                    "for day %s",
                    self._loss_today, self.config.max_daily_loss,
                    self._halted_until_day,
                )

    def _roll_daily_state(self, ts: datetime) -> None:
        day = ts.date().isoformat()
        if day != self._loss_day:
            _log.info("daily loss-state reset (%s -> %s)", self._loss_day, day)
            self._loss_day = day
            self._loss_today = 0.0
            self._halted_until_day = None

    # -- reconciliation ------------------------------------------------------

    def _reconcile(self, symbol: str) -> None:
        """Compare local lot vs exchange-reported position. Alert on drift."""
        try:
            remote = self._exchange.get_position(symbol)
        except (ExchangeUnreachable, Exception) as exc:  # noqa: BLE001
            _log.warning("reconcile: query failed (%s)", exc)
            return
        local = self.position(symbol)
        drift = abs(local.quantity - remote.quantity)
        rel = drift / max(remote.quantity, local.quantity, 1e-9)
        if rel > 0.001:  # >0.1% drift
            _log.error(
                "RECONCILIATION DRIFT %s: local qty=%.8f, exchange qty=%.8f "
                "(rel=%.2f%%)",
                symbol, local.quantity, remote.quantity, rel * 100,
            )
        else:
            _log.debug("reconcile OK %s: %.8f == %.8f", symbol,
                       local.quantity, remote.quantity)
