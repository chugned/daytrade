"""Market Safety Observatory tests — database, feed, safety, observer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daytrade.config import WatchlistConfig, load_config
from daytrade.observatory import (
    LiveMockFeed,
    ObservatoryDB,
    Observer,
    aggregate_safety,
    build_prediction_memory,
    compute_safety_score,
    evaluate_prediction,
)
from daytrade.observatory.daily_report import build_daily_report_markdown
from daytrade.observatory.safety_score import SafetyInputs

_T0 = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def _db(tmp_path) -> ObservatoryDB:
    return ObservatoryDB(tmp_path / "obs.db")


def _small_watchlist() -> WatchlistConfig:
    # Fewer symbols keeps observer tests fast.
    return WatchlistConfig(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])


def _observer(tmp_path) -> Observer:
    return Observer(load_config(load_dotenv_file=False), _small_watchlist(),
                    db=_db(tmp_path), feed=LiveMockFeed())


# --- database --------------------------------------------------------------

def test_db_creates_schema(tmp_path):
    db = _db(tmp_path)
    for table in ("market_snapshots", "predictions", "prediction_outcomes",
                  "paper_trades", "safety_scores", "symbol_health",
                  "bot_runs", "errors"):
        assert db.count(table) == 0
    db.close()


def test_db_bot_run_lifecycle(tmp_path):
    db = _db(tmp_path)
    run_id = db.start_bot_run(pid=999)
    db.heartbeat(run_id, 5)
    assert db.current_bot_run()["cycles"] == 5
    db.stop_bot_run(run_id)
    assert db.current_bot_run()["status"] == "stopped"
    db.close()


def test_db_recovers_dangling_runs(tmp_path):
    """Only runs whose PID is no longer alive should be marked crashed —
    a sibling process that's still running must not be clobbered."""
    import os
    db = _db(tmp_path)
    # PIDs above 4_000_000 are outside the default Linux/macOS PID space.
    dead_pid = 4_000_001
    while True:
        try:
            os.kill(dead_pid, 0)
            dead_pid += 1
        except (ProcessLookupError, OSError):
            break
    db.start_bot_run(pid=dead_pid)
    crashed = db.mark_dangling_runs_crashed()
    assert crashed == 1
    assert db.current_bot_run()["status"] == "crashed"
    db.close()


def test_db_keeps_alive_runs_running(tmp_path):
    """A still-alive sibling PID must NOT be marked crashed when a
    different process calls mark_dangling_runs_crashed (this was the
    bug that took the live bot's dashboard offline)."""
    import os
    db = _db(tmp_path)
    db.start_bot_run(pid=os.getpid())  # ourselves — definitely alive
    crashed = db.mark_dangling_runs_crashed()
    assert crashed == 0
    assert db.current_bot_run()["status"] == "running"
    db.close()


def test_heartbeat_resurrects_a_spuriously_crashed_row(tmp_path):
    """A heartbeat is the definitive 'I am alive' signal — it must
    restore status to 'running' and clear stopped_ts so the dashboard
    recovers automatically from a spurious crash mark."""
    db = _db(tmp_path)
    run_id = db.start_bot_run(pid=1)
    db.stop_bot_run(run_id, status="crashed")
    assert db.current_bot_run()["status"] == "crashed"
    db.heartbeat(run_id, cycles=42)
    row = db.current_bot_run()
    assert row["status"] == "running"
    assert row["stopped_ts"] is None
    assert row["cycles"] == 42
    db.close()


def test_db_outcome_upsert(tmp_path):
    db = _db(tmp_path)
    pid = db.insert_prediction(symbol="BTCUSDT", direction="buy", confidence=0.6)
    db.upsert_outcome(pid, symbol="BTCUSDT", price_5m=100.0)
    db.upsert_outcome(pid, price_1h=104.0, directionally_correct=1)
    row = db.outcomes()[0]
    assert row["price_5m"] == 100.0 and row["price_1h"] == 104.0


# --- feed ------------------------------------------------------------------

def test_feed_is_deterministic():
    feed = LiveMockFeed()
    assert feed.price_at("BTCUSDT", _T0) == feed.price_at("BTCUSDT", _T0)


def test_feed_restart_safe_history():
    """Candles ending at a time are identical regardless of when queried."""
    a = LiveMockFeed().candles_at("ETHUSDT", _T0, n_bars=100)
    b = LiveMockFeed().candles_at("ETHUSDT", _T0, n_bars=100)
    assert [c.close for c in a] == [c.close for c in b]


def test_feed_prices_are_sane():
    feed = LiveMockFeed()
    btc = feed.price_at("BTCUSDT", _T0)
    assert 30_000 < btc < 120_000  # not exploded, not collapsed


# --- safety score ----------------------------------------------------------

def test_safety_score_calm_is_high():
    a = compute_safety_score(SafetyInputs(
        trend_strength=0.5, volatility=0.003, liquidity_notional=900_000,
        spread_bps=2.5, imbalance=0.05, chop=False,
        slippage_estimate_bps=3, panic=False, recent_accuracy=0.58))
    assert a.score >= 61
    assert a.status == "SAFE_TO_OBSERVE"


