"""Risk engine tests: sizing, slippage, partial fills, loss limits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daytrade.models import Side
from daytrade.risk import RiskEngine, position_size, simulate_fill


def test_position_size_respects_risk_budget(config):
    """Loss from entry to stop should equal risk_per_trade * equity."""
    cfg = config.risk
    result = position_size(10_000.0, entry=100.0, stop=98.0, config=cfg)
    # Without the notional cap, risk_amount == equity * risk_per_trade.
    expected = 10_000.0 * cfg.risk_per_trade
    # The notional cap may bind; risk must never EXCEED the budget.
    assert result.risk_amount <= expected + 1e-6


def test_position_size_notional_cap(config):
    """A very tight stop is capped by max_position_pct."""
    result = position_size(10_000.0, entry=100.0, stop=99.99, config=config.risk)
    assert result.capped_by_notional
    assert result.notional <= 10_000.0 * config.risk.max_position_pct + 1e-6


def test_position_size_zero_when_entry_equals_stop(config):
    result = position_size(10_000.0, entry=100.0, stop=100.0, config=config.risk)
    assert result.quantity == 0.0
    assert not result.is_tradeable


def test_slippage_worsens_buy_fill(config):
    """A BUY must fill at or ABOVE the reference price."""
    fill = simulate_fill(
        "o",
        "BTC",
        Side.BUY,
        1.0,
        reference_price=100.0,
        available_liquidity=100.0,
        config=config.risk,
    )
    assert fill.price > 100.0
    assert fill.slippage > 0


def test_slippage_worsens_sell_fill(config):
    """A SELL must fill at or BELOW the reference price."""
    fill = simulate_fill(
        "o",
        "BTC",
        Side.SELL,
        1.0,
        reference_price=100.0,
        available_liquidity=100.0,
        config=config.risk,
    )
    assert fill.price < 100.0
    assert fill.slippage > 0


def test_larger_order_has_more_impact(config):
    """Market impact: a bigger order slips more than a small one."""
    small = simulate_fill("a", "BTC", Side.BUY, 1.0, 100.0, 1000.0, config.risk)
    large = simulate_fill("b", "BTC", Side.BUY, 200.0, 100.0, 1000.0, config.risk)
    assert large.slippage > small.slippage


def test_partial_fill_caps_at_liquidity(config):
    """An oversized order fills only the liquidity-capped fraction."""
    fill = simulate_fill(
        "o",
        "BTC",
        Side.BUY,
        1_000.0,
        reference_price=100.0,
        available_liquidity=10.0,
        config=config.risk,
    )
    assert fill.is_partial
    assert fill.quantity == pytest.approx(10.0 * config.risk.partial_fill_liquidity_frac)


def test_fee_charged_on_fill(config):
    fill = simulate_fill("o", "BTC", Side.BUY, 1.0, 100.0, 100.0, config.risk)
    assert fill.fee > 0


def test_fill_rejected_without_liquidity(config):
    with pytest.raises(ValueError):
        simulate_fill("o", "BTC", Side.BUY, 1.0, 100.0, available_liquidity=0.0, config=config.risk)


def test_daily_loss_limit_blocks_trading(config):
    engine = RiskEngine(config.risk, starting_equity=10_000.0)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    engine.observe_equity(t0, 10_000.0)
    engine.observe_equity(t0 + timedelta(hours=1), 9_000.0)  # -10%
    perm = engine.permission(9_000.0)
    assert not perm.allowed
    assert "daily loss" in perm.reason


def test_daily_loss_resets_next_day(config):
    engine = RiskEngine(config.risk, starting_equity=10_000.0)
    day1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    day2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    engine.observe_equity(day1, 10_000.0)
    engine.observe_equity(day1, 9_000.0)
    engine.observe_equity(day2, 9_000.0)  # new day -> baseline resets
    assert engine.permission(9_000.0).allowed


def test_weekly_loss_limit_blocks_trading(config):
    engine = RiskEngine(config.risk, starting_equity=10_000.0)
    t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)  # a Monday
    engine.observe_equity(t0, 10_000.0)
    # Down 13% within the same ISO week -> over the 12% weekly limit.
    engine.observe_equity(t0 + timedelta(days=2), 8_700.0)
    perm = engine.evaluate_entry(8_700.0, open_positions=0, bar_index=10)
    assert not perm.allowed
    assert any("weekly" in b for b in perm.blocks)


def test_max_open_positions_blocks_entry(config):
    engine = RiskEngine(config.risk, starting_equity=10_000.0)
    engine.observe_equity(datetime(2026, 1, 1, tzinfo=timezone.utc), 10_000.0)
    at_cap = config.risk.max_open_positions
    perm = engine.evaluate_entry(10_000.0, open_positions=at_cap, bar_index=10)
    assert not perm.allowed
    assert any("open positions" in b for b in perm.blocks)


def test_cooldown_blocks_entry_after_loss(config):
    engine = RiskEngine(config.risk, starting_equity=10_000.0)
    engine.observe_equity(datetime(2026, 1, 1, tzinfo=timezone.utc), 10_000.0)
    engine.register_trade_close(pnl=-50.0, bar_index=100)
    # Still inside the cooldown window.
    blocked = engine.evaluate_entry(10_000.0, open_positions=0, bar_index=105)
    assert not blocked.allowed
    assert any("cooldown" in b for b in blocked.blocks)


def test_cooldown_expires(config):
    engine = RiskEngine(config.risk, starting_equity=10_000.0)
    engine.observe_equity(datetime(2026, 1, 1, tzinfo=timezone.utc), 10_000.0)
    engine.register_trade_close(pnl=-50.0, bar_index=100)
    after = 100 + config.risk.loss_cooldown_bars + 1
    assert engine.evaluate_entry(10_000.0, 0, bar_index=after).allowed


def test_no_cooldown_after_winning_trade(config):
    engine = RiskEngine(config.risk, starting_equity=10_000.0)
    engine.observe_equity(datetime(2026, 1, 1, tzinfo=timezone.utc), 10_000.0)
    engine.register_trade_close(pnl=+50.0, bar_index=100)  # a win
    assert engine.evaluate_entry(10_000.0, 0, bar_index=101).allowed
