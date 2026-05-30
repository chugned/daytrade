"""Learning, regime and calibration metrics.

Aggregations over the observatory database that answer "is the bot
improving?": confidence calibration, per-regime performance, and the full
learning-metrics block (prediction / trading-simulation / market-understanding
/ reliability). All read-only; all about simulated paper results.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

# Confidence calibration buckets (lower bound inclusive, upper exclusive).
_CALIBRATION_BUCKETS = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80),
                        (0.80, 0.90), (0.90, 1.01)]
_OVERCONFIDENCE_GAP = 0.12  # stated - actual above this => overconfident


def _bucket_label(lo: float, hi: float) -> str:
    return f"{int(lo * 100)}-{int(min(hi, 1.0) * 100)}%"


def confidence_calibration(outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare stated confidence against realised accuracy, per bucket.

    Answers: when the bot says 80%, is it actually right ~80% of the time?
    """
    buckets: List[Dict[str, Any]] = []
    over_warnings: List[str] = []
    under_warnings: List[str] = []

    for lo, hi in _CALIBRATION_BUCKETS:
        rows = [o for o in outcomes
                if o.get("directionally_correct") is not None
                and lo <= float(o.get("confidence") or 0.0) < hi]
        samples = len(rows)
        if samples == 0:
            buckets.append({"bucket": _bucket_label(lo, hi), "samples": 0,
                            "stated_confidence": None, "accuracy": None,
                            "gap": None, "flag": "no data"})
            continue
        stated = sum(float(o["confidence"]) for o in rows) / samples
        accuracy = sum(int(o["directionally_correct"]) for o in rows) / samples
        gap = stated - accuracy
        flag = "calibrated"
        if gap > _OVERCONFIDENCE_GAP:
            flag = "OVERCONFIDENT"
            over_warnings.append(
                f"{_bucket_label(lo, hi)}: states {stated * 100:.0f}% but is "
                f"only {accuracy * 100:.0f}% accurate ({samples} samples)")
        elif gap < -_OVERCONFIDENCE_GAP:
            flag = "underconfident"
            under_warnings.append(
                f"{_bucket_label(lo, hi)}: states {stated * 100:.0f}% but is "
                f"{accuracy * 100:.0f}% accurate ({samples} samples)")
        buckets.append({
            "bucket": _bucket_label(lo, hi), "samples": samples,
            "stated_confidence": round(stated * 100, 1),
            "accuracy": round(accuracy * 100, 1),
            "gap": round(gap * 100, 1), "flag": flag,
        })

    return {
        "buckets": buckets,
        "overconfidence_warnings": over_warnings,
        "underconfidence_warnings": under_warnings,
        "notice": "High confidence is not yet proven reliable.",
    }


