"""Daily auto-report for the observatory.

Generates ``reports/daily/YYYY-MM-DD.md`` from the observatory database — a
plain-language end-of-day review. The recommendation describes whether
*conditions favoured this paper strategy*; it never tells anyone to buy or
sell. This is educational analysis, not financial advice.
"""

from __future__ import annotations

from collections import Counter
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import ObservatoryDB
from .prediction_tracker import build_prediction_memory

_REPO_ROOT = Path(__file__).resolve().parents[3]
DAILY_DIR = _REPO_ROOT / "reports" / "daily"

_SAFETY_NOTE = (
    "EDUCATIONAL ONLY — paper/simulation analysis. No real orders, "
    "no wallets, no money movement. Not financial advice."
)


def _on_date(rows: List[Dict[str, Any]], day: str, key: str = "ts") -> List[Dict[str, Any]]:
    return [r for r in rows if str(r.get(key, "")).startswith(day)]


def _recommendation(score: float) -> str:
    """Plain-language verdict — about conditions, never an instruction."""
    if score >= 61:
        return (
            "Conditions were acceptable for paper observation of this "
            "strategy. Continue observing."
        )
    if score >= 41:
        return (
            "Conditions were mixed. Waiting / observation-only was the "
            "reasonable stance for this strategy."
        )
    if score >= 21:
        return "Conditions were unfavourable for this strategy. " "Do not trade — observe only."
    return (
        "Conditions were hazardous for this strategy. "
        "Do not trade under any account — observation only."
    )


def build_daily_report_markdown(db: ObservatoryDB, day: Optional[str] = None) -> str:
    """Build the daily markdown report for ``day`` (defaults to today UTC)."""
    day = day or _date.today().isoformat()

    safety = _on_date(db.safety_score_history(limit=5000), day)
    closed = _on_date(db.closed_paper_trades(limit=5000), day, key="ts_close")
    health = db.latest_symbol_health()
    outcomes = db.outcomes(limit=5000)
    errors = _on_date(db.recent_errors(limit=500), day)

    # --- market summary ---
    if safety:
        scores = [s["score"] for s in safety]
        avg_score = sum(scores) / len(scores)
        last = safety[-1]
        conditions = Counter(s["condition"] for s in safety)
        dominant = conditions.most_common(1)[0][0]
    else:
        avg_score, last, dominant, conditions = 50.0, None, "MIXED", Counter()

    # --- symbol rankings ---
    ranked = sorted(health, key=lambda h: h.get("safety_score") or 0, reverse=True)
    safest = ranked[:3]
    riskiest = list(reversed(ranked[-3:])) if len(ranked) >= 3 else []

    # --- paper PnL ---
    total_pnl = sum(t.get("pnl") or 0.0 for t in closed)
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    total_fees = sum(t.get("fees") or 0.0 for t in closed)

    # --- prediction accuracy + memory ---
    memory = build_prediction_memory(outcomes)

    # --- skipped trades (symbols not in good paper condition) ---
    status_counts = Counter(h.get("status") for h in health)

    lines: List[str] = [
        f"# Market Safety Observatory — Daily Report {day}",
        "",
        f"> {_SAFETY_NOTE}",
        "",
        "## Market summary",
        "",
        f"- Average safety score: **{avg_score:.0f}/100**",
        f"- Latest condition: **{last['condition'] if last else 'n/a'}** "
        f"({last['status'] if last else 'n/a'})",
        f"- Dominant condition today: **{dominant}**",
        "- Conditions observed: " + ", ".join(f"{c} ×{n}" for c, n in conditions.most_common()),
        "",
        "## Safest symbols (paper conditions)",
        "",
    ]
    for h in safest:
        lines.append(
            f"- **{h['symbol']}** — score {h.get('safety_score', 0):.0f}, "
            f"status {h.get('status')}"
        )
    lines += ["", "## Riskiest symbols", ""]
    for h in riskiest:
        lines.append(
            f"- **{h['symbol']}** — score {h.get('safety_score', 0):.0f}, "
            f"status {h.get('status')}"
        )

    lines += [
        "",
        "## Paper trading",
        "",
        f"- Closed simulated trades: {len(closed)}",
        f"- Win rate: {(len(wins) / len(closed) * 100) if closed else 0:.0f}%",
        f"- Simulated PnL: {total_pnl:+,.2f}",
        f"- Simulated fees: {total_fees:,.2f}",
        "",
        "## Prediction accuracy",
        "",
        f"- Evaluated predictions: {memory.total}",
        f"- Overall directional accuracy: {memory.overall_accuracy * 100:.0f}%",
        f"- Model currently reliable: **{memory.is_reliable}**",
    ]
    if memory.by_condition:
        lines.append(
            "- Accuracy by regime: "
            + ", ".join(
                f"{g.label} {g.accuracy * 100:.0f}% ({g.samples})"
                for g in memory.by_condition.values()
            )
        )

    lines += ["", "## Skipped / non-traded symbols", ""]
    for status, n in status_counts.most_common():
        if status and status != "GOOD PAPER CONDITIONS":
            lines.append(f"- {status}: {n} symbol(s)")

    lines += ["", "## Mistakes & warnings", ""]
    warnings = memory.false_confidence_warnings()
    if memory.should_stop_trading():
        warnings.insert(
            0, "Recent accuracy below threshold — predictions " "should not be acted on."
        )
    if errors:
        warnings.append(f"{len(errors)} error(s)/alert(s) logged today.")
    if not warnings:
        warnings.append("No significant warnings today.")
    lines += [f"- {w}" for w in warnings]

    lines += [
        "",
        "## Recommendation",
        "",
        _recommendation(avg_score),
        "",
        "*Conditions language only — this is not advice to buy or sell.*",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_daily_report(db: ObservatoryDB, day: Optional[str] = None) -> Path:
    """Write the daily report to ``reports/daily/YYYY-MM-DD.md`` and return it."""
    day = day or _date.today().isoformat()
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    path = DAILY_DIR / f"{day}.md"
    path.write_text(build_daily_report_markdown(db, day), encoding="utf-8")
    return path
