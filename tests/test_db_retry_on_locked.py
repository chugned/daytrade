"""Tests for retry-with-backoff on SQLite 'database is locked' errors.

Bug history (P4-2): on 2026-06-02 at 03:50 the observer logged
``sqlite3.OperationalError: database is locked`` for one symbol (SW).
That one symbol was skipped, the cycle continued — low impact, but
the bot SHOULD just retry briefly instead of dropping the write. WAL
mode + ``timeout=10s`` on connect already absorb most contention, but
edge cases like a concurrent ``wal_checkpoint(TRUNCATE)`` or a
long-held read snapshot can still bubble the error to Python.

This file exercises the contract of the ``_RetryingConnection``
proxy. The proxy:

- Wraps ``sqlite3.Connection.execute`` and ``commit``.
- On ``OperationalError`` containing 'locked' or 'busy', retries with
  exponential backoff up to ``max_retries`` times.
- Re-raises other ``OperationalError`` immediately (no false retry on
  schema/SQL errors).
- Passes everything else through unchanged.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from daytrade.observatory.database import _RetryingConnection


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeConn:
    """Sqlite-Connection look-alike that raises 'database is locked' N times
    before succeeding, used to exercise the retry path without flakiness."""

    def __init__(self, fail_first_n: int, error_message: str = "database is locked"):
        self.fail_first_n = fail_first_n
        self.error_message = error_message
        self.execute_calls = 0
        self.commit_calls = 0

    def execute(self, sql, params=()):
        self.execute_calls += 1
        if self.execute_calls <= self.fail_first_n:
            raise sqlite3.OperationalError(self.error_message)
        return ("ok", sql, params)

    def commit(self):
        self.commit_calls += 1
        if self.commit_calls <= self.fail_first_n:
            raise sqlite3.OperationalError(self.error_message)
        return None

    # Anything else the proxy might pass through — never expected to be
    # called in these tests, fail loud if it is.
    def __getattr__(self, name):
        raise AttributeError(f"_FakeConn has no attribute {name}")


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

def test_retries_execute_until_success():
    fake = _FakeConn(fail_first_n=2)
    wrapped = _RetryingConnection(fake, max_retries=5, base_delay=0.001)
    result = wrapped.execute("INSERT INTO x VALUES (?)", (1,))
    assert result == ("ok", "INSERT INTO x VALUES (?)", (1,))
    assert fake.execute_calls == 3  # 2 failures + 1 success


def test_retries_commit_until_success():
    fake = _FakeConn(fail_first_n=1)
    wrapped = _RetryingConnection(fake, max_retries=5, base_delay=0.001)
    wrapped.commit()
    assert fake.commit_calls == 2  # 1 failure + 1 success


def test_raises_after_max_retries_exhausted():
    fake = _FakeConn(fail_first_n=100)
    wrapped = _RetryingConnection(fake, max_retries=3, base_delay=0.001)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        wrapped.execute("INSERT INTO x VALUES (?)", (1,))
    assert fake.execute_calls == 4  # 1 initial + 3 retries


def test_does_not_retry_unrelated_operational_errors():
    """Syntax errors, missing-table errors etc. surface as OperationalError
    too — retrying them would mask real bugs. Must re-raise immediately."""
    fake = _FakeConn(fail_first_n=10, error_message="no such table: predictions")
    wrapped = _RetryingConnection(fake, max_retries=5, base_delay=0.001)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        wrapped.execute("SELECT * FROM predictions")
    assert fake.execute_calls == 1  # no retry


def test_busy_error_also_triggers_retry():
    """SQLite raises 'database is busy' (SQLITE_BUSY) in some contention
    scenarios — the proxy treats it like 'locked' since the remedy
    (back off and retry) is the same."""
    fake = _FakeConn(fail_first_n=1, error_message="database is busy")
    wrapped = _RetryingConnection(fake, max_retries=3, base_delay=0.001)
    wrapped.execute("INSERT INTO x VALUES (?)", (1,))
    assert fake.execute_calls == 2


def test_backoff_grows_between_retries():
    """Sleep between retries should be non-zero and increasing — the proxy
    measures elapsed wall-clock during a multi-retry sequence to confirm
    backoff is actually waiting (not busy-spinning)."""
    fake = _FakeConn(fail_first_n=3)
    wrapped = _RetryingConnection(fake, max_retries=5, base_delay=0.01)
    started = time.monotonic()
    wrapped.execute("INSERT INTO x VALUES (?)", (1,))
    elapsed = time.monotonic() - started
    # base 0.01s * (1 + 2 + 4) = 0.07s minimum for 3 retries exponential.
    # Allow slack — the assertion is that backoff is NOT zero (immediate
    # retries), which would burn CPU during a real contention spike.
    assert elapsed >= 0.03, f"backoff too short ({elapsed:.4f}s)"


def test_passthrough_for_non_wrapped_attributes():
    """Reads via fetchone/fetchall happen on the cursor returned by
    execute(), not on the connection — and other attributes like
    row_factory should pass through unchanged."""
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    wrapped = _RetryingConnection(raw)
    # row_factory should be readable through the proxy
    assert wrapped.row_factory is sqlite3.Row


# ---------------------------------------------------------------------------
# End-to-end against a real ObservatoryDB
# ---------------------------------------------------------------------------

def test_observatorydb_writes_succeed_under_contention(tmp_path):
    """Real-DB smoke: open two connections to the same file, hold a
    write lock briefly on one, attempt a write on the other. With
    retry-on-locked active, the second write should succeed.

    Without the retry proxy the second write would surface
    ``sqlite3.OperationalError: database is locked`` to the caller
    (after the connect-level busy_timeout expires, which is short in
    test scenarios)."""
    from daytrade.observatory.database import ObservatoryDB

    db_path = tmp_path / "observatory.db"
    db = ObservatoryDB(path=db_path)
    # Open a competing raw connection and start an exclusive transaction.
    # check_same_thread=False lets the side thread run COMMIT on this
    # connection without sqlite's "objects created in a thread" check
    # tripping; this matches how the production observer/dashboard
    # connections are constructed.
    other = sqlite3.connect(str(db_path), timeout=0.1, check_same_thread=False)
    other.isolation_level = None
    other.execute("BEGIN EXCLUSIVE")

    # Release the lock after a brief delay from a side thread
    def _release_soon():
        time.sleep(0.15)
        other.execute("COMMIT")

    t = threading.Thread(target=_release_soon, daemon=True)
    t.start()

    # This write would normally fail with 'database is locked' before
    # the side thread releases; with the retry proxy it should keep
    # retrying past the 150ms release and succeed.
    db.insert_symbol_health(
        symbol="TESTUSDT",
        healthy=1,
        rejections=[],
    )
    t.join(timeout=2.0)
    assert not t.is_alive()
