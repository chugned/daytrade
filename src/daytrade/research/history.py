"""Historical market-data ingestion with a local cache.

Downloads real Binance historical klines (public, read-only — no API key, no
auth, no orders) and caches them in a local SQLite file. The cache is what
collapses the research feedback loop: the first run of a given
``(symbol, interval, range)`` fetches over the network; every run after that
is served from disk in milliseconds.

This is read-only market data. Nothing here can place a trade.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..exchanges.base import ExchangeError
from ..models import OHLCV
from ..runtime import get_logger

_log = get_logger("research.history")

_BASE_URL = "https://data-api.binance.vision"
_REPO_ROOT = Path(__file__).resolve().parents[3]
HISTORY_DB_PATH = _REPO_ROOT / "data" / "market_history.db"

# Supported kline intervals -> milliseconds per bar.
INTERVAL_MS: Dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


class HistoryStore:
    """SQLite-backed cache of downloaded klines."""

    def __init__(self, path: Path | str = HISTORY_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS klines ("
            "symbol TEXT, interval TEXT, open_time INTEGER, "
            "open REAL, high REAL, low REAL, close REAL, volume REAL, "
            "PRIMARY KEY (symbol, interval, open_time))"
        )
        self._conn.commit()

    def cached_span(self, symbol: str, interval: str) -> tuple[int, int, int]:
        """Return ``(min_open_time, max_open_time, count)`` cached, or zeros."""
        row = self._conn.execute(
            "SELECT MIN(open_time), MAX(open_time), COUNT(*) FROM klines "
            "WHERE symbol=? AND interval=?",
            (symbol, interval),
        ).fetchone()
        if not row or row[0] is None:
            return (0, 0, 0)
        return (int(row[0]), int(row[1]), int(row[2]))

    def read(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> List[OHLCV]:
        rows = self._conn.execute(
            "SELECT open_time, open, high, low, close, volume FROM klines "
            "WHERE symbol=? AND interval=? AND open_time>=? AND open_time<=? "
            "ORDER BY open_time",
            (symbol, interval, start_ms, end_ms),
        ).fetchall()
        return [
            OHLCV(
                symbol=symbol,
                timestamp=int(r[0]),
                open=r[1],
                high=r[2],
                low=r[3],
                close=r[4],
                volume=r[5],
            )
            for r in rows
        ]

    def write(self, symbol: str, interval: str, klines: List[list]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO klines "
            "(symbol, interval, open_time, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    symbol,
                    interval,
                    int(k[0]),
                    float(k[1]),
                    float(k[2]),
                    float(k[3]),
                    float(k[4]),
                    float(k[5]),
                )
                for k in klines
            ],
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


@retry(
    retry=retry_if_exception_type((httpx.HTTPError,)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
def _fetch_page(
    symbol: str, interval: str, start_ms: int, end_ms: int, timeout: float
) -> List[list]:
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(
            _BASE_URL + "/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
        )
        resp.raise_for_status()
        return resp.json()


def _download_range(
    symbol: str, interval: str, start_ms: int, end_ms: int, timeout: float = 15.0
) -> List[list]:
    """Page through Binance klines to cover ``[start_ms, end_ms]``."""
    step = INTERVAL_MS[interval]
    out: List[list] = []
    cursor = start_ms
    pages = 0
    while cursor < end_ms:
        try:
            rows = _fetch_page(symbol, interval, cursor, end_ms, timeout)
        except httpx.HTTPError as exc:
            raise ExchangeError(f"history download failed for {symbol} {interval}: {exc}") from exc
        if not rows:
            break
        out.extend(rows)
        cursor = int(rows[-1][0]) + step
        pages += 1
        if len(rows) < 1000:
            break
        time.sleep(0.12)  # be polite to the public endpoint
    _log.info("downloaded %d %s %s klines in %d page(s)", len(out), symbol, interval, pages)
    return out


def download_history(
    symbol: str,
    interval: str = "1h",
    days: int = 365,
    store: HistoryStore | None = None,
) -> List[OHLCV]:
    """Return ``days`` of real ``interval`` candles for ``symbol``.

    Served from the local cache when already present; otherwise downloaded
    from Binance's public klines endpoint and cached for next time.
    """
    if interval not in INTERVAL_MS:
        raise ValueError(
            f"unsupported interval {interval!r}; " f"choose from {sorted(INTERVAL_MS)}"
        )
    owns_store = store is None
    store = store or HistoryStore()
    try:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        step = INTERVAL_MS[interval]
        expected = max(1, (now_ms - start_ms) // step)

        cached = store.read(symbol, interval, start_ms, now_ms)
        # Serve from cache when it already covers ~the whole requested span.
        if len(cached) >= expected * 0.95:
            _log.info("history cache hit: %s %s (%d bars)", symbol, interval, len(cached))
            return cached

        rows = _download_range(symbol, interval, start_ms, now_ms)
        if not rows:
            raise ExchangeError(f"no history returned for {symbol} {interval}")
        store.write(symbol, interval, rows)
        return store.read(symbol, interval, start_ms, now_ms)
    finally:
        if owns_store:
            store.close()
