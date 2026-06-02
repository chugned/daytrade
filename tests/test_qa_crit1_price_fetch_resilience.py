"""QA-CRIT-1 regression — a feed.price_at failure must NOT crash the cycle.

Before this fix, observer._equity() and observer._manage_positions()
called feed.price_at() with no try/except. Any Binance hiccup
(ExchangeError, timeout) would raise out, the outer cycle handler
caught it, and the whole cycle failed. The rest of the open positions
were silently un-managed (stop-losses skipped).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from daytrade.observatory.observer import Observer
from daytrade.config.schema import AppConfig, WatchlistConfig


class _FlakyFeed:
    """A feed whose price_at fails for a target symbol but works for others."""

    def __init__(self, prices: dict, fail_symbol: str | None = None,
                 exc=RuntimeError("simulated Binance 503")):
        self._prices = prices
        self._fail = fail_symbol
        self._exc = exc
        self.calls = []

    def price_at(self, symbol, when):
        self.calls.append(symbol)
        if symbol == self._fail:
            raise self._exc
        return self._prices.get(symbol, 100.0)


def _obs(broker=None):
    cfg = AppConfig()
    # Patch ObservatoryDB so we don't write to disk
    from unittest.mock import patch
    with patch("daytrade.observatory.observer.ObservatoryDB"):
        return Observer(cfg, WatchlistConfig(), broker=broker)


def test_equity_handles_price_at_failure_for_one_position():
    """If one symbol's price fetch fails, equity calc must still
    return a valid number using the entry price as fallback."""
    obs = _obs()
    obs._open = {
        "BTCUSDT": {"entry": 100_000.0, "qty": 0.001, "stop": 99_000.0,
                    "target": 101_000.0, "opened_cycle": 1, "trade_id": 1},
        "ETHUSDT": {"entry": 3500.0, "qty": 0.01, "stop": 3450.0,
                    "target": 3550.0, "opened_cycle": 1, "trade_id": 2},
    }
    obs.feed = _FlakyFeed(
        prices={"ETHUSDT": 3550.0},
        fail_symbol="BTCUSDT",
    )
    obs.db = MagicMock()
    obs.db.closed_paper_trades.return_value = []
    obs.db.total_realised_pnl.return_value = 0.0  # QA-HIGH-6 new method
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    # Must not raise
    eq = obs._equity(now)
    # ETH gain (50 * 0.01 = 0.5) realised through this calc
    # BTC fallback to entry → contributes 0 to unrealised
    assert eq == pytest.approx(1000.0 + 0.5)


def test_manage_positions_skips_failed_symbol_but_processes_others():
    """A failing price fetch on symbol A must not prevent symbol B
    from being managed (the silent-stop-loss bug)."""
    obs = _obs(broker=MagicMock())
    obs._broker.close_long.return_value = MagicMock(pnl=10.0, fill_price=99_000.0,
                                                     fees=0.5, slippage=0.1)
    obs._open = {
        "BTCUSDT": {"entry": 100_000.0, "qty": 0.001, "stop": 99_000.0,
                    "target": 101_000.0, "opened_cycle": 1, "trade_id": 1},
        "ETHUSDT": {"entry": 3500.0, "qty": 0.01, "stop": 3450.0,
                    "target": 3550.0, "opened_cycle": 1, "trade_id": 2},
    }
    # BTC's price_at fails; ETH's hits the target → should close ETH
    obs.feed = _FlakyFeed(prices={"ETHUSDT": 3550.0},
                          fail_symbol="BTCUSDT")
    obs.db = MagicMock()
    obs._risk = MagicMock()
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)

    closed = obs._manage_positions(now)
    assert closed == 1  # ETH closed despite BTC's failure
    # broker.close_long called for ETH only
    assert obs._broker.close_long.call_count == 1
    sym_closed = obs._broker.close_long.call_args.kwargs.get("symbol")
    assert sym_closed == "ETHUSDT"
    # BTC still open
    assert "BTCUSDT" in obs._open
    assert "ETHUSDT" not in obs._open


def test_manage_positions_all_symbols_failing_returns_zero():
    """When every symbol's price fetch fails, the cycle must still
    return cleanly (zero closes) rather than propagating the exception."""
    obs = _obs(broker=MagicMock())
    obs._open = {
        "BTCUSDT": {"entry": 100_000.0, "qty": 0.001, "stop": 99_000.0,
                    "target": 101_000.0, "opened_cycle": 1, "trade_id": 1},
    }
    obs.feed = _FlakyFeed(prices={}, fail_symbol="BTCUSDT")
    obs.db = MagicMock()
    obs._risk = MagicMock()
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    closed = obs._manage_positions(now)
    assert closed == 0
    # Position still open — we didn't lose track
    assert "BTCUSDT" in obs._open
