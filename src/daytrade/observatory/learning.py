"""30-day paper-trading learning session.

A :class:`LearningSession` is the spine of ``daytrade learn`` — it tracks how
far into the 30-day observation window the bot is, what learning *phase* it is
in, how many cycles it has completed versus expected, and its uptime. The
session is persisted to ``data/learning_state.json`` so a restart resumes the
same window rather than starting the clock over.

This is paper / simulation observation only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
LEARNING_STATE_PATH = _REPO_ROOT / "data" / "learning_state.json"

#: Minimum realistic wall-clock seconds for one full cycle. The configured
#: --interval is the WAIT between cycles; a cycle's actual work (scanning N
#: symbols, each ~3 Binance round-trips) takes ~2-3 minutes on the live bot.
#: Using interval alone (e.g. 60s) as the denominator for "expected_cycles"
#: produces an unreachable ceiling — the day timeline then shows permanent
#: red even when the bot runs 24/7. We clamp the denominator to at least
#: this floor so the metric reflects what is physically achievable.
REALISTIC_CYCLE_SECONDS = 180

# The six learning phases, in order, with the progress fraction at which each
# ends. Fractions (not fixed days) so any ``--days`` value maps sensibly.
LEARNING_PHASES = [
    ("Warm-up", 0.07),
    ("Data collection", 0.27),
    ("Pattern discovery", 0.50),
    ("Reliability testing", 0.70),
    ("Stress testing", 0.93),
    ("Final evaluation", 1.01),
]


def phase_for(day_number: int, target_days: int) -> str:
    """Return the learning-phase name for ``day_number`` of ``target_days``."""
    frac = day_number / max(1, target_days)
    for name, end in LEARNING_PHASES:
        if frac <= end:
            return name
    return LEARNING_PHASES[-1][0]


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class LearningSession:
    """Time / phase / progress tracking for a multi-day learning run."""

    start: datetime
    target_days: int = 30
    interval_seconds: int = 300
    session_id: Optional[int] = None
    cycles_completed: int = 0

    # -- time & progress -----------------------------------------------------

    def days_elapsed(self, now: datetime) -> float:
        return max(0.0, (now - self.start).total_seconds() / 86_400.0)

    def day_number(self, now: datetime) -> int:
        """1-based day index, clamped to ``[1, target_days]``."""
        return max(1, min(self.target_days, int(self.days_elapsed(now)) + 1))

    def days_remaining(self, now: datetime) -> float:
        return max(0.0, self.target_days - self.days_elapsed(now))

    def progress_pct(self, now: datetime) -> float:
        return min(100.0, self.days_elapsed(now) / self.target_days * 100.0)

    @property
    def _effective_cycle_seconds(self) -> float:
        """Realistic per-cycle wall time (interval clamped to the floor)."""
        return max(float(self.interval_seconds), float(REALISTIC_CYCLE_SECONDS))

    def total_expected_cycles(self) -> int:
        return int(self.target_days * 86_400 / self._effective_cycle_seconds)

    def expected_cycles(self, now: datetime) -> int:
        """Cycles that *should* have run by ``now`` at the realistic cycle rate."""
        elapsed = (now - self.start).total_seconds()
        return max(1, int(elapsed / self._effective_cycle_seconds))

    def uptime_pct(self, now: datetime) -> float:
        return min(100.0, self.cycles_completed / self.expected_cycles(now) * 100.0)

    def phase(self, now: datetime) -> str:
        return phase_for(self.day_number(now), self.target_days)

    def is_complete(self, now: datetime) -> bool:
        return self.days_elapsed(now) >= self.target_days

    # -- persistence ---------------------------------------------------------

    def state_dict(
        self, now: datetime, counts: Dict[str, Any], status: str = "OBSERVING"
    ) -> Dict[str, Any]:
        """Assemble the full ``learning_state.json`` payload."""
        return {
            "start_date": self.start.isoformat(),
            "target_days": self.target_days,
            "interval_seconds": self.interval_seconds,
            "current_day": self.day_number(now),
            "days_remaining": round(self.days_remaining(now), 2),
            "progress_pct": round(self.progress_pct(now), 1),
            "cycles_completed": self.cycles_completed,
            "expected_cycles": self.expected_cycles(now),
            "total_expected_cycles": self.total_expected_cycles(),
            "uptime_pct": round(self.uptime_pct(now), 1),
            "current_phase": self.phase(now),
            "status": status,
            "symbols_monitored": counts.get("symbols_monitored", 0),
            "predictions_made": counts.get("predictions_made", 0),
            "predictions_evaluated": counts.get("predictions_evaluated", 0),
            "fake_trades": counts.get("fake_trades", 0),
            "skipped_trades": counts.get("skipped_trades", 0),
            "complete": self.is_complete(now),
            "last_update": now.isoformat(),
        }

    def save_state(
        self,
        now: datetime,
        counts: Dict[str, Any],
        status: str = "OBSERVING",
        path: Path | str | None = None,
    ) -> None:
        # Resolve the path at call time so tests can redirect LEARNING_STATE_PATH.
        path = Path(path) if path is not None else LEARNING_STATE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.state_dict(now, counts, status), indent=2), encoding="utf-8"
        )

    @classmethod
    def resume_or_create(
        cls, db, target_days: int = 30, interval_seconds: int = 300
    ) -> LearningSession:
        """Resume the active learning session from the DB, or create one.

        Resuming preserves the original start date so the 30-day clock keeps
        running across restarts rather than resetting.
        """
        now = datetime.now(timezone.utc)
        # Resume the OLDEST in-progress window (by start date), regardless of
        # whether it was last marked 'active' or 'stopped'. A clean shutdown
        # marks the session 'stopped', so resuming only on 'active' abandoned
        # the running 30-day clock on every restart and reset the day counter
        # to 1 (the 2026-06-03 'Day 1/30' bug). The trade history + models were
        # never affected — only this counter.
        existing = db.resumable_learning_session()
        if existing:
            candidate = cls(
                start=_parse(existing["start_ts"]),
                target_days=existing["target_days"],
                interval_seconds=existing["interval_seconds"],
                session_id=existing["id"],
                cycles_completed=existing.get("cycles_completed", 0),
            )
            if not candidate.is_complete(now):
                # Fold any spurious newer duplicate windows into this one so
                # the dashboard shows a single, honest day counter.
                db.supersede_learning_sessions(keep_id=candidate.session_id)
                return candidate
        session_id = db.start_learning_session(target_days, interval_seconds)
        return cls(
            start=now,
            target_days=target_days,
            interval_seconds=interval_seconds,
            session_id=session_id,
        )


def load_learning_state(path: Path | str = LEARNING_STATE_PATH) -> Optional[Dict[str, Any]]:
    """Read the persisted learning-state JSON, or None if it does not exist."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
