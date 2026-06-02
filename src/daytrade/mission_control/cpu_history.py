"""CPU history — JSONL log of per-bot AND host-wide CPU samples over time.

Mirrors ``ram_history`` for symmetry. Same JSONL log + size cap + best-
effort write semantics; the only added wrinkle is the **host** scope —
a single 1-minute load-average reading per probe, normalised to a 0–100%
scale by dividing by ``os.cpu_count()`` so the sparkline is comparable
to per-process CPU%.

Why bother with host samples when each bot already reports its own
CPU%: when the *whole machine* is saturated (some other process is
pegging every core), per-bot CPU% drops because the bot can't get
scheduled — and you'd misread "bot is idle" for "bot is starved". The
host sparkline tells you which case you're in.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
HISTORY_PATH = REPO_ROOT / "data" / "cpu_history.jsonl"

#: Cap on retained samples. Bot samples: ~5 procs × 1/5s ≈ 1/sec.
#: Host samples: 1 per probe ≈ 1/5s. ~20k lines ≈ 28 hours; well under
#: 3 MB on disk.
_MAX_LINES = 20_000


# ---------------------------------------------------------------------------
# Write side
# ---------------------------------------------------------------------------

def append_bot_samples(samples: Iterable[Dict[str, Any]]) -> None:
    """Append per-bot CPU samples. Records are tagged ``scope='bot'``
    so ``host()`` can filter them out.
    """
    _append(({"scope": "bot", **s} for s in samples))


def append_host_sample(sample: Dict[str, Any]) -> None:
    """Append one host-wide sample (as returned by ``sample_host_cpu``)."""
    record = dict(sample)
    record.setdefault("scope", "host")
    _append([record])


def _append(records: Iterable[Dict[str, Any]]) -> None:
    """Best-effort line-append + size trim. Any OSError is swallowed —
    sampling is observational, never load-bearing."""
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        _trim_if_needed()
    except OSError:
        pass


def _trim_if_needed() -> None:
    """If the file is over the line cap, keep only the last _MAX_LINES."""
    try:
        if not HISTORY_PATH.exists():
            return
        with HISTORY_PATH.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) <= _MAX_LINES:
            return
        keep = lines[-_MAX_LINES:]
        tmp = HISTORY_PATH.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(keep), encoding="utf-8")
        os.replace(tmp, HISTORY_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Host sampling
# ---------------------------------------------------------------------------

def sample_host_cpu() -> Dict[str, Any]:
    """Take one host CPU sample using ``os.getloadavg()``.

    The 1-minute load average is divided by the CPU count to give a
    rough 0–100% utilisation, capped at 100 so a backlogged queue
    doesn't visually compress every other sample. Returned dict can be
    fed directly to ``append_host_sample``.
    """
    load_1min, _, _ = os.getloadavg()
    cpu_count: Optional[int] = os.cpu_count()
    if cpu_count is None or cpu_count <= 0:
        load_pct: Optional[float] = None
    else:
        pct = (float(load_1min) / float(cpu_count)) * 100.0
        load_pct = round(min(pct, 100.0), 1)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "scope": "host",
        "load_1min": round(float(load_1min), 2),
        "cpu_count": cpu_count,
        "load_pct": load_pct,
    }


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------

def by_bot(bot_names: List[str], window_minutes: int = 60) -> Dict[str, List[Dict[str, Any]]]:
    """Return recent per-bot samples grouped by bot name, oldest first."""
    if not HISTORY_PATH.exists():
        return {n: [] for n in bot_names}

    cutoff = datetime.now(timezone.utc).timestamp() - window_minutes * 60
    series: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in _iter_records():
        if record.get("scope", "bot") != "bot":
            continue
        ts_epoch = _ts_epoch(record.get("ts"))
        if ts_epoch is None or ts_epoch < cutoff:
            continue
        bot = record.get("bot")
        if bot in bot_names:
            series[bot].append({
                "ts": record["ts"],
                "pcpu_pct": record.get("pcpu_pct"),
                "pid": record.get("pid"),
            })
    return {n: series.get(n, []) for n in bot_names}


def host(window_minutes: int = 60) -> List[Dict[str, Any]]:
    """Return recent host samples, oldest first."""
    if not HISTORY_PATH.exists():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - window_minutes * 60
    out: List[Dict[str, Any]] = []
    for record in _iter_records():
        if record.get("scope") != "host":
            continue
        ts_epoch = _ts_epoch(record.get("ts"))
        if ts_epoch is None or ts_epoch < cutoff:
            continue
        out.append({
            "ts": record["ts"],
            "load_1min": record.get("load_1min"),
            "load_pct": record.get("load_pct"),
            "cpu_count": record.get("cpu_count"),
        })
    return out


def _iter_records() -> Iterable[Dict[str, Any]]:
    """Yield parsed JSONL records, skipping unparseable lines silently."""
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _ts_epoch(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
