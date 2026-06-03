"""Pin: the dashboard must not show the meta-model as 'warming up' once it
has actually retrained.

2026-06-03: the meta-model had retrained 129 times, but the dashboard
scraped only the last 800 activity events for the 'meta-model retrained'
marker. Between retrains (every 30 cycles × ~33 symbols) there are ~1500
activity events, so the marker fell outside the 800-row window and the
card lied 'warming up' even though the model was trained and gating
(blocking 43 trades). Fix: query the retrain event directly, unbounded.
"""

from __future__ import annotations

from daytrade.dashboard.data import DashboardData
from daytrade.observatory import ObservatoryDB


def test_meta_trained_even_when_retrain_is_old(tmp_path):
    path = tmp_path / "obs.db"
    db = ObservatoryDB(path)
    # One real retrain, long ago...
    db.insert_activity("meta-model retrained", "853 samples · base win rate 21%")
    # ...then bury it under far more than the 800-row scan window.
    for i in range(1200):
        db.insert_activity("meta-model blocked SOLUSDT", "low win prob")
    db.close()

    gates = DashboardData(path).gates()
    assert gates["meta_status"] == "trained", (
        "meta-model retrained 1200+ events ago must still read 'trained', "
        "not 'warming up'"
    )
    assert "853 samples" in gates["meta_detail"]


def test_meta_warming_up_only_when_never_retrained(tmp_path):
    path = tmp_path / "obs.db"
    db = ObservatoryDB(path)
    for _ in range(50):
        db.insert_activity("meta-model blocked ETHUSDT", "low win prob")
    db.close()
    gates = DashboardData(path).gates()
    assert gates["meta_status"] == "warming up"
