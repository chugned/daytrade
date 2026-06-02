"""Pin learning-session resume-across-restart (the 'Day 1/30 reset' bug).

2026-06-03: after daytrade was restarted, the dashboard showed "Day 1/30,
Warm-up, 0.1%" even though the bot had been learning for 16 days. Cause:
``resume_or_create`` only resumed a session whose status was ``"active"``,
but a CLEAN shutdown marks the session ``"stopped"``. So every clean
restart abandoned the in-progress 30-day window and started a fresh
countdown — the trade history / models were never affected, but the
day-counter kept resetting.

Fix: resume the OLDEST not-yet-completed session (by start date),
regardless of active/stopped status, and supersede any spurious newer
in-progress sessions so the counter reflects the true original window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from daytrade.observatory import LearningSession, ObservatoryDB


def _mk_session(db, start: datetime, target_days: int, status: str, cycles: int) -> int:
    """Insert a learning_sessions row directly with a chosen start_ts/status."""
    sid = db.start_learning_session(target_days, 60)
    db._conn.execute(
        "UPDATE learning_sessions SET start_ts=?, status=?, cycles_completed=? WHERE id=?",
        (start.isoformat(), status, cycles, sid),
    )
    db._conn.commit()
    return sid


def test_resume_stopped_session_in_progress(tmp_path):
    """A cleanly-STOPPED session whose 30-day window has NOT elapsed must be
    resumed (same start date + id), not abandoned for a fresh countdown."""
    db = ObservatoryDB(tmp_path / "obs.db")
    start = datetime.now(timezone.utc) - timedelta(days=16)
    sid = _mk_session(db, start, target_days=30, status="stopped", cycles=215)

    session = LearningSession.resume_or_create(db, target_days=30, interval_seconds=60)

    assert session.session_id == sid, "must resume the existing session, not create a new one"
    # Day counter reflects the ORIGINAL start (~day 17), not day 1.
    assert session.day_number(datetime.now(timezone.utc)) >= 16
    db.close()


def test_resume_picks_oldest_when_a_spurious_newer_session_exists(tmp_path):
    """If a spurious newer session was created by the old bug, resume must
    still pick the ORIGINAL (oldest) in-progress window."""
    db = ObservatoryDB(tmp_path / "obs.db")
    old_start = datetime.now(timezone.utc) - timedelta(days=16)
    new_start = datetime.now(timezone.utc) - timedelta(hours=3)
    old_id = _mk_session(db, old_start, 30, "stopped", 215)
    new_id = _mk_session(db, new_start, 30, "active", 32)

    session = LearningSession.resume_or_create(db, target_days=30, interval_seconds=60)

    assert session.session_id == old_id, "must resume the original May-18 window"
    assert session.session_id != new_id
    assert session.day_number(datetime.now(timezone.utc)) >= 16
    # The spurious newer session must be superseded so it stops showing up.
    row = db._one("SELECT status FROM learning_sessions WHERE id=?", (new_id,))
    assert row["status"] == "superseded"
    db.close()


def test_completed_window_starts_fresh(tmp_path):
    """A session whose full window HAS elapsed should NOT be resumed — start
    a genuinely new 30-day window."""
    db = ObservatoryDB(tmp_path / "obs.db")
    start = datetime.now(timezone.utc) - timedelta(days=45)  # past the 30-day target
    old_id = _mk_session(db, start, target_days=30, status="stopped", cycles=9999)

    session = LearningSession.resume_or_create(db, target_days=30, interval_seconds=60)

    assert session.session_id != old_id, "an elapsed window must not be resumed"
    assert session.day_number(datetime.now(timezone.utc)) == 1
    db.close()


def test_no_session_creates_one(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    session = LearningSession.resume_or_create(db, target_days=30, interval_seconds=60)
    assert session.session_id is not None
    assert session.day_number(datetime.now(timezone.utc)) == 1
    db.close()


def test_current_learning_session_excludes_superseded(tmp_path):
    """The dashboard's current_learning_session must not return a superseded
    spurious session."""
    db = ObservatoryDB(tmp_path / "obs.db")
    old_start = datetime.now(timezone.utc) - timedelta(days=16)
    new_start = datetime.now(timezone.utc) - timedelta(hours=3)
    old_id = _mk_session(db, old_start, 30, "stopped", 215)
    _mk_session(db, new_start, 30, "active", 32)

    LearningSession.resume_or_create(db, target_days=30, interval_seconds=60)

    current = db.current_learning_session()
    assert current["id"] == old_id, "current session must be the resumed original, not the superseded one"
    db.close()