def test_safety_score_panic_is_unsafe():
    a = compute_safety_score(SafetyInputs(
        trend_strength=0.9, volatility=0.03, liquidity_notional=400_000,
        spread_bps=12, imbalance=-0.6, chop=False,
        slippage_estimate_bps=30, panic=True))
    assert a.score <= 20
    assert a.status == "UNSAFE" and a.condition == "PANIC"


def test_safety_score_illiquid_condition():
    a = compute_safety_score(SafetyInputs(
        trend_strength=0.4, volatility=0.008, liquidity_notional=80_000,
        spread_bps=28, imbalance=0.2, chop=False, slippage_estimate_bps=22,
        panic=False))
    assert a.condition == "ILLIQUID"


def test_aggregate_safety_is_pessimistic():
    """The global score is dragged toward the weakest symbol."""
    good = compute_safety_score(SafetyInputs(0.5, 0.003, 900_000, 2.5, 0.0,
                                             False, 3, False))
    bad = compute_safety_score(SafetyInputs(0.9, 0.03, 100_000, 30, -0.6,
                                            False, 35, True))
    agg = aggregate_safety([good, bad])
    assert agg.score < (good.score + bad.score) / 2
    assert agg.condition == "PANIC"  # worst condition wins


# --- prediction tracking ---------------------------------------------------

def test_prediction_not_evaluated_before_horizon():
    feed = LiveMockFeed()
    pred = {"id": 1, "ts": _T0.isoformat(), "symbol": "BTCUSDT",
            "direction": "buy", "entry": 100.0, "stop": 99.0, "target": 102.0,
            "confidence": 0.6, "market_condition": "CALM"}
    outcome, full = evaluate_prediction(pred, feed, _T0 + timedelta(minutes=2))
    assert outcome is None and full is False


def test_prediction_evaluated_after_horizon():
    feed = LiveMockFeed()
    entry = feed.price_at("BTCUSDT", _T0)
    pred = {"id": 1, "ts": _T0.isoformat(), "symbol": "BTCUSDT",
            "direction": "buy", "entry": entry, "stop": entry * 0.97,
            "target": entry * 1.03, "confidence": 0.6,
            "market_condition": "CALM"}
    outcome, _ = evaluate_prediction(pred, feed, _T0 + timedelta(minutes=70))
    assert outcome is not None
    assert outcome["price_5m"] is not None
    assert outcome["directionally_correct"] in (0, 1)


def test_prediction_memory_detects_false_confidence():
    rows = [{"directionally_correct": 0, "confidence": 0.82,
             "market_condition": "CHOPPY", "symbol": "DOGEUSDT"}
            for _ in range(6)]
    memory = build_prediction_memory(rows)
    assert memory.false_confidence_warnings()


# --- observer --------------------------------------------------------------

def test_observer_runs_one_cycle(tmp_path):
    obs = _observer(tmp_path)
    obs.start()
    summary = obs.run_once(_T0)
    assert summary.cycle == 1
    assert summary.symbols_observed == 3
    assert 0 <= summary.global_score <= 100
    obs.stop()


def test_observer_stores_predictions(tmp_path):
    obs = _observer(tmp_path)
    obs.start()
    obs.run_once(_T0)
    assert obs.db.count("predictions") == 3
    assert obs.db.count("market_snapshots") == 3
    assert obs.db.count("safety_scores") == 1
    obs.stop()


def test_observer_evaluates_outcomes_later(tmp_path):
    obs = _observer(tmp_path)
    obs.start()
    obs.run_once(_T0)
    # No outcomes yet — horizons have not elapsed.
    assert obs.db.count("prediction_outcomes") == 0
    # A cycle 70 minutes later evaluates the earlier predictions.
    obs.run_once(_T0 + timedelta(minutes=70))
    assert obs.db.count("prediction_outcomes") >= 3
    obs.stop()


def test_observer_restart_recovers(tmp_path):
    """A new observer marks the prior dangling run crashed and resumes."""
    db_path = tmp_path / "obs.db"
    obs1 = Observer(load_config(load_dotenv_file=False), _small_watchlist(),
                    db=ObservatoryDB(db_path), feed=LiveMockFeed())
    obs1.start()
    obs1.run_once(_T0)
    obs1.db.close()  # simulate a crash (no clean stop)

    obs2 = Observer(load_config(load_dotenv_file=False), _small_watchlist(),
                    db=ObservatoryDB(db_path), feed=LiveMockFeed())
    obs2.start()
    runs = [r for r in [obs2.db.current_bot_run()] if r]
    assert runs and runs[0]["status"] == "running"
    obs2.stop()


def test_daily_report_generates(tmp_path):
    obs = _observer(tmp_path)
    obs.start()
    obs.run_once(_T0)
    md = build_daily_report_markdown(obs.db, _T0.date().isoformat())
    obs.stop()
    assert "Daily Report" in md
    assert "Recommendation" in md
    # No financial-advice language.
    assert "you should buy" not in md.lower()
    assert "you should sell" not in md.lower()
