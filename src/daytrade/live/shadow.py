"""ShadowExchange — read real Binance state, route writes to MockExchange.

The final smoke test before real money. The bot:

  - Queries the actual Binance balance / position state of your account
    (so reconciliation, balance checks, and stake-cap logic all see
    real numbers).
  - When it decides to place an order, the order goes to a
    :class:`MockExchange` instead — no money moves, no orders hit the
    real exchange, but the bot books the trade as if it had filled.

This catches every edge case that "MockExchange + paper data" cannot:
balance-precision rounding, lot-size filter behaviour, real fee
deductions, real timestamp jitter, real network latency. After 7 days
of shadow trading you have hard evidence that the live wiring works
end-to-end before flipping the last gate.

Construction is intentionally awkward: you have to pass BOTH a real
read-only adapter AND a mock writer. Anyone reading the code can tell
which is which.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Fill, Position
from .exchange import (
    Exchange,
    ExchangeOrder,
    MockExchange,
    OrderState,
)

_log = logging.getLogger("live.shadow")


class ShadowExchange:
    """Reads from a real :class:`Exchange`, writes to a :class:`MockExchange`.

    Implements the :class:`Exchange` Protocol so it slots into the
    LiveBroker unchanged.
    """

    def __init__(
        self,
        *,
        reader: Exchange,
        writer: MockExchange,
    ) -> None:
        """
        Parameters
        ----------
        reader : Exchange
            The real exchange (typically :class:`BinanceExchange` in its
            default ``writes_enabled=False`` state). Used for
            ``get_balance``, ``get_position``, ``list_open_orders``.
        writer : MockExchange
            The fake executor that books simulated fills. Used for
            ``place_market_order``, ``cancel_order``.
        """
        self._reader = reader
        self._writer = writer
        # Reconcile the writer's starting cash with the reader's real
        # balance so the bot's accounting matches the actual account size.
        try:
            real_balance = float(reader.get_balance("USDT"))
            self._writer.inject_balance("USDT", real_balance)
            _log.info(
                "ShadowExchange initialised: real USDT balance = %.2f " "synced into MockExchange",
                real_balance,
            )
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            _log.warning(
                "ShadowExchange could not sync starting balance (%s); "
                "MockExchange retains its default",
                exc,
            )

    # -- reads come from the real exchange ----------------------------------

    def get_balance(self, asset: str) -> float:
        return self._reader.get_balance(asset)

    def get_position(self, symbol: str) -> Position:
        # Important nuance: the *real* exchange knows the spot balance,
        # but does NOT track our paper-style average entry price. The
        # reader returns avg_entry_price=0; that's fine — the LiveBroker
        # uses this for reconciliation against ITS OWN book, where the
        # broker keeps the real avg_price.
        return self._reader.get_position(symbol)

    def list_open_orders(self, symbol: Optional[str] = None) -> List[OrderState]:
        # The Binance side will report no real open orders (we never
        # placed any). The MockExchange may have its own. Merge.
        try:
            real_orders = self._reader.list_open_orders(symbol)
        except Exception as exc:  # noqa: BLE001
            _log.info(
                "ShadowExchange: reader open-orders query failed " "(%s); using mock only", exc
            )
            real_orders = []
        mock_orders = self._writer.list_open_orders(symbol)
        return list(real_orders) + list(mock_orders)

    # -- writes are routed to the mock --------------------------------------

    def place_market_order(self, order: ExchangeOrder) -> Fill:
        _log.info(
            "SHADOW place_market_order %s %s %.6f @~%.6f "
            "(clientOrderId=%s) — routed to MockExchange",
            order.side.value,
            order.symbol,
            order.quantity,
            order.reference_price,
            order.client_order_id,
        )
        return self._writer.place_market_order(order)

    def cancel_order(self, symbol: str, client_order_id: str) -> None:
        _log.info(
            "SHADOW cancel_order %s %s — routed to MockExchange",
            symbol,
            client_order_id,
        )
        self._writer.cancel_order(symbol, client_order_id)
