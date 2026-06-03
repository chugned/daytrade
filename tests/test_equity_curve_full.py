"""Pin the full-history equity curve (downsampled).

The accumulated-gain chart used to show only the last 3000 cycles, so the
early ramp from the €1000 start was invisible. ``equity_curve(full=True)``
returns the ENTIRE history, downsampled to a bounded point count so the
payload stays light on mobile while still spanning start → now.
"""

from __future__ import annotations

from daytrade.observatory import ObservatoryDB


def _seed(db, n: int, start_equity: float = 1000.0) -> None:
    for i in range(n):
        db.insert_safety_score(
            ts=f"2026-05-{1 + i // 1000:02d}T00:{(i % 60):02d}:00+00:00",
            score=50, status="WAIT", condition="CHOPPY",
            equity=start_equity + i,  # strictly increasing so first/last are identifiable
        )


def test_full_history_under_cap_returns_all(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    _seed(db, 500)
    curve = db.equity_curve(full=True, max_points=2500)
    assert len(curve) == 500
    assert curve[0]["equity"] == 1000.0          # the €1000 origin is present
    assert curve[-1]["equity"] == 1000.0 + 499
    db.close()


def test_full_history_over_cap_is_downsampled_but_spans_start_to_now(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    _seed(db, 6000)
    curve = db.equity_curve(full=True, max_points=2000)
    # Bounded point count (never blow up the mobile payload)...
    assert len(curve) <= 2001
    assert len(curve) >= 1000
    # ...but the first and last real points are preserved (full span).
    assert curve[0]["equity"] == 1000.0
    assert curve[-1]["equity"] == 1000.0 + 5999
    # Monotonic order preserved (ascending by time).
    eqs = [p["equity"] for p in curve]
    assert eqs == sorted(eqs)
    db.close()


def test_default_still_returns_last_n(tmp_path):
    """Non-full callers keep the old last-`limit` behaviour (newest window)."""
    db = ObservatoryDB(tmp_path / "obs.db")
    _seed(db, 4000)
    curve = db.equity_curve(limit=3000)
    assert len(curve) == 3000
    # Last 3000 of a 0..3999 ramp → ends at 3999, starts at 1000+1000.
    assert curve[-1]["equity"] == 1000.0 + 3999
    assert curve[0]["equity"] == 1000.0 + 1000
    db.close()
