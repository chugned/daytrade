"""End-to-end test for the CPU additions to the mission-control state API.

After the CPU integration, `/api/state` must include:

- ``host_cpu``: a single sample dict from ``sample_host_cpu``
- ``host_cpu_history``: list of host samples (oldest first), at least
  containing the just-taken one
- ``bots[i].cpu_history``: per-bot recent samples
- ``bots[i].total_pcpu_pct``: sum of pcpu_pct across the bot's processes

These tests run the FastAPI app in-process (no real network), with a
fake bot registry so we don't depend on the host's running processes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fake_mc_app(tmp_path, monkeypatch):
    # Fresh import so module-level paths are recomputed against tmp_path
    for name in list(sys.modules):
        if name.startswith("daytrade.mission_control"):
            del sys.modules[name]
    from daytrade.mission_control import app as mc_app
    from daytrade.mission_control import cpu_history as cpu_mod
    from daytrade.mission_control import ram_history as ram_mod

    monkeypatch.setattr(cpu_mod, "HISTORY_PATH", tmp_path / "cpu.jsonl")
    monkeypatch.setattr(ram_mod, "HISTORY_PATH", tmp_path / "ram.jsonl")

    # Stub list_processes — return one synthetic process per bot so
    # find_bot_processes has something to match.
    def fake_list_processes():
        return [
            {
                "pid": 111, "ppid": 1, "pmem_pct": 1.0, "pcpu_pct": 12.3,
                "rss_mb": 200.0, "etime": "00:30",
                "command": "/usr/bin/python3 -m daytrade learn --days 30",
            },
            {
                "pid": 222, "ppid": 1, "pmem_pct": 3.0, "pcpu_pct": 4.7,
                "rss_mb": 600.0, "etime": "00:20",
                "command": "/usr/bin/python3 -m nighttrade observe --live",
            },
        ]

    def fake_db_summary(_db_path):
        return {"available": False}

    monkeypatch.setattr(mc_app, "list_processes", fake_list_processes)
    monkeypatch.setattr(mc_app, "db_summary", fake_db_summary)
    # Stable host CPU so the assertions are deterministic
    monkeypatch.setattr(mc_app._cpu.os, "getloadavg", lambda: (1.0, 0.8, 0.5))
    monkeypatch.setattr(mc_app._cpu.os, "cpu_count", lambda: 4)

    bots = mc_app.default_bots()
    app = mc_app.create_app(bots=bots)
    return TestClient(app)


def test_state_includes_host_cpu_snapshot(fake_mc_app):
    r = fake_mc_app.get("/api/state")
    assert r.status_code == 200
    body = r.json()
    assert "host_cpu" in body
    hc = body["host_cpu"]
    assert hc["scope"] == "host"
    assert hc["load_1min"] == 1.0
    assert hc["cpu_count"] == 4
    assert hc["load_pct"] == pytest.approx(25.0)


def test_state_includes_host_cpu_history(fake_mc_app):
    # First call seeds, second call should have at least the seeded sample
    fake_mc_app.get("/api/state")
    body = fake_mc_app.get("/api/state").json()
    assert isinstance(body.get("host_cpu_history"), list)
    assert len(body["host_cpu_history"]) >= 1
    for sample in body["host_cpu_history"]:
        assert "load_pct" in sample
        assert "ts" in sample


def test_state_includes_per_bot_cpu_history_and_total(fake_mc_app):
    # Seed once to write a sample, then query
    fake_mc_app.get("/api/state")
    body = fake_mc_app.get("/api/state").json()
    bots = body["bots"]
    assert len(bots) >= 1
    for bot in bots:
        assert "cpu_history" in bot
        assert isinstance(bot["cpu_history"], list)
        assert "total_pcpu_pct" in bot


def test_per_bot_total_pcpu_pct_sums_process_samples(fake_mc_app):
    body = fake_mc_app.get("/api/state").json()
    by_name = {b["name"]: b for b in body["bots"]}
    # daytrade has one synthetic process with pcpu_pct=12.3
    assert by_name["daytrade"]["total_pcpu_pct"] == pytest.approx(12.3)
    # nighttrade has one synthetic process with pcpu_pct=4.7
    assert by_name["nighttrade"]["total_pcpu_pct"] == pytest.approx(4.7)
