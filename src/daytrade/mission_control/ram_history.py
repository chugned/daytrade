"""RAM history — JSONL log of process memory samples over time.

Mission control samples every probe and appends one entry per (pid,
sample_time). Stored in ``data/ram_history.jsonl`` with size cap so it
self-prunes. Rendered as a sparkline in each bot card.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[3]
HISTORY_PATH = REPO_ROOT / "data" / "ram_history.jsonl"

#: Cap on retained samples. At 1 sample / 5s per process, with ~5 known
#: processes, this covers ~28 hours of history (~2.5 MB on disk).
_MAX_LINES = 20_000


def append(samples: Iterable[Dict[str, Any]]) -> None:
    """Append one batch of process RAM samples. Best-effort."""
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as fh:
            for s in samples:
                fh.write(json.dumps(s) + "\n")
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


def by_bot(bot_names: List[str], window_minutes: int = 60) -> Dict[str, List[Dict[str, Any]]]:
    """Return recent samples grouped by bot name, oldest first per bot.

    A bot card reads its own series and renders a sparkline.
    """
    if not HISTORY_PATH.exists():
        return {n: [] for n in bot_names}

    cutoff = datetime.now(timezone.utc).timestamp() - window_minutes * 60
    series: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    s = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = s.get("ts")
                if not ts:
                    continue
                try:
                    ts_epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                if ts_epoch < cutoff:
                    continue
                bot = s.get("bot")
                if bot in bot_names:
                    series[bot].append(
                        {
                            "ts": ts,
                            "rss_mb": s.get("rss_mb"),
                            "pid": s.get("pid"),
                        }
                    )
    except OSError:
        pass
    return {n: series.get(n, []) for n in bot_names}
