"""Pin the contract of ``Observer._warm_feed_parallel``.

This is the speed fix that moves the per-cycle feed I/O from
sequential (N symbols × 3 endpoints round-trips) to parallel via
a hard-capped ThreadPoolExecutor. The CLAUDE.md anti-pattern doc
allows this when (a) workers are capped at ≤4 and (b) the cap is
pinned by a test.

These tests pin both."""

from __future__ import annotations

import time
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from daytrade.config import load_config
from daytrade.config.schema import WatchlistConfig
from daytrade.observatory import LiveMockFeed, ObservatoryDB, Observer


def _make_observer(tmp_path, symbols):
    return Observer(
        load_config(load_dotenv_file=False),
        WatchlistConfig(symbols=list(symbols)),
        db=ObservatoryDB(tmp_path / "obs.db"),
        feed=LiveMockFeed(),
    )


def test_worker_cap_pinned_at_four():
    """CLAUDE.md anti-pattern rule: parallel symbol-universe fetches
    must be ≤4 workers. The bot has historically tripped this when
    capped at higher values."""
    assert Observer._FEED_WARM_WORKERS <= 4, (
        "FEED_WARM_WORKERS must be ≤4 per the thread-budget anti-pattern "
        "(see CLAUDE.md 'Anti-patterns — DO NOT reintroduce')"
    )


def test_warm_calls_each_endpoint_per_symbol(tmp_path):
    """For N symbols, the warmer must call candles_at + orderbook_at +
    tick_at for each — that's what the cache warming relies on."""
    obs = _make_observer(tmp_path, ["AAPL", "MSFT", "GOOG"])
    candles_calls, ob_calls, tick_calls = [], [], []
    real_candles, real_ob, real_tick = (
        obs.feed.candles_at, obs.feed.orderbook_at, obs.feed.tick_at
    )

    def w_candles(sym, *a, **kw):
        candles_calls.append(sym); return real_candles(sym, *a, **kw)

    def w_ob(sym, *a, **kw):
        ob_calls.append(sym); return real_ob(sym, *a, **kw)

    def w_tick(sym, *a, **kw):
        tick_calls.append(sym); return real_tick(sym, *a, **kw)

    obs.feed.candles_at = w_candles
    obs.feed.orderbook_at = w_ob
    obs.feed.tick_at = w_tick

    obs._warm_feed_parallel(["AAPL", "MSFT", "GOOG"],
                             datetime(2026, 6, 2, tzinfo=timezone.utc))

    assert set(candles_calls) == {"AAPL", "MSFT", "GOOG"}
    assert set(ob_calls) == {"AAPL", "MSFT", "GOOG"}
    assert set(tick_calls) == {"AAPL", "MSFT", "GOOG"}


def test_warm_skips_when_single_symbol(tmp_path):
    """One symbol → no parallelism overhead. The check at the top of
    _warm_feed_parallel returns early."""
    obs = _make_observer(tmp_path, ["AAPL"])
    calls = []
    obs.feed.candles_at = lambda *a, **kw: (calls.append(a[0]) or
                                              LiveMockFeed().candles_at(*a, **kw))
    obs._warm_feed_parallel(["AAPL"], datetime(2026, 6, 2, tzinfo=timezone.utc))
    # With only 1 symbol there's no point fanning out — the helper exits early.
    assert calls == []


def test_warm_swallows_per_symbol_errors(tmp_path):
    """One bad symbol must not poison the rest. The real error
    handling happens inside _observe_symbol; warming is best-effort."""
    obs = _make_observer(tmp_path, ["GOOD", "BAD"])

    def crashy(sym, *a, **kw):
        if sym == "BAD":
            raise RuntimeError("boom")
        return []

    obs.feed.candles_at = crashy
    obs.feed.orderbook_at = lambda *a, **kw: None
    obs.feed.tick_at = lambda *a, **kw: None
    # Must not raise
    obs._warm_feed_parallel(["GOOD", "BAD"],
                             datetime(2026, 6, 2, tzinfo=timezone.utc))


def test_warm_actually_parallel_not_serial(tmp_path):
    """Sanity: 4 symbols × 100ms each should complete in ~100-150ms
    (parallel), not ~400ms (serial). Measures the speedup is real."""
    obs = _make_observer(tmp_path, ["A", "B", "C", "D"])
    barrier = threading.Barrier(4)

    def slow(sym, *a, **kw):
        # Coordinate so all 4 calls start at roughly the same time —
        # proves they run on the pool, not sequentially.
        barrier.wait(timeout=1.0)
        time.sleep(0.05)
        return []

    obs.feed.candles_at = slow
    obs.feed.orderbook_at = lambda *a, **kw: None
    obs.feed.tick_at = lambda *a, **kw: None

    t0 = time.monotonic()
    obs._warm_feed_parallel(list("ABCD"),
                             datetime(2026, 6, 2, tzinfo=timezone.utc))
    elapsed = time.monotonic() - t0

    # Serial would be ~0.20s (4 × 0.05s); parallel ~0.05-0.10s.
    # Generous bound to avoid CI flake.
    assert elapsed < 0.15, (
        f"warm took {elapsed:.3f}s — not parallel (serial baseline ~0.20s)"
    )
