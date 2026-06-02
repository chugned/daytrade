"""Startup reconciliation — refuse to act on a divergent state.

Before the bot can responsibly act, the local DB must agree with the
*source of truth* about what positions are open and what's owed. For paper
trading the source of truth is the DB itself; the reconciler checks the
DB is internally consistent (no orphaned trades, no impossible values).

For live trading (future), the source of truth is the exchange: query its
``openOrders`` / ``account`` endpoints, compare to the local DB, refuse
to act if they diverge until a human inspects.

This module ships the paper version + the interface the live version will
plug into. No live code path exists yet — that structural guarantee is
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..observatory.database import ObservatoryDB


@dataclass
class ReconciliationReport:
    """Findings from a startup reconciliation pass."""

    ok: bool
    anomalies: List[str] = field(default_factory=list)
    open_paper_positions: int = 0
    unevaluated_predictions: int = 0
    orphan_outcomes: int = 0

    def summary(self) -> str:
        if self.ok:
            return (
                f"state OK — {self.open_paper_positions} open paper "
                f"position(s), {self.unevaluated_predictions} prediction(s) "
                f"awaiting evaluation"
            )
        return "state DIVERGENT: " + "; ".join(self.anomalies)


def reconcile_paper_state(db: ObservatoryDB) -> ReconciliationReport:
    """Check the local DB is consistent before the bot starts trading.

    Reports issues rather than raising; the caller decides what to do.
    """
    anomalies: List[str] = []

    open_trades = db.open_paper_trades()
    open_count = len(open_trades)

    # Open trades must have entry/stop/target/quantity set and positive.
    for t in open_trades:
        qty = t.get("quantity") or 0
        entry = t.get("entry_price") or 0
        stop = t.get("stop") or 0
        target = t.get("target") or 0
        side = (t.get("side") or "").lower()
        bad: List[str] = []
        if qty <= 0:
            bad.append(f"quantity={qty}")
        if entry <= 0:
            bad.append(f"entry={entry}")
        if stop <= 0:
            bad.append(f"stop={stop}")
        if target <= 0:
            bad.append(f"target={target}")
        if side == "buy" and not (stop < entry < target):
            bad.append(f"long levels broken: stop {stop} entry {entry} target {target}")
        if side == "sell" and not (target < entry < stop):
            bad.append(f"short levels broken: target {target} entry {entry} stop {stop}")
        if bad:
            anomalies.append(f"open trade #{t.get('id')} {t.get('symbol')}: " + ", ".join(bad))

    # Unevaluated predictions — informational, not an anomaly until huge.
    unevaluated = db.unevaluated_predictions(limit=1)
    # Cheap count via a focused query: just check if there are unbounded amounts.
    big_backlog_threshold = 10_000
    backlog_count = 0
    try:
        backlog_count = len(db.unevaluated_predictions(limit=big_backlog_threshold))
    except Exception:
        pass
    if backlog_count >= big_backlog_threshold:
        anomalies.append(
            f"unevaluated prediction backlog >= {big_backlog_threshold} "
            "— evaluation may be lagging; check per-cycle cap"
        )

    # Orphan outcomes: outcome rows whose prediction id is gone.
    orphan_outcomes = 0
    try:
        rows = db._all(
            "SELECT COUNT(*) AS n FROM prediction_outcomes "
            "WHERE prediction_id NOT IN (SELECT id FROM predictions)"
        )
        orphan_outcomes = int(rows[0]["n"]) if rows else 0
    except Exception:
        pass
    if orphan_outcomes:
        anomalies.append(
            f"{orphan_outcomes} orphan outcome row(s) reference " "missing predictions"
        )

    return ReconciliationReport(
        ok=not anomalies,
        anomalies=anomalies,
        open_paper_positions=open_count,
        unevaluated_predictions=len(unevaluated),
        orphan_outcomes=orphan_outcomes,
    )
