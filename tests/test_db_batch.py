"""Pin the contract of ``ObservatoryDB.batch()`` on daytrade.

Symmetric with nighttrade tests/test_db_batch.py — same context manager,
same semantics."""

from __future__ import annotations

import sqlite3
import time

import pytest

from daytrade.observatory.database import ObservatoryDB


def test_batch_commits_at_exit(tmp_path):
    db = ObservatoryDB(path=tmp_path / "obs.db")
    with db.batch():
        for i in range(50):
            db.insert_snapshot(symbol=f"S{i:03d}", price=100.0 + i)
    rows = db._all("SELECT COUNT(*) AS c FROM market_snapshots")
    assert rows[0]["c"] == 50


def test_batch_is_nestable(tmp_path):
    db = ObservatoryDB(path=tmp_path / "obs.db")
    with db.batch():
        db.insert_snapshot(symbol="OUT", price=100.0)
        with db.batch():
            db.insert_snapshot(symbol="IN", price=101.0)
        assert db._batch_depth == 1
    rows = db._all("SELECT symbol FROM market_snapshots ORDER BY id")
    assert [r["symbol"] for r in rows] == ["OUT", "IN"]


def test_batch_rolls_back_on_exception(tmp_path):
    db = ObservatoryDB(path=tmp_path / "obs.db")
    with pytest.raises(RuntimeError):
        with db.batch():
            db.insert_snapshot(symbol="A", price=1.0)
            raise RuntimeError("boom")
    rows = db._all("SELECT COUNT(*) AS c FROM market_snapshots")
    assert rows[0]["c"] == 0


def test_batch_faster_than_individual_commits(tmp_path):
    n = 200
    db1 = ObservatoryDB(path=tmp_path / "individual.db")
    t = time.monotonic()
    for i in range(n):
        db1.insert_snapshot(symbol=f"S{i:04d}", price=100.0)
    individual = time.monotonic() - t

    db2 = ObservatoryDB(path=tmp_path / "batched.db")
    t = time.monotonic()
    with db2.batch():
        for i in range(n):
            db2.insert_snapshot(symbol=f"S{i:04d}", price=100.0)
    batched = time.monotonic() - t

    # Daytrade's _insert also writes a per-row line to db-writes.log
    # (file I/O), which db.batch doesn't optimise away. So the win on
    # daytrade is smaller than on nighttrade (where _writelog is absent).
    # 1.3x is the empirical floor on this host; below that would mean
    # batching is not helping at all.
    assert batched < individual * 0.75, (
        f"batched={batched*1000:.0f}ms expected < individual×0.75={individual*750:.0f}ms"
    )