def regime_metrics(outcomes: List[Dict[str, Any]],
                   regime_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-regime performance: accuracy, fake PnL, sample counts."""
    per: Dict[str, Dict[str, float]] = {}
    for o in outcomes:
        cond = o.get("market_condition") or "UNKNOWN"
        agg = per.setdefault(cond, {"samples": 0.0, "correct": 0.0,
                                    "fake_pnl": 0.0})
        if o.get("directionally_correct") is not None:
            agg["samples"] += 1
            agg["correct"] += int(o["directionally_correct"])
        agg["fake_pnl"] += float(o.get("realized_pnl") or 0.0)

    by_regime = {
        cond: {
            "samples": int(a["samples"]),
            "accuracy": round(a["correct"] / a["samples"] * 100, 1)
            if a["samples"] else None,
            "fake_pnl": round(a["fake_pnl"], 2),
        }
        for cond, a in per.items()
    }
    timeline = [{"ts": r["ts"], "condition": r.get("condition"),
                 "regime": r.get("regime"), "safety_score": r.get("safety_score")}
                for r in regime_rows]
    counts = Counter(r.get("condition") for r in regime_rows)
    return {
        "by_regime": by_regime,
        "timeline": timeline,
        "regime_counts": dict(counts),
        "regimes_observed": len([c for c in counts if c]),
    }


def roll_up_day(db, day_date: str, day_number: int,
                expected_cycles: int) -> Dict[str, Any]:
    """Aggregate one calendar day into a ``daily_metrics`` row.

    ``status`` is green (ran well), yellow (partial data) or red (errors /
    significant downtime), which drives the day-timeline colours.
    """
    def _on(rows: List[Dict[str, Any]], key: str = "ts") -> List[Dict[str, Any]]:
        return [r for r in rows if str(r.get(key, "")).startswith(day_date)]

    safety = _on(db.safety_score_history(limit=20000))
    predictions = _on(db.recent_predictions(limit=20000))
    outcomes = _on(db.outcomes(limit=20000), key="predicted_ts")
    closed = _on(db.closed_paper_trades(limit=20000), key="ts_close")
    # The errors table also collects informational alerts (e.g.
    # "illiquid — excluded from paper trading"); those are notices about
    # market conditions, not software faults. Only real exceptions should
    # turn a day red.
    errors = [e for e in _on(db.recent_errors(limit=5000))
              if not str(e.get("context", "")).startswith("alert:")]

    cycles = len(safety)
    scored = [o for o in outcomes if o.get("directionally_correct") is not None]
    accuracy = (sum(int(o["directionally_correct"]) for o in scored)
                / len(scored)) if scored else 0.0
    fake_pnl = sum(t.get("pnl") or 0.0 for t in closed)
    conditions = Counter(s.get("condition") for s in safety)
    regimes = Counter(s.get("regime") for s in _on(db.regime_periods(limit=20000)))
    uptime = min(100.0, cycles / max(1, expected_cycles) * 100.0)

    if errors or uptime < 50:
        status = "red"
    elif uptime < 85:
        status = "yellow"
    else:
        status = "green"

    return {
        "day_number": day_number, "cycles": cycles,
        "expected_cycles": expected_cycles,
        "uptime_pct": round(uptime, 1),
        "predictions_made": len(predictions),
        "predictions_evaluated": len(scored),
        "accuracy": round(accuracy * 100, 1),
        "fake_pnl": round(fake_pnl, 2),
        "drawdown_pct": 0.0,
        "paper_trades": len(closed),
        "skipped": 0,
        "dominant_regime": (regimes.most_common(1)[0][0] if regimes else None),
        "dominant_condition": (conditions.most_common(1)[0][0]
                               if conditions else None),
        "errors": len(errors),
        "status": status,
    }


def learning_metrics(db) -> Dict[str, Any]:
    """The full learning-metrics block: is the strategy actually improving?"""
    outcomes = db.outcomes(limit=8000)
    closed = db.closed_paper_trades(limit=8000)
    snapshots = db.latest_snapshots()
    health = db.latest_symbol_health()
    errors = db.recent_errors(limit=2000)

    scored = [o for o in outcomes if o.get("directionally_correct") is not None]
    n = len(scored)
    correct = sum(int(o["directionally_correct"]) for o in scored)
    target_hits = sum(int(o.get("target_hit") or 0) for o in outcomes)
    stop_hits = sum(int(o.get("stop_hit") or 0) for o in outcomes)
    confidences = [float(o.get("confidence") or 0) for o in scored]

    # Directional false positives/negatives (buy that fell / sell that rose).
    fp = sum(1 for o in scored if o.get("direction") == "buy"
             and not o["directionally_correct"])
    fn = sum(1 for o in scored if o.get("direction") == "sell"
             and not o["directionally_correct"])

    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl") or 0) <= 0]
    gross_win = sum(t.get("pnl") or 0 for t in wins)
    gross_loss = abs(sum(t.get("pnl") or 0 for t in losses))

    api_failures = sum(1 for e in errors
                       if "api" in (e.get("context") or "").lower()
                       or "exchange" in (e.get("context") or "").lower())
    chop_now = sum(1 for s in snapshots if s.get("chop"))

    return {
        "prediction": {
            "evaluated": n,
            "directional_accuracy": round(correct / n * 100, 1) if n else 0.0,
            "target_hit_rate": round(target_hits / len(outcomes) * 100, 1)
            if outcomes else 0.0,
            "stop_hit_rate": round(stop_hits / len(outcomes) * 100, 1)
            if outcomes else 0.0,
            "avg_confidence": round(sum(confidences) / n * 100, 1) if n else 0.0,
            "false_positive_rate": round(fp / n * 100, 1) if n else 0.0,
            "false_negative_rate": round(fn / n * 100, 1) if n else 0.0,
        },
        "trading_simulation": {
            "fake_pnl": round(sum(t.get("pnl") or 0 for t in closed), 2),
            "trades": len(closed),
            "win_rate": round(len(wins) / len(closed) * 100, 1)
            if closed else 0.0,
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2)
            if gross_loss > 1e-9 else 0.0,
            "fees_paid": round(sum(t.get("fees") or 0 for t in closed), 2),
            "slippage_cost": round(sum(t.get("slippage") or 0 for t in closed), 2),
        },
        "market_understanding": {
            "chop_detected_now": chop_now,
            "panic_symbols": sum(1 for h in health
                                 if h.get("status") == "PANIC"),
            "liquidity_rejections": sum(1 for h in health
                                        if h.get("status") == "TOO ILLIQUID"),
            "choppy_rejections": sum(1 for h in health
                                     if h.get("status") == "TOO CHOPPY"),
        },
        "reliability": {
            "api_failures": api_failures,
            "errors_logged": len(errors),
            "model_degradation_warnings": sum(
                1 for e in errors if "accuracy" in (e.get("message") or "").lower()),
        },
    }
