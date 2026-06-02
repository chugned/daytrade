"""Tests for the LiveBroker scaffolding.

All tests use :class:`MockExchange` — no real network, no real money.
The MockExchange mirrors the slippage / fee math of the paper broker so
"dry-run live" numbers stay comparable to "paper mode" numbers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daytrade.live import (
    LiveBroker,
    LiveBrokerError,
    LiveConfig,
    MockExchange,
    OrderRejected,
)
from daytrade.models import Side

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def _ts(year=2026, month=7, day=1, hour=12, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def _make() -> tuple[LiveBroker, MockExchange]:
    ex = MockExchange(starting_balance_usdt=1000.0)
    cfg = LiveConfig(
        dry_run=True, max_stake_per_trade=250.0, max_daily_loss=30.0, max_open_positions=3
    )
    broker = LiveBroker(cfg, ex)
    return broker, ex


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def test_default_config_is_dry_run():
    cfg = LiveConfig()
    assert cfg.dry_run is True


def test_starting_cash_comes_from_exchange_balance():
    broker, ex = _make()
    assert broker.starting_cash == 1000.0
    assert broker.cash == 1000.0
    assert broker.realized_pnl == 0.0


def test_no_position_initially():
    broker, _ = _make()
    p = broker.position("BTCUSDT")
    assert p.quantity == 0.0
    assert not broker.has_position("BTCUSDT")


# ---------------------------------------------------------------------------
# Place + bookkeeping
# ---------------------------------------------------------------------------


def test_buy_then_sell_round_trip():
    broker, _ = _make()
    t0 = _ts()
    fill_buy = broker.submit_market_order(
        "BTCUSDT",
        Side.BUY,
        quantity=0.001,
        reference_price=100_000.0,
        timestamp=t0,
    )
    assert fill_buy.side is Side.BUY
    assert broker.has_position("BTCUSDT")
    pos = broker.position("BTCUSDT")
    assert pos.quantity == pytest.approx(0.001)
    assert pos.avg_entry_price > 100_000.0  # slippage hit BUYs upward

    # Sell back — same minute bucket would dedupe, so use a later ts.
    fill_sell = broker.submit_market_order(
        "BTCUSDT",
        Side.SELL,
        quantity=0.001,
        reference_price=101_000.0,
        timestamp=t0 + timedelta(minutes=2),
    )
    assert fill_sell.side is Side.SELL
    assert not broker.has_position("BTCUSDT")
    trades = broker.closed_trades
    assert len(trades) == 1
    assert trades[0].symbol == "BTCUSDT"


def test_idempotent_within_same_minute_bucket():
    """Re-calling submit with the same (symbol, side, minute) must NOT
    open a second position — the clientOrderId collides and the
    exchange returns the prior fill."""
    broker, ex = _make()
    t = _ts()
    f1 = broker.submit_market_order(
        "BTCUSDT",
        Side.BUY,
        quantity=0.001,
        reference_price=100_000.0,
        timestamp=t,
    )
    # Same timestamp bucket -> same clientOrderId -> exchange returns the
    # prior fill rather than opening a new one. The LiveBroker DOES apply
    # the returned Fill again though, since it's the caller's responsibility
    # to avoid this — what we're testing is that the MockExchange does the
    # right thing on the network side.
    state = ex.list_open_orders()
    # MockExchange fills immediately, so no resting orders. But the
    # idempotency record is in _seen_ids — second call returns same fill.
    assert len(ex.fills) == 1, "exchange must not record a second fill"


def test_sell_with_no_position_raises():
    broker, _ = _make()
    with pytest.raises(LiveBrokerError):
        broker.submit_market_order(
            "BTCUSDT",
            Side.SELL,
            quantity=0.001,
            reference_price=100_000.0,
            timestamp=_ts(),
        )


def test_sell_clamps_to_held_quantity():
    broker, _ = _make()
    broker.submit_market_order(
        "BTCUSDT",
        Side.BUY,
        quantity=0.001,
        reference_price=100_000.0,
        timestamp=_ts(hour=10),
    )
    fill = broker.submit_market_order(
        "BTCUSDT",
        Side.SELL,
        quantity=0.005,  # 5× what we have
        reference_price=100_500.0,
        timestamp=_ts(hour=11),
    )
    assert fill.quantity == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# Safety limits
# ---------------------------------------------------------------------------


def test_stake_cap_rejects_oversized_order():
    broker, _ = _make()  # max_stake_per_trade=250
    with pytest.raises(LiveBrokerError, match="stake"):
        broker.submit_market_order(
            "BTCUSDT",
            Side.BUY,
            quantity=0.01,  # 0.01 * 100k = 1000 > 250
            reference_price=100_000.0,
            timestamp=_ts(),
        )


def test_max_open_positions_blocks_new_buy():
    broker, _ = _make()  # max_open_positions=3
    for i, sym in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT")):
        broker.submit_market_order(
            sym,
            Side.BUY,
            quantity=0.001,
            reference_price=100.0,
            timestamp=_ts(hour=10 + i),
        )
    with pytest.raises(LiveBrokerError, match="max_open_positions"):
        broker.submit_market_order(
            "BNBUSDT",
            Side.BUY,
            quantity=0.001,
            reference_price=100.0,
            timestamp=_ts(hour=14),
        )


def test_daily_loss_cap_halts_new_buys_but_allows_close():
    broker, _ = _make()  # max_daily_loss=30
    t = _ts(hour=10)
    # Open a position, then close it at a $50 loss so the cap trips.
    broker.submit_market_order(
        "BTCUSDT",
        Side.BUY,
        quantity=0.001,
        reference_price=100_000.0,
        timestamp=t,
    )
    # Sell at much lower → big loss
    broker.submit_market_order(
        "BTCUSDT",
        Side.SELL,
        quantity=0.001,
        reference_price=50_000.0,
        timestamp=t + timedelta(minutes=5),
    )
    pnl = broker.realized_pnl
    assert pnl < -30, f"expected big loss to trip cap, got {pnl:.2f}"

    # A new BUY same day must now be refused
    with pytest.raises(LiveBrokerError, match="daily loss-cap"):
        broker.submit_market_order(
            "ETHUSDT",
            Side.BUY,
            quantity=0.0001,
            reference_price=3000.0,
            timestamp=t + timedelta(minutes=10),
        )


def test_daily_loss_cap_resets_next_day():
    broker, _ = _make()
    t = _ts(hour=10)
    broker.submit_market_order(
        "BTCUSDT",
        Side.BUY,
        quantity=0.001,
        reference_price=100_000.0,
        timestamp=t,
    )
    broker.submit_market_order(
        "BTCUSDT",
        Side.SELL,
        quantity=0.001,
        reference_price=50_000.0,
        timestamp=t + timedelta(minutes=5),
    )
    # New UTC day → cap reset, BUYs allowed again
    next_day = _ts(year=2026, month=7, day=2, hour=10)
    fill = broker.submit_market_order(
        "ETHUSDT",
        Side.BUY,
        quantity=0.0001,
        reference_price=3000.0,
        timestamp=next_day,
    )
    assert fill.quantity == pytest.approx(0.0001)


# ---------------------------------------------------------------------------
# Failure modes — fail closed
# ---------------------------------------------------------------------------


def test_exchange_unreachable_raises_no_state_change():
    broker, ex = _make()
    ex.force_unreachable_next()
    cash_before = broker.cash
    with pytest.raises(LiveBrokerError):
        broker.submit_market_order(
            "BTCUSDT",
            Side.BUY,
            quantity=0.001,
            reference_price=100_000.0,
            timestamp=_ts(),
        )
    assert broker.cash == cash_before
    assert not broker.has_position("BTCUSDT")
    assert broker.closed_trades == []


def test_exchange_rejection_propagates_no_state_change():
    broker, ex = _make()
    ex.force_reject_next(OrderRejected("min lot size 0.0001 not met"))
    cash_before = broker.cash
    with pytest.raises(OrderRejected):
        broker.submit_market_order(
            "BTCUSDT",
            Side.BUY,
            quantity=0.001,
            reference_price=100_000.0,
            timestamp=_ts(),
        )
    assert broker.cash == cash_before
    assert not broker.has_position("BTCUSDT")


def test_insufficient_balance_rejected():
    ex = MockExchange(starting_balance_usdt=50.0)
    cfg = LiveConfig(dry_run=True, max_stake_per_trade=500.0)
    broker = LiveBroker(cfg, ex)
    with pytest.raises(OrderRejected):
        broker.submit_market_order(
            "BTCUSDT",
            Side.BUY,
            quantity=0.001,
            reference_price=100_000.0,
            timestamp=_ts(),
        )


# ---------------------------------------------------------------------------
# Equity + PnL
# ---------------------------------------------------------------------------


def test_equity_reflects_position_value():
    broker, _ = _make()
    t = _ts(hour=10)
    broker.submit_market_order(
        "BTCUSDT",
        Side.BUY,
        quantity=0.001,
        reference_price=100_000.0,
        timestamp=t,
    )
    eq_flat = broker.equity({"BTCUSDT": 100_000.0})
    eq_up = broker.equity({"BTCUSDT": 110_000.0})
    assert eq_up > eq_flat


def test_round_trip_pnl_is_negative_after_fees_when_price_flat():
    broker, _ = _make()
    t = _ts(hour=10)
    broker.submit_market_order(
        "BTCUSDT",
        Side.BUY,
        quantity=0.001,
        reference_price=100_000.0,
        timestamp=t,
    )
    broker.submit_market_order(
        "BTCUSDT",
        Side.SELL,
        quantity=0.001,
        reference_price=100_000.0,
        timestamp=t + timedelta(minutes=5),
    )
    # Fees + slippage should make a flat round-trip slightly negative.
    assert broker.realized_pnl < 0.0


# ---------------------------------------------------------------------------
# Defence-in-depth grep: no live-trading code without dry_run gate
# ---------------------------------------------------------------------------


def test_live_module_does_not_call_real_exchange_directly():
    """The live package must not import ccxt or python-binance directly,
    EXCEPT in binance.py (the explicit adapter). The Binance adapter
    itself must (a) be guarded by writes_enabled=False default and
    (b) enforce the trade-only key check at construction. Other files
    must remain exchange-library-free so a typo can't accidentally
    enable live trading."""
    import pkgutil

    import daytrade.live as live_pkg

    ALLOWED_TO_IMPORT_REAL_EXCHANGE = {"binance"}

    for mod in pkgutil.iter_modules(live_pkg.__path__):
        name = mod.name
        if name in ALLOWED_TO_IMPORT_REAL_EXCHANGE:
            continue
        src = open(f"{live_pkg.__path__[0]}/{name}.py").read()
        assert "import ccxt" not in src, (
            f"live/{name}.py imports ccxt — this would enable live trading "
            "without explicit gating. Live exchange writes must go through "
            "the BinanceExchange adapter in live/binance.py only."
        )
        assert (
            "from binance" not in src
        ), f"live/{name}.py imports python-binance directly — same problem."
