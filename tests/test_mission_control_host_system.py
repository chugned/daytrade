"""Mission control — host system metrics + bot 'sleeping' state.

Two additions, both surfaced through ``/api/state``:

1. ``host_system``: a single dict with RAM (used/total GB + pct), disk
   (free/total GB + pct), and CPU load pct. Lets the operator see at a
   glance how much of the *machine* is being used — answering "how much
   RAM/CPU/storage am I using right now?" without opening Activity
   Monitor.

2. ``bots[i].now``: the contents of each bot's ``data/now.json`` (its
   "what am I doing right now" status), including the ``sleeping`` flag
   that the nighttrade observer writes when it pauses for closed markets
   (ADR-0007). The dashboard uses this to show a PAUSED/SLEEPING badge.
"""

from __future__ import annotations

import dataclasses
import json
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fake_mc_app(tmp_path, monkeypatch):
    for name in list(sys.modules):
        if name.startswith("daytrade.mission_control"):
            del sys.modules[name]
    from daytrade.mission_control import app as mc_app
    from daytrade.mission_control import cpu_history as cpu_mod
    from daytrade.mission_control import ram_history as ram_mod

    monkeypatch.setattr(cpu_mod, "HISTORY_PATH", tmp_path / "cpu.jsonl")
    monkeypatch.setattr(ram_mod, "HISTORY_PATH", tmp_path / "ram.jsonl")

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
    monkeypatch.setattr(mc_app._cpu.os, "getloadavg", lambda: (1.0, 0.8, 0.5))
    monkeypatch.setattr(mc_app._cpu.os, "cpu_count", lambda: 4)
    return mc_app, tmp_path, monkeypatch


# --------------------------------------------------------------------------- #
# sample_host_system — the pure probe                                          #
# --------------------------------------------------------------------------- #

def test_sample_host_system_shape(fake_mc_app):
    mc_app, _tmp, _mp = fake_mc_app
    sysm = mc_app.sample_host_system()
    # RAM
    assert sysm["ram_total_gb"] > 0
    assert 0 <= sysm["ram_used_pct"] <= 100
    assert sysm["ram_used_gb"] >= 0
    # Disk
    assert sysm["disk_total_gb"] > 0
    assert sysm["disk_free_gb"] >= 0
    assert 0 <= sysm["disk_used_pct"] <= 100
    # CPU load pct present (may be None only if getloadavg unavailable)
    assert "cpu_load_pct" in sysm


def test_state_includes_host_system(fake_mc_app):
    mc_app, _tmp, _mp = fake_mc_app
    client = TestClient(mc_app.create_app(bots=mc_app.default_bots()))
    body = client.get("/api/state").json()
    assert "host_system" in body
    hs = body["host_system"]
    for key in (
        "ram_total_gb", "ram_used_gb", "ram_used_pct",
        "disk_total_gb", "disk_free_gb", "disk_used_pct",
        "cpu_load_pct",
    ):
        assert key in hs, f"missing host_system.{key}"


def test_host_system_degrades_gracefully(fake_mc_app):
    """If the underlying probes raise, sample_host_system must return a
    dict with None values rather than crash the whole endpoint."""
    mc_app, _tmp, monkeypatch = fake_mc_app

    def boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(mc_app.shutil, "disk_usage", boom)
    sysm = mc_app.sample_host_system()
    assert sysm["disk_total_gb"] is None
    # RAM probe is independent — should still be present (or None, never raise)
    assert "ram_total_gb" in sysm


# --------------------------------------------------------------------------- #
# bots[i].now — surfacing the sleeping flag                                    #
# --------------------------------------------------------------------------- #

def test_state_surfaces_bot_now_sleeping(fake_mc_app):
    """When a bot's project_root has data/now.json with sleeping=True,
    /api/state must expose it under bots[i].now."""
    mc_app, tmp_path, monkeypatch = fake_mc_app

    # Point nighttrade's project_root at a tmp dir with a sleeping now.json
    nt_root = tmp_path / "nt"
    (nt_root / "data").mkdir(parents=True)
    (nt_root / "data" / "now.json").write_text(json.dumps({
        "sleeping": True,
        "current_step": "Sleeping — market closed until Wed 09:30 EDT",
        "next_cycle_at": "2026-06-03T13:30:00+00:00",
    }))

    bots = [
        dataclasses.replace(b, project_root=nt_root) if b.name == "nighttrade" else b
        for b in mc_app.default_bots()
    ]

    client = TestClient(mc_app.create_app(bots=bots))
    body = client.get("/api/state").json()
    by_name = {b["name"]: b for b in body["bots"]}
    nt = by_name["nighttrade"]
    assert nt["now"] is not None
    assert nt["now"]["sleeping"] is True
    assert "Sleeping" in nt["now"]["current_step"]


def test_state_bot_now_absent_is_none(fake_mc_app):
    """A bot with no now.json gets now=None (not an error)."""
    mc_app, tmp_path, monkeypatch = fake_mc_app
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    bots = [dataclasses.replace(b, project_root=empty_root) for b in mc_app.default_bots()]
    client = TestClient(mc_app.create_app(bots=bots))
    body = client.get("/api/state").json()
    for b in body["bots"]:
        assert b["now"] is None
