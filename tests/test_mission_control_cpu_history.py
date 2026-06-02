"""Tests for the CPU history sparkline backing the mission-control dashboard.

Mirrors ``ram_history`` for symmetry — but ALSO captures host-wide CPU
load (normalised to 0–100%) since the per-process samples don't show
when the whole machine is saturated by something outside the bots.

Persisted as one JSONL line per sample with two shapes:

  {"ts": "...", "scope": "bot",  "bot": "daytrade",   "pid": 123, "pcpu_pct": 1.2}
  {"ts": "...", "scope": "host", "load_pct": 45.0, "load_1min": 3.6, "cpu_count": 8}

Same size-cap + best-effort write semantics as ``ram_history``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


def _reset_module_path(tmp_path, monkeypatch):
    """Point the module's HISTORY_PATH at a temp file and (re)import fresh."""
    # Force a fresh import so module-level HISTORY_PATH is recomputed
    sys.modules.pop("daytrade.mission_control.cpu_history", None)
    from daytrade.mission_control import cpu_history as mod

    monkeypatch.setattr(mod, "HISTORY_PATH", tmp_path / "cpu_history.jsonl")
    return mod


# ---------------------------------------------------------------------------
# Bot samples
# ---------------------------------------------------------------------------

def test_append_bot_samples_writes_one_line_per_sample(tmp_path, monkeypatch):
    mod = _reset_module_path(tmp_path, monkeypatch)
    mod.append_bot_samples(
        [
            {"ts": "2026-06-02T05:00:00+00:00", "bot": "daytrade",   "pid": 1, "pcpu_pct": 1.2},
            {"ts": "2026-06-02T05:00:00+00:00", "bot": "nighttrade", "pid": 2, "pcpu_pct": 0.5},
        ]
    )
    lines = mod.HISTORY_PATH.read_text().strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert {p["bot"] for p in parsed} == {"daytrade", "nighttrade"}
    assert all(p["scope"] == "bot" for p in parsed)


def test_by_bot_returns_recent_samples_grouped(tmp_path, monkeypatch):
    mod = _reset_module_path(tmp_path, monkeypatch)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    mod.append_bot_samples(
        [
            {"ts": now, "bot": "daytrade",   "pid": 1, "pcpu_pct": 1.5},
            {"ts": now, "bot": "nighttrade", "pid": 2, "pcpu_pct": 0.5},
            {"ts": now, "bot": "daytrade",   "pid": 1, "pcpu_pct": 2.0},
        ]
    )
    series = mod.by_bot(["daytrade", "nighttrade"], window_minutes=60)
    assert len(series["daytrade"]) == 2
    assert len(series["nighttrade"]) == 1
    assert series["daytrade"][0]["pcpu_pct"] == 1.5
    assert series["daytrade"][1]["pcpu_pct"] == 2.0


def test_by_bot_drops_samples_outside_window(tmp_path, monkeypatch):
    mod = _reset_module_path(tmp_path, monkeypatch)
    # Old sample (way past the window)
    mod.append_bot_samples(
        [{"ts": "2024-01-01T00:00:00+00:00", "bot": "daytrade", "pid": 1, "pcpu_pct": 99.0}]
    )
    series = mod.by_bot(["daytrade"], window_minutes=60)
    assert series["daytrade"] == []


def test_by_bot_returns_empty_lists_for_unknown_bots(tmp_path, monkeypatch):
    mod = _reset_module_path(tmp_path, monkeypatch)
    series = mod.by_bot(["nope"], window_minutes=60)
    assert series == {"nope": []}


# ---------------------------------------------------------------------------
# Host samples
# ---------------------------------------------------------------------------

def test_sample_host_cpu_returns_load_pct_normalised(tmp_path, monkeypatch):
    """``sample_host_cpu`` reads ``os.getloadavg()`` and ``os.cpu_count()``,
    normalises the 1-minute load to a 0–100% scale (load 1.0 on 4 cores
    = 25%) so the sparkline is comparable to per-bot pcpu_pct."""
    mod = _reset_module_path(tmp_path, monkeypatch)
    monkeypatch.setattr(mod.os, "getloadavg", lambda: (2.0, 1.5, 1.0))
    monkeypatch.setattr(mod.os, "cpu_count", lambda: 8)
    sample = mod.sample_host_cpu()
    assert sample["scope"] == "host"
    assert sample["load_1min"] == 2.0
    assert sample["cpu_count"] == 8
    assert sample["load_pct"] == pytest.approx(25.0)  # 2 / 8 * 100
    assert "ts" in sample


