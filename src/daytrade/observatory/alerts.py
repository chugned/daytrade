"""Alert system for the observatory.

Alerts are surfaced to the console and the log file. A Discord webhook sink is
included but **off unless explicitly configured** (and network-gated); email
and Telegram are left as clearly-marked future sinks.

To avoid flooding, each alert *kind* has a cooldown — the same condition will
not re-alert every cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..runtime import get_logger

_log = get_logger("observatory.alerts")

# Alert kinds and their re-alert cooldown (seconds).
_COOLDOWN_SECONDS = 900.0

LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    """A single alert event."""

    level: str  # info | warning | critical
    kind: str  # panic | illiquid | drawdown | accuracy | crash | api
    message: str
    timestamp: datetime

    def to_dict(self) -> Dict[str, str]:
        return {
            "level": self.level,
            "kind": self.kind,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
        }


def build_condition_alerts(
    *,
    global_condition: str,
    illiquid_symbols: List[str],
    paper_drawdown_pct: float,
    max_drawdown_pct: float,
    recent_accuracy: Optional[float],
    now: datetime,
) -> List[Alert]:
    """Derive the alerts implied by the current observatory state."""
    alerts: List[Alert] = []
    if global_condition == "PANIC":
        alerts.append(
            Alert(LEVEL_CRITICAL, "panic", "Market condition is PANIC — observation only.", now)
        )
    for sym in illiquid_symbols:
        alerts.append(
            Alert(
                LEVEL_WARNING, "illiquid", f"{sym} is illiquid — excluded from paper trading.", now
            )
        )
    if paper_drawdown_pct > max_drawdown_pct:
        alerts.append(
            Alert(
                LEVEL_CRITICAL,
                "drawdown",
                f"Paper drawdown {paper_drawdown_pct:.1%} exceeds "
                f"limit {max_drawdown_pct:.1%}.",
                now,
            )
        )
    if recent_accuracy is not None and recent_accuracy < 0.40:
        alerts.append(
            Alert(
                LEVEL_WARNING,
                "accuracy",
                f"Model accuracy collapsed to {recent_accuracy:.0%} "
                "— predictions flagged unreliable.",
                now,
            )
        )
    return alerts


class AlertManager:
    """Delivers alerts to console + log, with per-kind cooldown."""

    def __init__(
        self, db=None, discord_webhook: Optional[str] = None, allow_network: bool = False
    ) -> None:
        self._db = db
        self._discord_webhook = discord_webhook
        self._allow_network = allow_network
        self._last_emitted: Dict[str, datetime] = {}

    def emit(self, alert: Alert, *, force: bool = False) -> bool:
        """Deliver ``alert`` unless an identical kind fired within the cooldown.

        Returns True if the alert was actually delivered.
        """
        last = self._last_emitted.get(alert.kind)
        if (
            not force
            and last is not None
            and alert.timestamp - last < timedelta(seconds=_COOLDOWN_SECONDS)
        ):
            return False
        self._last_emitted[alert.kind] = alert.timestamp

        prefix = {"info": "[ALERT]", "warning": "[ALERT ⚠]", "critical": "[ALERT ‼]"}.get(
            alert.level, "[ALERT]"
        )
        line = f"{prefix} {alert.kind.upper()}: {alert.message}"
        # Console + log file (the log handler is configured by the observer).
        print(line, flush=True)
        if alert.level == LEVEL_CRITICAL:
            _log.error(line)
        elif alert.level == LEVEL_WARNING:
            _log.warning(line)
        else:
            _log.info(line)

        if self._db is not None:
            # Route to the alerts table (was incorrectly writing to errors,
            # inflating the dashboard's error count with informational
            # market observations like 'VETUSDT illiquid'). Fall back to
            # insert_error on older DB schemas that lack insert_alert.
            try:
                if hasattr(self._db, "insert_alert"):
                    self._db.insert_alert(alert.level, alert.kind, alert.message)
                else:
                    self._db.insert_error(f"alert:{alert.kind}", alert.message)
            except Exception:  # noqa: BLE001 - alerting must never crash the loop
                pass
        self._maybe_discord(alert)
        return True

    def emit_all(self, alerts: List[Alert]) -> int:
        return sum(1 for a in alerts if self.emit(a))

    def _maybe_discord(self, alert: Alert) -> None:
        """Post to a Discord webhook if one is configured and network is on."""
        if not self._discord_webhook or not self._allow_network:
            return
        try:
            import httpx

            with httpx.Client(timeout=5.0) as client:
                client.post(
                    self._discord_webhook, json={"content": f"**{alert.kind}** — {alert.message}"}
                )
        except Exception as exc:  # noqa: BLE001
            _log.warning("discord alert failed: %s", exc)

    # email / Telegram sinks are intentionally left for future work.
