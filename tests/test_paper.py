"""Paper broker tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daytrade.config import load_config
from daytrade.models import Side
from daytrade.paper import PaperBroker

_CFG = load_config(load_dotenv_file=False)
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _broker(cash=100_000.0):
    return PaperBroker(starting_cash=cash)


def test_broker_starts_flat():
    broker = _broker()
    assert broker.cash == 100_000.0
    assert not broker.has_position("BTCUSDT")
    assert broker.realized_pnl == 0.0


def test_buy_opens_position_and_spends_cash():
    broker = _broker()
    broker.submit_market_order(
        "o1",
        "BTCUSDT",
        Side.BUY,
        1.0,
        reference_price=30_000.0,
        available_liquidity=100.0,
        risk_config=_CFG.risk,
        timestamp=_T0,
    )
    assert broker.has_position("BTCUSDT")
    assert broker.cash < 100_000.0


def test_sell_without_position_raises():
    broker = _broker()
    with pytest.raises(ValueError):
        broker.submit_market_order("o1", "BTCUSDT", Side.SELL, 1.0, 30_000.0, 100.0, _CFG.risk, _T0)


def test_round_trip_records_trade():
    broker = _broker()
    broker.submit_market_order("buy", "BTCUSDT", Side.BUY, 1.0, 30_000.0, 1000.0, _CFG.risk, _T0)
    broker.submit_market_order(
        "sell", "BTCUSDT", Side.SELL, 1.0, 31_000.0, 1000.0, _CFG.risk, _T0 + timedelta(hours=1)
    )
    assert len(broker.closed_trades) == 1
    assert not broker.has_position("BTCUSDT")


def test_profitable_round_trip_has_positive_pnl():
    broker = _broker()
    broker.submit_market_order("buy", "BTCUSDT", Side.BUY, 1.0, 30_000.0, 10_000.0, _CFG.risk, _T0)
    broker.submit_market_order(
        "sell", "BTCUSDT", Side.SELL, 1.0, 33_000.0, 10_000.0, _CFG.risk, _T0 + timedelta(hours=1)
    )
    trade = broker.closed_trades[0]
    assert trade.pnl > 0 and trade.is_win


def test_sell_quantity_clamped_to_holdings():
    broker = _broker()
    broker.submit_market_order("buy", "BTCUSDT", Side.BUY, 1.0, 30_000.0, 1000.0, _CFG.risk, _T0)
    held = broker.position("BTCUSDT").quantity
    fill = broker.submit_market_order(
        "sell", "BTCUSDT", Side.SELL, 999.0, 30_000.0, 1000.0, _CFG.risk, _T0 + timedelta(hours=1)
    )
    assert fill.quantity <= held + 1e-9


def test_equity_reflects_marked_position():
    broker = _broker()
    broker.submit_market_order("buy", "BTCUSDT", Side.BUY, 1.0, 30_000.0, 10_000.0, _CFG.risk, _T0)
    eq_up = broker.equity({"BTCUSDT": 35_000.0})
    eq_down = broker.equity({"BTCUSDT": 25_000.0})
    assert eq_up > eq_down


def test_snapshot_is_consistent():
    broker = _broker()
    broker.submit_market_order("buy", "BTCUSDT", Side.BUY, 1.0, 30_000.0, 10_000.0, _CFG.risk, _T0)
    snap = broker.snapshot(_T0, {"BTCUSDT": 30_000.0})
    assert "BTCUSDT" in snap.open_symbols
    assert snap.equity == pytest.approx(broker.equity({"BTCUSDT": 30_000.0}))


def test_fills_are_logged():
    broker = _broker()
    broker.submit_market_order("o1", "BTCUSDT", Side.BUY, 1.0, 30_000.0, 1000.0, _CFG.risk, _T0)
    assert len(broker.fills) == 1
