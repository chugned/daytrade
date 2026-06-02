"""TradingBroker — the minimum surface the Observer needs from a broker.

The current Observer writes trades straight to SQLite via
``db.insert_paper_trade`` and ``db.close_paper_trade``. To support live
trading without copy-pasting the engine, we extract the two operations
behind a Protocol and supply two implementations:

  - :class:`DBPaperBroker`: thin adapter that wraps the *current*
    direct-DB code. Bit-for-bit equivalent to today's observer.
    Default for ``Observer(broker=None)``.
  - :class:`LiveBrokerAdapter`: routes through
    :class:`daytrade.live.LiveBroker` to place real orders on a real
    exchange, then persists the resulting fills to the same SQLite
    schema so the dashboard / metrics keep working unchanged.

The Protocol is small on purpose: two methods, ``open_long`` and
``close_long``. Anything more would be premature — the Observer doesn't
care about the broker's internal state, only about getting trade
records persisted with the right numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class OpenedPosition:
    """Outcome of a successful open. ``fill_price`` and ``fill_quantity``
    may differ from the requested values in live mode (slippage / lot
    size rounding); they reflect what *actually* happened."""

    trade_id: int
    fill_price: float
    fill_quantity: float
    fees: float = 0.0
    slippage: float = 0.0


@dataclass(frozen=True)
class ClosedPosition:
    """Outcome of a successful close."""

    pnl: float
    fees: float
    slippage: float
    fill_price: float


class TradingBroker(Protocol):
    """Two methods, that's it."""

    def open_long(
        self,
        symbol: str,
        quantity: float,
        entry_price: float,
        stop: float,
        target: float,
        timestamp: datetime,
    ) -> OpenedPosition: ...

    def close_long(
        self,
        trade_id: int,
        symbol: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        timestamp: datetime,
    ) -> ClosedPosition: ...


class DBPaperBroker:
    """Paper broker that writes directly to the observatory DB.

    Reproduces the exact accounting the Observer used before this
    refactor:

      gross    = (exit - entry) * qty
      fee      = (exit + entry) * qty * fee_bps / 10_000
      pnl      = gross - fee
      slippage = exit * slippage_rate * qty

    All four numbers are persisted via ``db.close_paper_trade``.
    """

    def __init__(
        self,
        db,
        *,
        fee_bps: float,
        slippage_rate: float = 0.0004,
    ) -> None:
        self._db = db
        self._fee_bps = float(fee_bps)
        self._slippage_rate = float(slippage_rate)

    def open_long(
        self,
        symbol: str,
        quantity: float,
        entry_price: float,
        stop: float,
        target: float,
        timestamp: datetime,
    ) -> OpenedPosition:
        # Side is hard-coded to BUY here; sells happen in close_long. The
        # Observer only ever calls open_long for new positions.
        from ..models import Side  # local: avoid import cycle in __init__

        trade_id = self._db.insert_paper_trade(
            symbol=symbol,
            side=Side.BUY.value,
            quantity=quantity,
            entry_price=entry_price,
            stop=stop,
            target=target,
            fees=0.0,
            slippage=0.0,
            pnl=0.0,
        )
        return OpenedPosition(
            trade_id=trade_id,
            fill_price=entry_price,
            fill_quantity=quantity,
        )

    def close_long(
        self,
        trade_id: int,
        symbol: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        timestamp: datetime,
    ) -> ClosedPosition:
        gross = (exit_price - entry_price) * quantity
        fee = (exit_price + entry_price) * quantity * self._fee_bps / 10_000.0
        pnl = gross - fee
        slippage = exit_price * self._slippage_rate * quantity
        self._db.close_paper_trade(
            trade_id,
            exit_price=exit_price,
            pnl=pnl,
            fees=fee,
            slippage=slippage,
        )
        return ClosedPosition(
            pnl=pnl,
            fees=fee,
            slippage=slippage,
            fill_price=exit_price,
        )


class LiveBrokerAdapter:
    """Routes the Observer's broker calls through a real
    :class:`daytrade.live.LiveBroker` and persists fills to the
    observatory DB.

    Numbers come from actual fills (slippage / fees), not the modelled
    ones — so the dashboard reflects what *really* happened, not what
    the engine wanted to happen.
    """

    def __init__(self, live_broker, db) -> None:
        self._live = live_broker
        self._db = db

    def open_long(
        self,
        symbol: str,
        quantity: float,
        entry_price: float,
        stop: float,
        target: float,
        timestamp: datetime,
    ) -> OpenedPosition:
        from ..models import Side

        fill = self._live.submit_market_order(
            symbol=symbol,
            side=Side.BUY,
            quantity=quantity,
            reference_price=entry_price,
            timestamp=timestamp,
        )
        trade_id = self._db.insert_paper_trade(
            symbol=symbol,
            side=Side.BUY.value,
            quantity=fill.quantity,
            entry_price=fill.price,  # ACTUAL fill price, not requested
            stop=stop,
            target=target,
            fees=fill.fee,
            slippage=fill.slippage,
            pnl=0.0,
        )
        return OpenedPosition(
            trade_id=trade_id,
            fill_price=fill.price,
            fill_quantity=fill.quantity,
            fees=fill.fee,
            slippage=fill.slippage,
        )

    def close_long(
        self,
        trade_id: int,
        symbol: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        timestamp: datetime,
    ) -> ClosedPosition:
        from ..models import Side

        fill = self._live.submit_market_order(
            symbol=symbol,
            side=Side.SELL,
            quantity=quantity,
            reference_price=exit_price,
            timestamp=timestamp,
        )
        gross = (fill.price - entry_price) * fill.quantity
        pnl = gross - fill.fee
        self._db.close_paper_trade(
            trade_id,
            exit_price=fill.price,
            pnl=pnl,
            fees=fill.fee,
            slippage=fill.slippage,
        )
        return ClosedPosition(
            pnl=pnl,
            fees=fill.fee,
            slippage=fill.slippage,
            fill_price=fill.price,
        )
