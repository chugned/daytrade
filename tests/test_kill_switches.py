"""Kill-switch verification — prove each risk circuit-breaker actually fires.

These are the mandatory pre-live tests: every check that prevents catastrophic
behaviour must be exercised with hard assertions, not just shipped and hoped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daytrade.config.schema import RiskConfig
from daytrade.risk.engine import RiskEngine


def _engine(**overrides) -> RiskEngine:
    cfg = RiskConfig(**overrides)
    return RiskEngine(cfg, starting_equity=1000.0)


# ---------------------------------------------------------------------------
# Daily loss limit
# ---------------------------------------------------------------------------

def test_daily_loss_limit_blocks_new_entries_once_breached():
    risk = _engine(max_daily_loss_pct=0.05)         # 5% daily loss limit
    day_start = datetime(2026, 5, 31, 8, 0, tzinfo=timezone.utc)
    risk.observe_equity(day_start, 1000.0)

    # Lose 6% — over the cap.
    risk.observe_equity(day_start + timedelta(hours=2), 940.0)
    permission = risk.evaluate_entry(940.0, open_positions=0, bar_index=10)
    assert permission.allowed is False
    assert "daily loss limit" in permission.reason.lower()


def test_daily_loss_limit_resets_at_the_day_rollover():
    risk = _engine(max_daily_loss_pct=0.05)
    d1 = datetime(2026, 5, 31, 8, 0, tzinfo=timezone.utc)
    risk.observe_equity(d1, 1000.0)
    risk.observe_equity(d1 + timedelta(hours=2), 940.0)
    assert risk.evaluate_entry(940.0, 0, 10).allowed is False
    # New day begins — baseline resets.
    risk.observe_equity(d1 + timedelta(days=1), 940.0)
    assert risk.evaluate_entry(940.0, 0, 100).allowed is True


# ---------------------------------------------------------------------------
# Weekly loss limit
# ---------------------------------------------------------------------------

def test_weekly_loss_limit_blocks_when_breached():
    risk = _engine(max_weekly_loss_pct=0.12)
    monday = datetime(2026, 5, 25, 8, 0, tzinfo=timezone.utc)
    risk.observe_equity(monday, 1000.0)
    # Lose 14% across the week.
    risk.observe_equity(monday + timedelta(days=4), 860.0)
    permission = risk.evaluate_entry(860.0, open_positions=0, bar_index=500)
    assert permission.allowed is False
    assert "weekly loss limit" in permission.reason.lower()


# ---------------------------------------------------------------------------
# Max open positions
# ---------------------------------------------------------------------------

def test_max_open_positions_blocks_new_entry():
    risk = _engine(max_open_positions=3)
    risk.observe_equity(datetime.now(timezone.utc), 1000.0)
    permission = risk.evaluate_entry(1000.0, open_positions=3, bar_index=10)
    assert permission.allowed is False
    assert "max open positions" in permission.reason.lower()


def test_max_open_positions_allows_below_cap():
    risk = _engine(max_open_positions=3)
    risk.observe_equity(datetime.now(timezone.utc), 1000.0)
    assert risk.evaluate_entry(1000.0, open_positions=2, bar_index=10).allowed is True


# ---------------------------------------------------------------------------
# Post-loss cooldown
# ---------------------------------------------------------------------------

def test_post_loss_cooldown_blocks_new_entries_for_N_bars():
    risk = _engine(loss_cooldown_bars=20)
    risk.observe_equity(datetime.now(timezone.utc), 1000.0)
    risk.register_trade_close(pnl=-5.0, bar_index=100)
    # Within the cooldown window — blocked.
    permission = risk.evaluate_entry(1000.0, open_positions=0, bar_index=110)
    assert permission.allowed is False
    assert "cooldown" in permission.reason.lower()
    # After the cooldown expires — allowed again.
    permission = risk.evaluate_entry(1000.0, open_positions=0, bar_index=121)
    assert permission.allowed is True


def test_winning_trade_does_not_start_a_cooldown():
    risk = _engine(loss_cooldown_bars=20)
    risk.observe_equity(datetime.now(timezone.utc), 1000.0)
    risk.register_trade_close(pnl=+5.0, bar_index=100)
    assert risk.evaluate_entry(1000.0, 0, 105).allowed is True


# ---------------------------------------------------------------------------
# Position sizing (per-coin notional cap)
# ---------------------------------------------------------------------------

def test_position_size_respects_notional_cap():
    risk = _engine(risk_per_trade=0.05, max_position_pct=0.25)
    sizing = risk.size(equity=1000.0, entry=100.0, stop=99.0)
    # Notional must not exceed 25% of 1,000 = 250 EUR.
    assert sizing.notional <= 250.0 + 1e-6
    assert sizing.is_tradeable


def test_position_size_zero_when_entry_equals_stop():
    risk = _engine()
    sizing = risk.size(equity=1000.0, entry=100.0, stop=100.0)
    assert sizing.quantity == 0.0
    assert not sizing.is_tradeable


# ---------------------------------------------------------------------------
# Composite — multiple blocks at once
# ---------------------------------------------------------------------------

def test_multiple_blocks_are_all_reported():
    """When several limits fail simultaneously, evaluate_entry exposes them all."""
    risk = _engine(max_daily_loss_pct=0.05, max_open_positions=3,
                   loss_cooldown_bars=20)
    day_start = datetime(2026, 5, 31, 8, 0, tzinfo=timezone.utc)
    risk.observe_equity(day_start, 1000.0)
    risk.observe_equity(day_start + timedelta(hours=1), 900.0)   # -10%
    risk.register_trade_close(pnl=-10.0, bar_index=50)
    permission = risk.evaluate_entry(900.0, open_positions=3, bar_index=55)
    assert permission.allowed is False
    assert len(permission.blocks) >= 2
