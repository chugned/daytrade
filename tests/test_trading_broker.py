"""Tests for the TradingBroker abstraction layer.

Two things to prove:

  1. `DBPaperBroker` produces the exact same DB writes the Observer
     used to produce before the refactor (bit-for-bit equivalent).
  2. `LiveBrokerAdapter` routes the right calls through a LiveBroker
     and persists actual fill prices/fees (not requested prices).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from daytrade.live import LiveBroker, LiveConfig, MockExchange
from daytrade.models import Side
from daytrade.observatory.trading_broker import (
    DBPaperBroker,
    LiveBrokerAdapter,
)

UTC = timezone.utc


def _ts() -> datetime:
    return datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# DBPaperBroker — preserves current observer behaviour
# ---------------------------------------------------------------------------


def test_dbpaperbroker_open_calls_insert_paper_trade():
    db = MagicMock()
    db.insert_paper_trade.return_value = 42
    broker = DBPaperBroker(db, fee_bps=10.0)
    opened = broker.open_long(
        "BTCUSDT",
        quantity=0.001,
        entry_price=100_000.0,
        stop=99_500.0,
        target=101_000.0,
        timestamp=_ts(),
    )
    assert opened.trade_id == 42
    assert opened.fill_price == 100_000.0
    assert opened.fill_quantity == 0.001
    db.insert_paper_trade.assert_called_once()
    kwargs = db.insert_paper_trade.call_args.kwargs
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["side"] == Side.BUY.value
    assert kwargs["quantity"] == 0.001
    assert kwargs["entry_price"] == 100_000.0
    assert kwargs["stop"] == 99_500.0
    assert kwargs["target"] == 101_000.0
    # fees/slippage/pnl are zero on open (matches pre-refactor code)
    assert kwargs["fees"] == 0.0
    assert kwargs["slippage"] == 0.0
    assert kwargs["pnl"] == 0.0


def test_dbpaperbroker_close_matches_pre_refactor_math():
    """The exact arithmetic the Observer ran before this refactor:
    gross    = (exit - entry) * qty
    fee      = (exit + entry) * qty * fee_bps / 10_000
    pnl      = gross - fee
    slippage = exit * 0.0004 * qty
    """
    db = MagicMock()
    broker = DBPaperBroker(db, fee_bps=10.0, slippage_rate=0.0004)
    closed = broker.close_long(
        trade_id=42,
        symbol="BTCUSDT",
        quantity=0.001,
        entry_price=100_000.0,
        exit_price=101_000.0,
        timestamp=_ts(),
    )
    expected_gross = (101_000.0 - 100_000.0) * 0.001  # 1.0
    expected_fee = (101_000.0 + 100_000.0) * 0.001 * 10.0 / 10_000.0  # 0.201
    expected_pnl = expected_gross - expected_fee  # 0.799
    expected_slippage = 101_000.0 * 0.0004 * 0.001  # 0.0404
    assert closed.pnl == pytest.approx(expected_pnl)
    assert closed.fees == pytest.approx(expected_fee)
    assert closed.slippage == pytest.approx(expected_slippage)
    assert closed.fill_price == 101_000.0
    db.close_paper_trade.assert_called_once_with(
        42,
        exit_price=101_000.0,
        pnl=pytest.approx(expected_pnl),
        fees=pytest.approx(expected_fee),
        slippage=pytest.approx(expected_slippage),
    )


def test_dbpaperbroker_close_negative_pnl_when_stopped_out():
    db = MagicMock()
    broker = DBPaperBroker(db, fee_bps=10.0)
    closed = broker.close_long(
        trade_id=1,
        symbol="BTCUSDT",
        quantity=0.001,
        entry_price=100_000.0,
        exit_price=99_500.0,
        timestamp=_ts(),
    )
    assert closed.pnl < 0


# ---------------------------------------------------------------------------
# LiveBrokerAdapter — routes through LiveBroker
# ---------------------------------------------------------------------------


def _live_broker() -> tuple[LiveBroker, MockExchange]:
    ex = MockExchange(starting_balance_usdt=1000.0)
    cfg = LiveConfig(dry_run=True, max_stake_per_trade=250.0, max_daily_loss=200.0)
    return LiveBroker(cfg, ex), ex


def test_live_adapter_open_routes_through_live_broker_and_persists():
    live, _ = _live_broker()
    db = MagicMock()
    db.insert_paper_trade.return_value = 99
    adapter = LiveBrokerAdapter(live, db)
    opened = adapter.open_long(
        "BTCUSDT",
        quantity=0.001,
        entry_price=100_000.0,
        stop=99_500.0,
        target=101_000.0,
        timestamp=_ts(),
    )
    assert opened.trade_id == 99
    # MockExchange applies 2 bps BUY slippage upward → fill_price > entry
    assert opened.fill_price > 100_000.0
    assert opened.fill_quantity == 0.001
    # DB row has the ACTUAL fill price + actual fee, not the requested
    db.insert_paper_trade.assert_called_once()
    kwargs = db.insert_paper_trade.call_args.kwargs
    assert kwargs["entry_price"] == opened.fill_price
    assert kwargs["fees"] > 0
    assert kwargs["slippage"] > 0


def test_live_adapter_close_uses_real_fill_price():
    live, _ = _live_broker()
    db = MagicMock()
    db.insert_paper_trade.return_value = 1
    adapter = LiveBrokerAdapter(live, db)
    opened = adapter.open_long(
        "BTCUSDT",
        quantity=0.001,
        entry_price=100_000.0,
        stop=99_500.0,
        target=101_000.0,
        timestamp=_ts(),
    )
    closed = adapter.close_long(
        trade_id=opened.trade_id,
        symbol="BTCUSDT",
        quantity=0.001,
        entry_price=opened.fill_price,
        exit_price=101_000.0,
        timestamp=_ts() + timedelta(minutes=5),
    )
    # SELL slippage is downward in MockExchange
    assert closed.fill_price < 101_000.0
    # DB close was called with the real fill price, not the requested
    db.close_paper_trade.assert_called_once()
    args, kwargs = db.close_paper_trade.call_args
    assert args[0] == 1
    assert kwargs["exit_price"] == closed.fill_price


def test_live_adapter_propagates_broker_errors():
    """If the LiveBroker raises (e.g. stake cap, exchange down), the
    error must propagate so the Observer can refuse the trade rather
    than recording a fake DB row."""
    db = MagicMock()
    failing_live = MagicMock()
    failing_live.submit_market_order.side_effect = RuntimeError("stake cap")
    adapter = LiveBrokerAdapter(failing_live, db)
    with pytest.raises(RuntimeError, match="stake cap"):
        adapter.open_long(
            "BTCUSDT",
            quantity=0.001,
            entry_price=100_000.0,
            stop=99_500.0,
            target=101_000.0,
            timestamp=_ts(),
        )
    # Crucially: no DB write on a failed open
    db.insert_paper_trade.assert_not_called()


# ---------------------------------------------------------------------------
# Observer integration — default broker preserves behaviour
# ---------------------------------------------------------------------------


def test_observer_default_broker_is_dbpaperbroker():
    """An Observer constructed without an explicit broker should fall
    back to DBPaperBroker — preserving paper-mode behaviour."""
    from unittest.mock import patch

    # Patch heavy-deps so we can construct an Observer without a real DB
    # or feed but still verify the broker assignment.
    with patch("daytrade.observatory.observer.ObservatoryDB"):
        from daytrade.config.schema import AppConfig, WatchlistConfig
        from daytrade.observatory.observer import Observer

        cfg = AppConfig()
        obs = Observer(cfg, WatchlistConfig())
        from daytrade.observatory.trading_broker import DBPaperBroker

        assert isinstance(obs._broker, DBPaperBroker)


def test_observer_accepts_custom_broker():
    """Passing broker= should override the default."""
    from unittest.mock import patch

    with patch("daytrade.observatory.observer.ObservatoryDB"):
        from daytrade.config.schema import AppConfig, WatchlistConfig
        from daytrade.observatory.observer import Observer

        mock_broker = MagicMock()
        obs = Observer(AppConfig(), WatchlistConfig(), broker=mock_broker)
        assert obs._broker is mock_broker
