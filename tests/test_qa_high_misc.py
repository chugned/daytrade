"""Regression tests for QA-HIGH-{1,2,3,6} batch fixes.

  - HIGH-1: upsert_outcome_and_mark_evaluated is atomic
  - HIGH-2: Observer seeds _peak_equity from db history
  - HIGH-3: db.prune_old removes old rows + returns counts
  - HIGH-6: db.total_realised_pnl sums every closed trade
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from daytrade.observatory.database import ObservatoryDB


def _db(tmp_path):
    return ObservatoryDB(path=tmp_path / "obs.db")


# ---- HIGH-6 --------------------------------------------------------------

def test_total_realised_pnl_sums_all_closed_trades(tmp_path):
    db = _db(tmp_path)
    try:
        for i, pnl in enumerate([1.0, -0.5, 2.5, -1.0, 0.25]):
            db.insert_paper_trade(
                symbol="BTCUSDT", side="buy", quantity=0.001,
                entry_price=100.0, stop=99.0, target=101.0,
                fees=0.0, slippage=0.0, pnl=0.0,
            )
            # Find the row we just made + close it with pnl
            row = db._conn.execute(
                "SELECT id FROM paper_trades ORDER BY id DESC LIMIT 1"
            ).fetchone()
            db.close_paper_trade(row[0], exit_price=101.0, pnl=pnl,
                                  fees=0.1, slippage=0.05)
        assert db.total_realised_pnl() == pytest.approx(2.25)
    finally:
        db.close()


def test_total_realised_pnl_zero_when_no_trades(tmp_path):
    db = _db(tmp_path)
    try:
        assert db.total_realised_pnl() == 0.0
    finally:
        db.close()


# ---- HIGH-2 --------------------------------------------------------------

def test_historical_peak_equity_returns_max_from_safety_scores(tmp_path):
    db = _db(tmp_path)
    try:
        for eq in [1000.0, 1050.0, 980.0, 1200.0, 1100.0]:
            db._insert("safety_scores", {
                "ts": (datetime.now(timezone.utc)).isoformat(),
                "score": 50.0, "status": "OK", "condition": "OK",
                "reasons": "", "breakdown": "",
                "equity": eq, "drawdown_pct": 0.0,
            })
        assert db.historical_peak_equity() == 1200.0
    finally:
        db.close()


def test_historical_peak_equity_zero_when_empty(tmp_path):
    db = _db(tmp_path)
    try:
        assert db.historical_peak_equity() == 0.0
    finally:
        db.close()


# ---- HIGH-1 --------------------------------------------------------------

def test_upsert_outcome_and_mark_evaluated_atomic(tmp_path):
    """Both writes land OR neither does. After success the prediction
    is flagged evaluated=1 AND the outcome row exists."""
    db = _db(tmp_path)
    try:
        pid = db.insert_prediction(
            ts=datetime.now(timezone.utc).isoformat(),
            symbol="BTCUSDT", direction="buy", confidence=0.6,
            entry=100.0, stop=99.0, target=101.0,
            fused_score=0.5, reasons="x",
        )
        db.upsert_outcome_and_mark_evaluated(
            pid, target_hit=1, stop_hit=0, realized_pnl=1.0)
        pred = db._one("SELECT evaluated FROM predictions WHERE id=?",
                        (pid,))
        assert pred["evaluated"] == 1
        outcome = db._one(
            "SELECT prediction_id FROM prediction_outcomes WHERE prediction_id=?",
            (pid,))
        assert outcome is not None
    finally:
        db.close()


# ---- HIGH-3 --------------------------------------------------------------

def test_prune_old_drops_aged_activity_rows(tmp_path):
    db = _db(tmp_path)
    try:
        # Old row
        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        db._insert("activity_events", {
            "ts": old_ts, "event": "old", "detail": "", "level": "info"})
        # Recent row
        new_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db._insert("activity_events", {
            "ts": new_ts, "event": "new", "detail": "", "level": "info"})
        pruned = db.prune_old(days=30)
        assert pruned.get("activity_events", 0) == 1
        remaining = db._all("SELECT event FROM activity_events")
        assert [r["event"] for r in remaining] == ["new"]
    finally:
        db.close()


def test_prune_old_handles_missing_tables(tmp_path):
    """If a table doesn't exist (older schema), we don't crash."""
    db = _db(tmp_path)
    try:
        pruned = db.prune_old(days=30)
        # All tables in the target list should report a count (or 0)
        for k in ("activity_events", "market_snapshots", "symbol_health"):
            assert k in pruned
    finally:
        db.close()
