"""Exchange abstraction layer.

Everything the LiveBroker needs from an exchange goes through the
:class:`Exchange` Protocol. This lets us:

  - test the broker against an in-memory :class:`MockExchange`
  - wire a real adapter (e.g. BinanceExchange) without touching broker code
  - swap exchanges later without rewriting the broker

The Protocol surface is intentionally tiny: place an order, get a
balance, get a position, list open orders, cancel. Nothing else.
The real broker on the other side has 50+ endpoints; we expose only
what we use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol

from ..models import Fill, Position, Side


class OrderRejected(RuntimeError):
    """The exchange refused the order (insufficient balance, min-lot, etc.)."""


class ExchangeUnreachable(RuntimeError):
    """Network/HTTP failure. Caller should retry-with-backoff or fail-closed."""


@dataclass(frozen=True)
class ExchangeOrder:
    """A request to the exchange. Immutable. Carries the idempotency key."""

    client_order_id: str
    symbol: str
    side: Side
    quantity: float
    reference_price: float  # for slippage modelling in dry-run + logging


@dataclass(frozen=True)
class OrderState:
    """What the exchange reports back about an existing order."""

    client_order_id: str
    exchange_order_id: str
    symbol: str
    side: Side
    quantity_requested: float
    quantity_filled: float
    avg_fill_price: float
    status: str  # "filled" | "partial" | "open" | "cancelled" | "rejected"


class Exchange(Protocol):
    """Minimum surface the LiveBroker needs from any exchange backend."""

    def place_market_order(self, order: ExchangeOrder) -> Fill: ...

    def get_balance(self, asset: str) -> float: ...

    def get_position(self, symbol: str) -> Position: ...

    def list_open_orders(self, symbol: Optional[str] = None) -> List[OrderState]: ...

    def cancel_order(self, symbol: str, client_order_id: str) -> None: ...


# ---------------------------------------------------------------------------
# MockExchange: in-memory implementation used in tests AND in dry-run mode.
# ---------------------------------------------------------------------------


@dataclass
class _MockBook:
    """Per-symbol simulated state."""

    qty: float = 0.0
    avg_price: float = 0.0


class MockExchange:
    """An in-memory exchange.

    Used by:
      - All `test_live_broker*` tests.
      - The `LiveBroker` itself when ``LiveConfig.dry_run=True``, so the
        broker can exercise its full code path without ever touching the
        network.

    Slippage / fee model mirrors the paper broker (so dry-run numbers and
    paper-mode numbers are directly comparable). Orders are accepted
    deterministically: same inputs → same fill.
    """

    def __init__(
        self,
        *,
        starting_balance_usdt: float = 1000.0,
        taker_fee_bps: float = 10.0,
        slippage_bps: float = 2.0,
        clock=None,
    ) -> None:
        self._balances: Dict[str, float] = {"USDT": float(starting_balance_usdt)}
        self._books: Dict[str, _MockBook] = {}
        self._taker_fee = taker_fee_bps / 10_000.0
        self._slippage = slippage_bps / 10_000.0
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._seen_ids: Dict[str, OrderState] = {}
        self._fills: List[Fill] = []
        self._reject_next_with: Optional[Exception] = None
        self._unreachable_next: bool = False

    # ----- test injection -------------------------------------------------

    def inject_balance(self, asset: str, amount: float) -> None:
        self._balances[asset] = float(amount)

    def force_reject_next(self, exc: Exception) -> None:
        self._reject_next_with = exc

    def force_unreachable_next(self) -> None:
        self._unreachable_next = True

    @property
    def fills(self) -> List[Fill]:
        return list(self._fills)

    # ----- Exchange protocol ---------------------------------------------

    def place_market_order(self, order: ExchangeOrder) -> Fill:
        if self._unreachable_next:
            self._unreachable_next = False
            raise ExchangeUnreachable("mock: network down")
        if self._reject_next_with is not None:
            exc = self._reject_next_with
            self._reject_next_with = None
            raise exc

        # Idempotency: if we've seen this clientOrderId, return its prior fill.
        if order.client_order_id in self._seen_ids:
            prior = self._seen_ids[order.client_order_id]
            if prior.status == "filled":
                # Synthesise a Fill that matches the prior state — broker
                # treats this as success without applying it twice (caller
                # tracks its own state).
                return Fill(
                    order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=prior.quantity_filled,
                    price=prior.avg_fill_price,
                    requested_price=order.reference_price,
                    fee=prior.quantity_filled * prior.avg_fill_price * self._taker_fee,
                    slippage=abs(prior.avg_fill_price - order.reference_price),
                    timestamp=self._clock(),
                    is_partial=False,
                )
            raise OrderRejected(
                f"clientOrderId {order.client_order_id} already used " f"with status {prior.status}"
            )

        # Slippage: BUY hits higher, SELL hits lower (the same way the
        # paper broker models it). This is the same fill math.
        ref = order.reference_price
        if order.side is Side.BUY:
            fill_price = ref * (1 + self._slippage)
        else:
            fill_price = ref * (1 - self._slippage)
        notional = fill_price * order.quantity
        fee = notional * self._taker_fee

        if order.side is Side.BUY:
            cost = notional + fee
            if self._balances.get("USDT", 0.0) < cost - 1e-9:
                raise OrderRejected(
                    f"insufficient USDT: need {cost:.2f}, have "
                    f"{self._balances.get('USDT', 0.0):.2f}"
                )
            self._balances["USDT"] -= cost
            base = order.symbol.replace("USDT", "")
            book = self._books.setdefault(order.symbol, _MockBook())
            new_qty = book.qty + order.quantity
            book.avg_price = (
                (book.avg_price * book.qty + fill_price * order.quantity) / new_qty
                if new_qty > 0
                else 0.0
            )
            book.qty = new_qty
            self._balances[base] = self._balances.get(base, 0.0) + order.quantity
        else:  # SELL
            book = self._books.get(order.symbol)
            if book is None or book.qty < order.quantity - 1e-9:
                raise OrderRejected(
                    f"insufficient {order.symbol} qty to sell: requested "
                    f"{order.quantity}, have {book.qty if book else 0}"
                )
            self._balances["USDT"] += notional - fee
            book.qty -= order.quantity
            if book.qty < 1e-12:
                book.qty = 0.0
                book.avg_price = 0.0
            base = order.symbol.replace("USDT", "")
            self._balances[base] = max(0.0, self._balances.get(base, 0.0) - order.quantity)

        ts = self._clock()
        fill = Fill(
            order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            requested_price=ref,
            fee=fee,
            slippage=abs(fill_price - ref),
            timestamp=ts,
            is_partial=False,
        )
        self._fills.append(fill)
        self._seen_ids[order.client_order_id] = OrderState(
            client_order_id=order.client_order_id,
            exchange_order_id=f"mock-{len(self._seen_ids) + 1}",
            symbol=order.symbol,
            side=order.side,
            quantity_requested=order.quantity,
            quantity_filled=order.quantity,
            avg_fill_price=fill_price,
            status="filled",
        )
        return fill

    def get_balance(self, asset: str) -> float:
        return float(self._balances.get(asset, 0.0))

    def get_position(self, symbol: str) -> Position:
        book = self._books.get(symbol)
        if book is None or book.qty <= 0:
            return Position(symbol=symbol, quantity=0.0, avg_entry_price=0.0)
        return Position(
            symbol=symbol,
            quantity=book.qty,
            avg_entry_price=book.avg_price,
        )

    def list_open_orders(self, symbol: Optional[str] = None) -> List[OrderState]:
        # MockExchange fills market orders synchronously, so there are
        # never resting orders. Filter for completeness.
        out: List[OrderState] = []
        for st in self._seen_ids.values():
            if symbol is not None and st.symbol != symbol:
                continue
            if st.status in ("open", "partial"):
                out.append(st)
        return out

    def cancel_order(self, symbol: str, client_order_id: str) -> None:
        # No-op for the mock; market orders are immediate.
        if client_order_id in self._seen_ids:
            prior = self._seen_ids[client_order_id]
            if prior.status in ("open", "partial"):
                self._seen_ids[client_order_id] = OrderState(
                    client_order_id=prior.client_order_id,
                    exchange_order_id=prior.exchange_order_id,
                    symbol=prior.symbol,
                    side=prior.side,
                    quantity_requested=prior.quantity_requested,
                    quantity_filled=prior.quantity_filled,
                    avg_fill_price=prior.avg_fill_price,
                    status="cancelled",
                )
