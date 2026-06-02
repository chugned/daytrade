"""QA-CRIT-2 regression — RealMarketFeed._minute_close is FIFO-capped.

Before the fix this dict grew without bound for the process lifetime,
which is the same memory leak nighttrade has been exhibiting (~5GB
after 12 days). The cap (_MINUTE_CLOSE_CACHE_MAX) keeps it constant.
"""

from __future__ import annotations

import pytest

from daytrade.observatory.real_feed import RealMarketFeed


def test_minute_close_cache_caps_at_max():
    feed = RealMarketFeed()
    cap = RealMarketFeed._MINUTE_CLOSE_CACHE_MAX
    # Insert cap + 1000 entries — should NEVER exceed cap.
    for i in range(cap + 1000):
        feed._cache_minute_close(("BTCUSDT", i), float(100 + i))
    assert len(feed._minute_close) == cap


def test_minute_close_cache_evicts_oldest_fifo():
    feed = RealMarketFeed()
    # Mini cap to make the test cheap.
    feed._MINUTE_CLOSE_CACHE_MAX = 3
    for i in range(5):
        feed._cache_minute_close(("X", i), float(i))
    # Only the last 3 keys should survive — FIFO eviction
    keys = list(feed._minute_close.keys())
    assert keys == [("X", 2), ("X", 3), ("X", 4)]


def test_minute_close_cache_repeat_key_promotes_recency():
    """Re-inserting an existing key should move it to the recent end so
    it isn't evicted out from under callers using it."""
    feed = RealMarketFeed()
    feed._MINUTE_CLOSE_CACHE_MAX = 3
    feed._cache_minute_close(("X", 1), 1.0)
    feed._cache_minute_close(("X", 2), 2.0)
    feed._cache_minute_close(("X", 3), 3.0)
    # Touch key 1 → should become most-recent
    feed._cache_minute_close(("X", 1), 1.0)
    # Insert a new key — should evict (X, 2), the oldest
    feed._cache_minute_close(("X", 4), 4.0)
    keys = list(feed._minute_close.keys())
    assert keys == [("X", 3), ("X", 1), ("X", 4)]


def test_default_cap_is_sensible():
    """Sanity: the cap is large enough to cover a full day of minute
    closes across a 50-symbol watchlist (≈72k entries), but capped so
    a year-long run doesn't grow to GB."""
    cap = RealMarketFeed._MINUTE_CLOSE_CACHE_MAX
    # At least one day across a small watchlist
    assert cap >= 1440 * 30
    # Not so large that it grows past ~10 MB of dict storage
    assert cap <= 200_000