def test_sample_host_cpu_caps_at_100_when_overloaded(tmp_path, monkeypatch):
    """Load can exceed CPU count (the queue is backed up). Cap at 100%
    so the sparkline scale stays meaningful — a 400% number squashes
    every other sample to zero visually."""
    mod = _reset_module_path(tmp_path, monkeypatch)
    monkeypatch.setattr(mod.os, "getloadavg", lambda: (32.0, 30.0, 28.0))
    monkeypatch.setattr(mod.os, "cpu_count", lambda: 8)
    sample = mod.sample_host_cpu()
    assert sample["load_pct"] == 100.0


def test_sample_host_cpu_handles_unknown_cpu_count(tmp_path, monkeypatch):
    """``os.cpu_count()`` returns ``None`` on weird systems. The sample
    should still record a row, just with ``load_pct=None``."""
    mod = _reset_module_path(tmp_path, monkeypatch)
    monkeypatch.setattr(mod.os, "getloadavg", lambda: (1.0, 1.0, 1.0))
    monkeypatch.setattr(mod.os, "cpu_count", lambda: None)
    sample = mod.sample_host_cpu()
    assert sample["scope"] == "host"
    assert sample["load_1min"] == 1.0
    assert sample["cpu_count"] is None
    assert sample["load_pct"] is None


def test_append_host_sample_round_trips_via_host_history(tmp_path, monkeypatch):
    mod = _reset_module_path(tmp_path, monkeypatch)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    mod.append_host_sample({
        "ts": now, "scope": "host",
        "load_pct": 42.5, "load_1min": 3.4, "cpu_count": 8,
    })
    samples = mod.host(window_minutes=60)
    assert len(samples) == 1
    assert samples[0]["load_pct"] == 42.5
    assert samples[0]["cpu_count"] == 8


def test_host_drops_bot_scoped_rows(tmp_path, monkeypatch):
    """``host()`` is for the host series — bot rows in the same file
    must not bleed into it."""
    mod = _reset_module_path(tmp_path, monkeypatch)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    mod.append_bot_samples(
        [{"ts": now, "bot": "daytrade", "pid": 1, "pcpu_pct": 99.0}]
    )
    mod.append_host_sample({
        "ts": now, "scope": "host",
        "load_pct": 33.3, "load_1min": 2.7, "cpu_count": 8,
    })
    assert len(mod.host(window_minutes=60)) == 1
    assert mod.host(window_minutes=60)[0]["load_pct"] == 33.3


# ---------------------------------------------------------------------------
# File hygiene (mirrors ram_history)
# ---------------------------------------------------------------------------

def test_log_trims_when_over_cap(tmp_path, monkeypatch):
    """Once the file exceeds the line cap, the oldest rows should be
    dropped on the next append — bounded disk footprint."""
    mod = _reset_module_path(tmp_path, monkeypatch)
    # Shrink the cap so the test runs fast
    monkeypatch.setattr(mod, "_MAX_LINES", 10)
    for i in range(20):
        mod.append_bot_samples(
            [{"ts": f"2026-06-02T05:00:0{i % 10}+00:00", "bot": "d", "pid": 1, "pcpu_pct": float(i)}]
        )
    lines = mod.HISTORY_PATH.read_text().strip().splitlines()
    assert len(lines) <= 10


def test_append_is_best_effort_on_oserror(tmp_path, monkeypatch):
    """A disk-full / permission error must not propagate out of the
    appender — sampling is observational, not load-bearing."""
    mod = _reset_module_path(tmp_path, monkeypatch)

    # Point HISTORY_PATH at a location whose parent can't be created
    # (under a regular file, which is not a directory). mkdir() will
    # raise OSError('Not a directory'); append must swallow it.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    monkeypatch.setattr(mod, "HISTORY_PATH", blocker / "nested" / "cpu.jsonl")
    # Must not raise
    mod.append_bot_samples([{"ts": "x", "bot": "d", "pid": 1, "pcpu_pct": 0.0}])
    mod.append_host_sample({"ts": "x", "scope": "host", "load_pct": 1.0,
                            "load_1min": 0.5, "cpu_count": 8})
