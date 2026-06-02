"""Tests for ShadowExchange — real reads, mocked writes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from daytrade.live.exchange import ExchangeOrder, MockExchange
from daytrade.live.shadow import ShadowExchange
from daytrade.models import Position, Side


def _shadow(reader_balance: float = 750.0) -> tuple[ShadowExchange, MagicMock, MockExchange]:
    """Build a shadow with a mocked reader + real MockExchange writer."""
    reader = MagicMock()
    reader.get_balance.return_value = reader_balance
    reader.get_position.return_value = Position(
        symbol="BTCUSDT",
        quantity=0.0,
        avg_entry_price=0.0,
    )
    reader.list_open_orders.return_value = []
    writer = MockExchange(starting_balance_usdt=999.99)  # will be overwritten
    return ShadowExchange(reader=reader, writer=writer), reader, writer


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_syncs_starting_balance_from_reader():
    shadow, reader, writer = _shadow(reader_balance=1234.56)
    # The writer's USDT balance should now match the reader's reported value
    assert writer.get_balance("USDT") == pytest.approx(1234.56)


def test_construction_tolerates_reader_balance_failure():
    """If the real exchange balance query fails, ShadowExchange should
    still construct and fall back to the writer's default balance."""
    reader = MagicMock()
    reader.get_balance.side_effect = RuntimeError("network down")
    writer = MockExchange(starting_balance_usdt=42.0)
    shadow = ShadowExchange(reader=reader, writer=writer)
    # writer keeps its original balance — the bot can still simulate
    assert writer.get_balance("USDT") == 42.0


# ---------------------------------------------------------------------------
# Reads delegate to the real exchange
# ---------------------------------------------------------------------------


def test_get_balance_routes_to_reader():
    shadow, reader, _ = _shadow(reader_balance=500.0)
    reader.get_balance.reset_mock()
    reader.get_balance.return_value = 510.0
    assert shadow.get_balance("USDT") == 510.0
    reader.get_balance.assert_called_once_with("USDT")


def test_get_position_routes_to_reader():
    shadow, reader, _ = _shadow()
    reader.get_position.return_value = Position(
        symbol="ETHUSDT",
        quantity=0.5,
        avg_entry_price=3500.0,
    )
    pos = shadow.get_position("ETHUSDT")
    assert pos.quantity == 0.5
    reader.get_position.assert_called_with("ETHUSDT")


def test_list_open_orders_merges_reader_and_writer():
    shadow, reader, writer = _shadow()
    reader.list_open_orders.return_value = []
    out = shadow.list_open_orders("BTCUSDT")
    # No real or mock orders yet → empty
    assert out == []


def test_list_open_orders_tolerates_reader_failure():
    shadow, reader, _ = _shadow()
    reader.list_open_orders.side_effect = RuntimeError("network down")
    # Must not raise; falls back to mock-only
    out = shadow.list_open_orders()
    assert out == []


# ---------------------------------------------------------------------------
# Writes go to the MOCK only
# ---------------------------------------------------------------------------


def test_place_market_order_routes_to_writer_not_reader():
    shadow, reader, writer = _shadow(reader_balance=1000.0)
    order = ExchangeOrder(
        client_order_id="shadow-1",
        symbol="BTCUSDT",
        side=Side.BUY,
        quantity=0.001,
        reference_price=100_000.0,
    )
    fill = shadow.place_market_order(order)
    assert fill.symbol == "BTCUSDT"
    # CRUCIAL: the reader was NEVER asked to place an order
    reader.place_market_order.assert_not_called()
    # The writer (MockExchange) recorded the fill
    assert len(writer.fills) == 1


def test_cancel_order_routes_to_writer_not_reader():
    shadow, reader, writer = _shadow(reader_balance=1000.0)
    # Try cancelling a (non-existent) mock order — should be no-op, not raise
    shadow.cancel_order("BTCUSDT", "shadow-x")
    reader.cancel_order.assert_not_called()


# ---------------------------------------------------------------------------
# End-to-end with a fake "reader" exchange (in-memory)
# ---------------------------------------------------------------------------


def test_full_shadow_session_no_real_writes():
    """Build the full LiveBroker → ShadowExchange chain end-to-end and
    verify the reader exchange never has place_market_order called."""
    from daytrade.live import LiveBroker, LiveConfig

    # Use one MockExchange as the 'real' reader (just for plumbing —
    # any Exchange protocol implementation works as a stand-in).
    reader_mock = MockExchange(starting_balance_usdt=500.0)
    # Spy on its place_market_order to prove it's not called
    real_place_calls = []
    original = reader_mock.place_market_order

    def _spy(o):
        real_place_calls.append(o)
        return original(o)

    reader_mock.place_market_order = _spy  # type: ignore[method-assign]

    writer = MockExchange(starting_balance_usdt=0.0)
    shadow = ShadowExchange(reader=reader_mock, writer=writer)
    broker = LiveBroker(LiveConfig(dry_run=True, max_stake_per_trade=200.0), shadow)

    fill = broker.submit_market_order(
        "BTCUSDT",
        Side.BUY,
        quantity=0.001,
        reference_price=100_000.0,
    )
    assert fill.quantity == 0.001
    # The reader-as-real exchange MUST NOT have received any orders.
    assert len(real_place_calls) == 0
    # The writer received the order.
    assert len(writer.fills) == 1
