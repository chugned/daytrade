"""Activity feed — append-only log of what Claude (and its agents) are doing.

Writes JSONL to ``data/agent_activity.jsonl``. Each entry is one line.
Cheap to write, cheap to tail, safe across processes (one writer at a
time in practice — append + fsync). Mission control reads the tail
and renders a live timeline.

Schema (one JSON object per line):
{
  "ts":      "2026-06-02T00:30:00.123456+00:00",
  "agent":   "claude-main" | "qa-audit-2" | ...,
  "kind":    "status" | "edit" | "shell" | "spawn" | "finding" |
             "complete" | "roadmap",
  "summary": "<one-line human-readable>",
  "detail":  "<optional longer text or JSON-stringified blob>"
}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
ACTIVITY_PATH = REPO_ROOT / "data" / "agent_activity.jsonl"
ROADMAP_PATH = REPO_ROOT / "data" / "agent_roadmap.json"


def log(
    agent: str,
    kind: str,
    summary: str,
    detail: Optional[str] = None,
) -> None:
    """Append one event. Best-effort; never raises."""
    try:
        ACTIVITY_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "kind": kind,
            "summary": summary,
        }
        if detail is not None:
            entry["detail"] = detail
        with ACTIVITY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001 - logging must never crash callers
        pass


def tail(n: int = 60) -> List[Dict[str, Any]]:
    """Return the last ``n`` entries (newest last)."""
    if not ACTIVITY_PATH.exists():
        return []
    try:
        lines = ACTIVITY_PATH.read_text(encoding="utf-8").splitlines()
        out: List[Dict[str, Any]] = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    except OSError:
        return []


def write_roadmap(roadmap: Dict[str, Any]) -> None:
    """Replace the current roadmap file."""
    try:
        ROADMAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = ROADMAP_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(roadmap, indent=2), encoding="utf-8")
        os.replace(tmp, ROADMAP_PATH)
    except Exception:  # noqa: BLE001
        pass


def read_roadmap() -> Dict[str, Any]:
    if not ROADMAP_PATH.exists():
        return {}
    try:
        return json.loads(ROADMAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
