"""Dashboard backend tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from daytrade.config import WatchlistConfig, load_config
from daytrade.dashboard import create_app
from daytrade.observatory import LiveMockFeed, ObservatoryDB, Observer

_T0 = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def _populated_db(tmp_path):
    db_path = tmp_path / "obs.db"
    obs = Observer(
        load_config(load_dotenv_file=False),
        WatchlistConfig(symbols=["BTCUSDT", "ETHUSDT"]),
        db=ObservatoryDB(db_path),
        feed=LiveMockFeed(),
    )
    obs.start()
    for k in range(3):
        obs.run_once(_T0 + timedelta(minutes=20 * k))
    obs.run_once(_T0 + timedelta(minutes=120))
    obs.stop()
    obs.db.close()
    return db_path


def test_dashboard_serves_index(tmp_path):
    client = TestClient(create_app(tmp_path / "empty.db"))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Learning Observatory" in resp.text


def test_dashboard_health_is_paper_only(tmp_path):
    client = TestClient(create_app(tmp_path / "empty.db"))
    body = client.get("/api/health").json()
    assert body["real_trading"] is False
    assert body["paper_only"] is True


def test_dashboard_overview_returns_data(tmp_path):
    client = TestClient(create_app(_populated_db(tmp_path)))
    body = client.get("/api/overview").json()
    assert "safety_score" in body
    assert body["symbols_observed"] == 2
    assert "status" in body and "condition" in body


def test_dashboard_symbols_endpoint(tmp_path):
    client = TestClient(create_app(_populated_db(tmp_path)))
    rows = client.get("/api/symbols").json()
    assert isinstance(rows, list) and len(rows) == 2
    assert {"symbol", "price", "trend", "safety_score", "status"} <= set(rows[0])


def test_dashboard_symbol_detail(tmp_path):
    client = TestClient(create_app(_populated_db(tmp_path)))
    body = client.get("/api/symbol/BTCUSDT").json()
    assert body["symbol"] == "BTCUSDT"
    assert len(body["series"]) > 0
    assert len(body["predictions"]) > 0


def test_dashboard_accuracy_paper_risk(tmp_path):
    client = TestClient(create_app(_populated_db(tmp_path)))
    for endpoint in ("/api/accuracy", "/api/paper", "/api/risk", "/api/safety-history"):
        resp = client.get(endpoint)
        assert resp.status_code == 200
        assert resp.json() is not None


def test_dashboard_empty_db_does_not_crash(tmp_path):
    """The dashboard works against a brand-new, empty database."""
    client = TestClient(create_app(tmp_path / "fresh.db"))
    overview = client.get("/api/overview").json()
    assert overview["symbols_observed"] == 0
    assert client.get("/api/symbols").json() == []


# --- learning observatory endpoints ----------------------------------------


def _learning_db(tmp_path):
    from daytrade.observatory import LearningSession

    db_path = tmp_path / "learn.db"
    db = ObservatoryDB(db_path)
    session = LearningSession.resume_or_create(db, target_days=30, interval_seconds=300)
    session.start = _T0
    obs = Observer(
        load_config(load_dotenv_file=False),
        WatchlistConfig(symbols=["BTCUSDT", "ETHUSDT"]),
        db=db,
        feed=LiveMockFeed(),
        learning_session=session,
    )
    obs.start()
    for k in range(3):
        obs.run_once(_T0 + timedelta(hours=8 * k))
    obs.run_once(_T0 + timedelta(days=1, hours=2))
    obs.stop()
    obs.db.close()
    return db_path


def test_dashboard_learning_endpoints(tmp_path):
    client = TestClient(create_app(_learning_db(tmp_path)))
    for endpoint in (
        "/api/progress",
        "/api/regimes",
        "/api/calibration",
        "/api/readiness",
        "/api/learning",
        "/api/activity",
        "/api/status",
        "/api/daily-reports",
        "/api/predictions",
        "/api/paper-trades",
    ):
        resp = client.get(endpoint)
        assert resp.status_code == 200, endpoint
        assert resp.json() is not None


def test_dashboard_progress_format(tmp_path):
    client = TestClient(create_app(_learning_db(tmp_path)))
    body = client.get("/api/progress").json()
    assert body["target_days"] == 30
    assert body["current_day"] >= 1
    assert "current_phase" in body
    assert isinstance(body["day_timeline"], list)


def test_dashboard_readiness_capped_and_safe_language(tmp_path):
    client = TestClient(create_app(_learning_db(tmp_path)))
    body = client.get("/api/readiness").json()
    # Early in the window readiness must be capped at 60.
    assert body["score"] <= 60.0
    assert "invest" not in body["level"].lower()


def test_dashboard_status_now_panel(tmp_path):
    client = TestClient(create_app(_learning_db(tmp_path)))
    body = client.get("/api/status").json()
    assert "current_step" in body
    assert "cycle" in body


def test_dashboard_health_denies_wallets_and_transfers(tmp_path):
    client = TestClient(create_app(tmp_path / "obs.db"))
    body = client.get("/api/health").json()
    assert body["real_trading"] is False
    assert body["wallets"] is False
    assert body["bank_transfers"] is False


def test_dashboard_logs_endpoint(tmp_path):
    """The /api/logs endpoint (the 'See Terminal' view) returns a tail."""
    client = TestClient(create_app(tmp_path / "obs.db"))
    body = client.get("/api/logs?lines=50").json()
    assert "lines" in body and isinstance(body["lines"], list)
    assert "exists" in body


def test_dashboard_gates_endpoint(tmp_path):
    """The /api/gates endpoint summarises the Phase 1-4 strategy gates."""
    client = TestClient(create_app(tmp_path / "obs.db"))
    body = client.get("/api/gates").json()
    for key in ("regime_blocks", "calibration_blocks", "meta_blocks", "meta_status"):
        assert key in body


def test_dashboard_price_chart_endpoint(tmp_path):
    """The /api/price-chart endpoint returns a chart payload (any network state)."""
    client = TestClient(create_app(tmp_path / "obs.db"))
    body = client.get("/api/price-chart?symbol=BTCUSDT&range=1D").json()
    assert body.get("symbol") == "BTCUSDT"
    assert body.get("range") == "1D"
    assert "points" in body  # populated if Binance is reachable, else []


def test_dashboard_db_writes_endpoint(tmp_path):
    """The /api/db-writes endpoint returns the database write-log tail."""
    client = TestClient(create_app(tmp_path / "obs.db"))
    body = client.get("/api/db-writes").json()
    assert "lines" in body and isinstance(body["lines"], list)
    assert "exists" in body


def test_dashboard_learning_summary_endpoint(tmp_path):
    """The /api/learning-summary endpoint returns plain-language cards."""
    client = TestClient(create_app(_learning_db(tmp_path)))
    body = client.get("/api/learning-summary").json()
    assert isinstance(body.get("cards"), list) and body["cards"]
    for card in body["cards"]:
        assert "title" in card and "icon" in card
        assert isinstance(card.get("items"), list)


def test_dashboard_open_without_password_env(tmp_path):
    """With no DASHBOARD_PASSWORD set the dashboard stays open (local use)."""
    client = TestClient(create_app(tmp_path / "obs.db"))
    assert client.get("/api/health").status_code == 200


def test_dashboard_password_gate(tmp_path, monkeypatch):
    """When DASHBOARD_PASSWORD is set, requests need the right password."""
    import base64

    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    client = TestClient(create_app(tmp_path / "obs.db"))

    # No credentials -> 401 with a Basic-Auth challenge.
    resp = client.get("/api/health")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate", "").lower().startswith("basic")

    # Wrong password -> 401.
    bad = base64.b64encode(b"user:nope").decode()
    assert client.get("/api/health", headers={"Authorization": f"Basic {bad}"}).status_code == 401

    # Correct password (any username) -> 200.
    good = base64.b64encode(b"user:s3cret").decode()
    ok = client.get("/api/health", headers={"Authorization": f"Basic {good}"})
    assert ok.status_code == 200
    assert ok.json()["paper_only"] is True
