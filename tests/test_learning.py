"""30-Day Paper Trading Learning Observatory tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daytrade.config import WatchlistConfig, load_config
from daytrade.observatory import (
    LearningSession,
    LiveMockFeed,
    ObservatoryDB,
    Observer,
    compute_readiness,
    confidence_calibration,
    phase_for,
    regime_metrics,
)
from daytrade.observatory.metrics import learning_metrics, roll_up_day
from daytrade.observatory.readiness import ReadinessInputs, readiness_level

_START = datetime(2026, 5, 1, tzinfo=timezone.utc)


def _session(days=30, interval=300) -> LearningSession:
    return LearningSession(start=_START, target_days=days, interval_seconds=interval)


# --- learning session ------------------------------------------------------


def test_learning_session_day_number():
    s = _session()
    assert s.day_number(_START) == 1
    assert s.day_number(_START + timedelta(days=6, hours=12)) == 7
    # day number is clamped to the target window
    assert s.day_number(_START + timedelta(days=40)) == 30


def test_learning_session_progress():
    s = _session()
    assert s.progress_pct(_START) == pytest.approx(0.0, abs=0.1)
    assert s.progress_pct(_START + timedelta(days=15)) == pytest.approx(50.0, abs=1)
    assert s.is_complete(_START + timedelta(days=30, hours=1))


def test_learning_session_phases_progress_in_order():
    s = _session()
    seen = []
    for d in range(0, 30, 3):
        p = s.phase(_START + timedelta(days=d, hours=2))
        if p not in seen:
            seen.append(p)
    assert seen[0] == "Warm-up"
    assert seen[-1] == "Final evaluation"
    assert "Pattern discovery" in seen


def test_phase_for_boundaries():
    assert phase_for(1, 30) == "Warm-up"
    assert phase_for(30, 30) == "Final evaluation"


def test_learning_session_uptime():
    s = _session(interval=300)
    s.cycles_completed = 288  # one day's worth of 5-min cycles
    now = _START + timedelta(days=1)
    assert s.uptime_pct(now) == pytest.approx(100.0, abs=1.0)


def test_learning_session_resume_or_create(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    a = LearningSession.resume_or_create(db, target_days=30)
    b = LearningSession.resume_or_create(db, target_days=30)
    # The second call resumes the same session rather than restarting.
    assert a.session_id == b.session_id
    db.close()


def test_learning_state_file_written(tmp_path):
    s = _session()
    path = tmp_path / "learning_state.json"
    s.cycles_completed = 100
    s.save_state(_START + timedelta(days=3), {"predictions_made": 50}, path=path)
    import json

    state = json.loads(path.read_text())
    assert state["current_day"] == 4
    assert state["predictions_made"] == 50
    assert state["current_phase"] == "Data collection"


# --- readiness score -------------------------------------------------------


def _readiness_inputs(day_number: int) -> ReadinessInputs:
    return ReadinessInputs(
        day_number=day_number,
        target_days=30,
        predictions_evaluated=800,
        uptime_pct=98,
        max_drawdown_pct=4,
        overall_accuracy=0.62,
        false_confidence_count=0,
        regimes_observed=5,
        regime_accuracy_spread=0.1,
        api_failures=0,
    )


def test_readiness_capped_before_day_30():
    """Even with strong evidence, readiness is capped at 60 before day 30."""
    result = compute_readiness(_readiness_inputs(day_number=7))
    assert result.score <= 60.0
    assert result.capped is True


def test_readiness_uncapped_at_completion():
    result = compute_readiness(_readiness_inputs(day_number=30))
    assert result.score > 60.0
    assert result.capped is False


def test_readiness_levels():
    assert readiness_level(10) == "NOT ENOUGH DATA"
    assert readiness_level(40) == "UNRELIABLE"
    assert readiness_level(60) == "PROMISING BUT UNPROVEN"
    assert readiness_level(78) == "STABLE IN PAPER CONDITIONS"
    assert readiness_level(95) == "STRONG PAPER PERFORMANCE, STILL NOT GUARANTEED"


def test_readiness_never_says_safe_to_invest():
    result = compute_readiness(_readiness_inputs(day_number=30))
    assert "invest" not in result.level.lower()
    assert "invest" not in result.headline.lower()


def test_readiness_weak_evidence_has_blockers():
    weak = ReadinessInputs(
        day_number=3,
        target_days=30,
        predictions_evaluated=20,
        uptime_pct=70,
        max_drawdown_pct=15,
        overall_accuracy=0.46,
        false_confidence_count=2,
        regimes_observed=1,
        regime_accuracy_spread=0.4,
        api_failures=3,
    )
    result = compute_readiness(weak)
    assert result.level in ("NOT ENOUGH DATA", "UNRELIABLE")
    assert len(result.blockers) >= 3


# --- metrics ---------------------------------------------------------------


def test_confidence_calibration_buckets():
    outcomes = [
        {"directionally_correct": 1, "confidence": 0.55},
        {"directionally_correct": 0, "confidence": 0.85},
        {"directionally_correct": 0, "confidence": 0.82},
        {"directionally_correct": 0, "confidence": 0.88},
    ]
    calib = confidence_calibration(outcomes)
    labels = [b["bucket"] for b in calib["buckets"]]
    assert labels == ["50-60%", "60-70%", "70-80%", "80-90%", "90-100%"]
    # The 80-90% bucket is confidently wrong -> flagged overconfident.
    b80 = next(b for b in calib["buckets"] if b["bucket"] == "80-90%")
    assert b80["flag"] == "OVERCONFIDENT"


def test_regime_metrics():
    outcomes = [
        {"market_condition": "CALM", "directionally_correct": 1, "realized_pnl": 5.0},
        {"market_condition": "CHOPPY", "directionally_correct": 0, "realized_pnl": -3.0},
    ]
    regimes = [
        {"ts": "2026-05-01T00:00:00", "condition": "CALM", "regime": "calm", "safety_score": 70}
    ]
    m = regime_metrics(outcomes, regimes)
    assert "CALM" in m["by_regime"] and "CHOPPY" in m["by_regime"]
    assert m["by_regime"]["CALM"]["accuracy"] == 100.0
    assert m["by_regime"]["CHOPPY"]["fake_pnl"] == -3.0


def test_learning_metrics_block(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    m = learning_metrics(db)
    # The four metric blocks are always present, even on an empty database.
    assert set(m) == {"prediction", "trading_simulation", "market_understanding", "reliability"}
    db.close()


def test_roll_up_day(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    db.insert_safety_score(
        ts="2026-05-01T10:00:00+00:00", score=55, status="WAIT", condition="CHOPPY"
    )
    metric = roll_up_day(db, "2026-05-01", day_number=1, expected_cycles=288)
    assert metric["day_number"] == 1
    assert metric["cycles"] == 1
    assert metric["status"] in ("green", "yellow", "red")
    db.close()


# --- observer with a learning session --------------------------------------


def test_observer_writes_learning_state(tmp_path, monkeypatch):
    from daytrade.observatory import learning as learning_mod

    state_path = tmp_path / "learning_state.json"
    monkeypatch.setattr(learning_mod, "LEARNING_STATE_PATH", state_path)

    db = ObservatoryDB(tmp_path / "obs.db")
    session = LearningSession.resume_or_create(db, target_days=30, interval_seconds=300)
    session.start = _START
    obs = Observer(
        load_config(load_dotenv_file=False),
        WatchlistConfig(symbols=["BTCUSDT", "ETHUSDT"]),
        db=db,
        feed=LiveMockFeed(),
        learning_session=session,
    )
    obs.start()
    obs.run_once(_START)
    obs.run_once(_START + timedelta(hours=6))
    obs.stop()

    assert db.count("readiness_scores") == 2
    assert db.count("regime_periods") == 2
    assert db.count("activity_events") > 0
    assert db.current_learning_session()["cycles_completed"] == 2
    db.close()


def test_observer_day_rollover_creates_daily_metric(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    session = LearningSession.resume_or_create(db, target_days=30, interval_seconds=300)
    session.start = _START
    obs = Observer(
        load_config(load_dotenv_file=False),
        WatchlistConfig(symbols=["BTCUSDT"]),
        db=db,
        feed=LiveMockFeed(),
        learning_session=session,
    )
    obs.start()
    obs.run_once(_START)
    obs.run_once(_START + timedelta(days=1, hours=1))  # crosses a day boundary
    obs.stop()
    assert len(db.daily_metrics()) >= 1
    db.close()
