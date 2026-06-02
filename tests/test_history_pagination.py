"""Tests for the multi-page behaviour of research.history.

The existing test_research.py covers the basic round-trip and the cache
hit/miss path with a fully mocked ``_download_range``. This file goes one
level deeper and exercises the ``_fetch_page``/``_download_range`` pair so
the pagination + retry + cursor-advancement logic stays correct as the
module evolves. All HTTP calls are mocked — no network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import httpx
import pytest

from daytrade.research import history as hist
from daytrade.research.history import (
    INTERVAL_MS,
    HistoryStore,
    _download_range,
    download_history,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kline(open_ms: int, price: float = 100.0, vol: float = 10.0) -> list:
    return [
        open_ms,
        str(price),
        str(price * 1.001),
        str(price * 0.999),
        str(price * 1.0005),
        str(vol),
        open_ms + 59_999,
        "0",
        1,
        "0",
        "0",
        "0",
    ]


def _patch_fetch(monkeypatch, pages: List[List[list]]):
    """Patch _fetch_page to return successive pre-built pages."""
    seq = iter(pages)
    calls: List[dict] = []

    def _stub(symbol, interval, start_ms, end_ms, timeout):
        calls.append(
            {
                "symbol": symbol,
                "interval": interval,
                "start_ms": start_ms,
                "end_ms": end_ms,
            }
        )
        try:
            return next(seq)
        except StopIteration:
            return []

    monkeypatch.setattr(hist, "_fetch_page", _stub)
    monkeypatch.setattr(hist.time, "sleep", lambda *_: None)  # speed up
    return calls


# ---------------------------------------------------------------------------
# Cursor advancement and assembly
# ---------------------------------------------------------------------------


def test_download_range_walks_through_multiple_pages(monkeypatch):
    step = INTERVAL_MS["1m"]
    start = 1_700_000_000_000
    page1 = [_kline(start + i * step) for i in range(1000)]
    page2 = [_kline(page1[-1][0] + step + i * step) for i in range(1000)]
    page3 = [_kline(page2[-1][0] + step + i * step) for i in range(200)]
    calls = _patch_fetch(monkeypatch, [page1, page2, page3])

    rows = _download_range("BTCUSDT", "1m", start_ms=start, end_ms=start + 3000 * step)
    assert len(rows) == 2200
    assert len(calls) == 3
    # Each call's start cursor should be > the previous page's last bar.
    assert calls[1]["start_ms"] > page1[-1][0]
    assert calls[2]["start_ms"] > page2[-1][0]


def test_download_range_stops_on_short_page(monkeypatch):
    """A page returning < 1000 rows means we've hit the end — don't loop."""
    step = INTERVAL_MS["1m"]
    start = 1_700_000_000_000
    short = [_kline(start + i * step) for i in range(50)]
    calls = _patch_fetch(monkeypatch, [short, [_kline(start)]])

    rows = _download_range("BTCUSDT", "1m", start_ms=start, end_ms=start + 100_000 * step)
    assert len(rows) == 50
    # We must NOT have called a second time.
    assert len(calls) == 1


def test_download_range_stops_on_empty_page(monkeypatch):
    step = INTERVAL_MS["1m"]
    start = 1_700_000_000_000
    _patch_fetch(monkeypatch, [[]])
    rows = _download_range("BTCUSDT", "1m", start_ms=start, end_ms=start + 100 * step)
    assert rows == []


def test_download_range_raises_exchange_error_on_http(monkeypatch):
    from daytrade.exchanges.base import ExchangeError

    def _explode(*a, **kw):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(hist, "_fetch_page", _explode)
    monkeypatch.setattr(hist.time, "sleep", lambda *_: None)

    with pytest.raises(ExchangeError):
        _download_range(
            "BTCUSDT", "1m", start_ms=1_700_000_000_000, end_ms=1_700_000_000_000 + 60_000 * 10
        )


# ---------------------------------------------------------------------------
# End-to-end via download_history
# ---------------------------------------------------------------------------


def test_download_history_assembles_long_range(monkeypatch, tmp_path):
    step = INTERVAL_MS["1m"]
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    days = 2
    expected_bars = (days * 86_400_000) // step  # ~2880 minutes

    def _fake_range(symbol, interval, start_ms, e_ms, timeout=15.0):
        return [_kline(start_ms + i * step) for i in range(expected_bars)]

    monkeypatch.setattr(hist, "_download_range", _fake_range)
    store = HistoryStore(tmp_path / "h.db")
    try:
        rows = download_history("BTCUSDT", interval="1m", days=days, store=store)
    finally:
        store.close()
    # Allow a small buffer for the cache-coverage heuristic.
    assert abs(len(rows) - expected_bars) <= 5


def test_download_history_cache_hits_skip_network(monkeypatch, tmp_path):
    """Second call with the same range must NOT hit the network."""
    step = INTERVAL_MS["1m"]
    now = datetime.now(timezone.utc)
    days = 1
    bars = (days * 86_400_000) // step

    calls = {"n": 0}

    def _fake_range(symbol, interval, start_ms, e_ms, timeout=15.0):
        calls["n"] += 1
        return [_kline(start_ms + i * step) for i in range(bars)]

    monkeypatch.setattr(hist, "_download_range", _fake_range)
    store = HistoryStore(tmp_path / "h.db")
    try:
        download_history("BTCUSDT", interval="1m", days=days, store=store)
        download_history("BTCUSDT", interval="1m", days=days, store=store)
    finally:
        store.close()
    assert calls["n"] == 1


def test_download_history_rejects_unsupported_interval(tmp_path):
    with pytest.raises(ValueError):
        download_history("BTCUSDT", interval="11s", days=1)


# ---------------------------------------------------------------------------
# HistoryStore semantics
# ---------------------------------------------------------------------------


def test_history_store_upsert_overwrites_same_open_time(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    try:
        store.write("BTCUSDT", "1m", [_kline(1_700_000_000_000, price=100.0)])
        store.write("BTCUSDT", "1m", [_kline(1_700_000_000_000, price=999.0)])
        rows = store.read("BTCUSDT", "1m", 1_700_000_000_000, 1_700_000_000_000 + 60_000)
        assert len(rows) == 1
        assert rows[0].open == 999.0
    finally:
        store.close()


def test_history_store_cached_span_empty(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    try:
        lo, hi, n = store.cached_span("BTCUSDT", "1m")
        assert (lo, hi, n) == (0, 0, 0)
    finally:
        store.close()


def test_history_store_reads_ordered(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    try:
        ts = [1_700_000_000_000 + i * 60_000 for i in (5, 1, 3, 2, 4)]
        store.write("BTCUSDT", "1m", [_kline(t) for t in ts])
        rows = store.read("BTCUSDT", "1m", 1_700_000_000_000, 1_700_000_000_000 + 60_000 * 10)
        out_ts = [int(r.timestamp.timestamp() * 1000) for r in rows]
        assert out_ts == sorted(ts)
    finally:
        store.close()
