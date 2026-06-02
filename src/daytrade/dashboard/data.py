"""Dashboard data assembly.

Turns the observatory database into the JSON payloads the dashboard pages
consume. Every accessor is defensive — an empty or freshly-created database
yields sensible empty/default structures rather than errors, so the dashboard
works even before the observer has run.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from ..config import load_config
from ..observatory.database import DEFAULT_DB_PATH, ObservatoryDB
from ..observatory.metrics import (
    confidence_calibration,
    learning_metrics,
    regime_metrics,
)
from ..observatory.prediction_tracker import build_prediction_memory

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LATEST_CYCLE = _REPO_ROOT / "reports" / "observer" / "latest.json"
_NOW_PATH = _REPO_ROOT / "data" / "now.json"
_LEARNING_STATE = _REPO_ROOT / "data" / "learning_state.json"
_DAILY_DIR = _REPO_ROOT / "reports" / "daily"
_LOG_FILE = _REPO_ROOT / "logs" / "daytrade.log"
_DBWRITE_LOG = _REPO_ROOT / "logs" / "db-writes.log"

# Price-chart ranges -> (Binance kline interval, number of candles).
_CHART_RANGES: Dict[str, tuple[str, int]] = {
    "1D": ("15m", 96),
    "5D": ("1h", 120),
    "1M": ("4h", 180),
    "6M": ("1d", 182),
    "1Y": ("1d", 365),
}
# Small in-process cache so toggling ranges does not hammer Binance.
_CHART_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_CHART_TTL = 45.0

# A heartbeat older than this means the observer is not considered "live".
_HEARTBEAT_STALE_SECONDS = 1200.0


def _tail(path: Path, n: int) -> List[str]:
    """Return the last ``n`` lines of ``path`` — reads only the tail bytes."""
    if not path.exists():
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 200_000))  # last ~200 KB is plenty
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    return data.splitlines()[-n:]


def _starting_cash() -> float:
    """The configured paper starting capital (matches what the observer uses)."""
    try:
        return load_config(load_dotenv_file=False).paper.starting_cash
    except Exception:  # noqa: BLE001 - fall back to the schema default
        return 1_000.0


_STARTING_CASH = _starting_cash()


def _read_json(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def _json_field(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _trend_label(slope: Optional[float]) -> str:
    if slope is None:
        return "flat"
    if slope > 0.0004:
        return "up"
    if slope < -0.0004:
        return "down"
    return "flat"


def _drawdown(closed: List[Dict[str, Any]]) -> Dict[str, float]:
    """Equity curve + max drawdown from closed paper trades."""
    equity = _STARTING_CASH
    peak = equity
    max_dd = 0.0
    ordered = sorted(closed, key=lambda t: t.get("ts_close") or "")
    for trade in ordered:
        equity += trade.get("pnl") or 0.0
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    return {"equity": round(equity, 2), "peak": round(peak, 2),
            "max_drawdown_pct": round(max_dd * 100, 2)}


class DashboardData:
    """Read-only data accessor for the dashboard backend."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db = ObservatoryDB(db_path)

    # -- pages ---------------------------------------------------------------

    def overview(self) -> Dict[str, Any]:
        safety = self.db.latest_safety_score() or {}
        run = self.db.current_bot_run() or {}
        health = self.db.latest_symbol_health()
        closed = self.db.closed_paper_trades(limit=5000)
        memory = build_prediction_memory(self.db.outcomes(limit=5000))

        live = self._observer_live(run)
        cycle = self._latest_cycle()
        dd = _drawdown(closed)
        equity = cycle.get("equity", dd["equity"])

        ranked = sorted(health, key=lambda h: h.get("safety_score") or 0,
                        reverse=True)
        return {
            "safety_score": safety.get("score", 50.0),
            "status": safety.get("status", "WAIT"),
            "condition": safety.get("condition", "MIXED"),
            "reasons": _json_field(safety.get("reasons")) or [],
            "bot_running": live,
            "bot_status": run.get("status", "never started"),
            "cycles": run.get("cycles", 0),
            "last_heartbeat": run.get("last_heartbeat_ts"),
            "equity": equity,
            "starting_cash": _STARTING_CASH,
            "drawdown_pct": cycle.get("drawdown_pct", dd["max_drawdown_pct"] / 100.0),
            "max_drawdown_pct": dd["max_drawdown_pct"],
            "symbols_observed": len(health),
            "best_symbols": [self._mini(h) for h in ranked[:3]],
            "worst_symbols": [self._mini(h) for h in reversed(ranked[-3:])]
            if len(ranked) >= 3 else [],
            "model_reliable": memory.is_reliable,
            "prediction_accuracy": round(memory.overall_accuracy * 100, 1),
            "updated": datetime.now(timezone.utc).isoformat(),
        }

    def symbols(self) -> List[Dict[str, Any]]:
        health = {h["symbol"]: h for h in self.db.latest_symbol_health()}
        snaps = {s["symbol"]: s for s in self.db.latest_snapshots()}
        preds = self._latest_prediction_per_symbol()
        rows: List[Dict[str, Any]] = []
        for symbol, h in sorted(health.items()):
            s = snaps.get(symbol, {})
            p = preds.get(symbol, {})
            rows.append({
                "symbol": symbol,
                "price": s.get("price"),
                "trend": _trend_label(s.get("trend_slope")),
                "volatility": s.get("volatility"),
                "liquidity": h.get("book_notional"),
                "spread_bps": s.get("spread_bps"),
                "imbalance": s.get("imbalance"),
                "chop": bool(s.get("chop")),
                "prediction": p.get("direction", "—"),
                "confidence": p.get("confidence"),
                "recent_accuracy": h.get("recent_accuracy"),
                "safety_score": h.get("safety_score"),
                "status": h.get("status", "WATCH ONLY"),
            })
        return rows

    def symbol_detail(self, symbol: str) -> Dict[str, Any]:
        symbol = symbol.upper()
        snaps = self.db.snapshots_for(symbol, limit=240)
        preds = self.db.recent_predictions(limit=60, symbol=symbol)
        health = {h["symbol"]: h for h in self.db.latest_symbol_health()}
        closed = [t for t in self.db.closed_paper_trades(limit=2000)
                  if t["symbol"] == symbol]
        open_trades = [t for t in self.db.open_paper_trades()
                       if t["symbol"] == symbol]
        return {
            "symbol": symbol,
            "health": health.get(symbol, {}),
            "series": [{
                "ts": s["ts"], "price": s["price"], "rsi": s["rsi"],
                "macd": s["macd"], "volatility": s["volatility"],
                "trend_slope": s["trend_slope"], "spread_bps": s["spread_bps"],
                "imbalance": s["imbalance"], "chop": bool(s["chop"]),
            } for s in snaps],
            "predictions": [{
                "ts": p["ts"], "direction": p["direction"],
                "confidence": p["confidence"], "entry": p["entry"],
                "stop": p["stop"], "target": p["target"],
                "condition": p["market_condition"],
            } for p in preds],
            "paper_trades": {"open": open_trades, "closed": closed[:50]},
        }

    def accuracy(self) -> Dict[str, Any]:
        outcomes = self.db.outcomes(limit=5000)
        memory = build_prediction_memory(outcomes)
        return {
            "overall_accuracy": round(memory.overall_accuracy * 100, 1),
            "total_evaluated": memory.total,
            "reliable": memory.is_reliable,
            "should_stop": memory.should_stop_trading(),
            "by_symbol": {k: {"accuracy": round(g.accuracy * 100, 1),
                              "samples": g.samples}
                          for k, g in memory.by_symbol.items()},
            "by_condition": {k: {"accuracy": round(g.accuracy * 100, 1),
                                 "samples": g.samples}
                             for k, g in memory.by_condition.items()},
            "confidence_calibration": {
                k: {"accuracy": round(g.accuracy * 100, 1),
                    "stated_confidence": round(g.mean_confidence * 100, 1),
                    "samples": g.samples}
                for k, g in memory.confidence_buckets.items()},
            "false_confidence_warnings": memory.false_confidence_warnings(),
            "best_regimes": memory.best_regimes(),
            "worst_regimes": memory.worst_regimes(),
        }

    def paper(self) -> Dict[str, Any]:
        closed = self.db.closed_paper_trades(limit=5000)
        open_trades = self.db.open_paper_trades()
        prices = {s["symbol"]: s.get("price")
                  for s in self.db.latest_snapshots()}
        dd = _drawdown(closed)
        wins = [t for t in closed if (t.get("pnl") or 0) > 0]
        best = max(closed, key=lambda t: t.get("pnl") or 0, default=None)
        worst = min(closed, key=lambda t: t.get("pnl") or 0, default=None)

        def _invested(t: Dict[str, Any]) -> float:
            """Money put into a position = quantity * entry price."""
            return round((t.get("quantity") or 0.0)
                         * (t.get("entry_price") or 0.0), 2)

        # Open positions: add the amount invested and a live mark-to-market
        # unrealized PnL against the latest observed price.
        open_rows: List[Dict[str, Any]] = []
        for t in open_trades:
            row = dict(t)
            row["invested"] = _invested(t)
            cur = prices.get(t.get("symbol"))
            row["current_price"] = cur
            if cur:
                sign = 1.0 if t.get("side") == "buy" else -1.0
                row["unrealized_pnl"] = round(
                    sign * (t.get("quantity") or 0.0)
                    * (cur - (t.get("entry_price") or 0.0)), 2)
            else:
                row["unrealized_pnl"] = None
            open_rows.append(row)

        closed_rows: List[Dict[str, Any]] = []
        for t in closed[:100]:
            row = dict(t)
            row["invested"] = _invested(t)
            closed_rows.append(row)

        return {
            "equity": dd["equity"],
            "starting_cash": _STARTING_CASH,
            "total_pnl": round(dd["equity"] - _STARTING_CASH, 2),
            "max_drawdown_pct": dd["max_drawdown_pct"],
            "invested_now": round(sum(r["invested"] for r in open_rows), 2),
            "open_positions": open_rows,
            "closed_trades": closed_rows,
            "closed_count": len(closed),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "total_fees": round(sum(t.get("fees") or 0 for t in closed), 2),
            "total_slippage": round(sum(t.get("slippage") or 0 for t in closed), 2),
            "best_trade": best,
            "worst_trade": worst,
        }

    def risk(self) -> Dict[str, Any]:
        health = self.db.latest_symbol_health()
        memory = build_prediction_memory(self.db.outcomes(limit=5000))
        hazardous = [h for h in health
                     if h.get("status") in ("PANIC", "TOO ILLIQUID",
                                             "TOO CHOPPY")]
        return {
            "errors": self.db.recent_errors(limit=40),
            "illiquid_symbols": [h["symbol"] for h in health
                                 if h.get("status") == "TOO ILLIQUID"],
            "panic_symbols": [h["symbol"] for h in health
                              if h.get("status") == "PANIC"],
            "choppy_symbols": [h["symbol"] for h in health
                               if h.get("status") == "TOO CHOPPY"],
            "skipped": [{"symbol": h["symbol"], "status": h["status"],
                         "reasons": _json_field(h.get("rejections")) or []}
                        for h in hazardous],
            "false_confidence_warnings": memory.false_confidence_warnings(),
            "model_unreliable": not memory.is_reliable,
            "should_stop_trading": memory.should_stop_trading(),
        }

    def safety_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        return [{"ts": s["ts"], "score": s["score"], "status": s["status"],
                 "condition": s["condition"]}
                for s in self.db.safety_score_history(limit)]

    def equity_history(self) -> Dict[str, Any]:
        """Accumulated paper-equity curve — the visual 'gain over time'."""
        curve = self.db.equity_curve(limit=3000)
        points = [{
            "ts": r["ts"], "equity": r["equity"],
            "gain": round((r["equity"] or _STARTING_CASH) - _STARTING_CASH, 2),
            "gain_pct": round(((r["equity"] or _STARTING_CASH)
                               / _STARTING_CASH - 1) * 100, 3),
            "drawdown_pct": round((r.get("drawdown_pct") or 0.0) * 100, 2),
        } for r in curve]
        current = curve[-1]["equity"] if curve else _STARTING_CASH
        peak = max((p["equity"] for p in points), default=_STARTING_CASH)
        return {
            "starting_cash": _STARTING_CASH,
            "current_equity": round(current, 2),
            "peak_equity": round(peak, 2),
            "total_gain": round(current - _STARTING_CASH, 2),
            "total_gain_pct": round((current / _STARTING_CASH - 1) * 100, 2),
            "points": points,
        }

    # -- learning observatory pages -----------------------------------------

    def progress(self) -> Dict[str, Any]:
        """30-day learning progress: day, phase, cycles, uptime, day timeline."""
        state = _read_json(_LEARNING_STATE)
        session = self.db.current_learning_session() or {}
        timeline = [
            {"day": m.get("day_number"), "date": m.get("day_date"),
             "status": m.get("status", "yellow"),
             "uptime_pct": m.get("uptime_pct"), "accuracy": m.get("accuracy"),
             "cycles": m.get("cycles")}
            for m in self.db.daily_metrics()
        ]
        return {
            "active": bool(state) and not state.get("complete", False),
            "current_day": state.get("current_day", 0),
            "target_days": state.get("target_days",
                                     session.get("target_days", 30)),
            "days_remaining": state.get("days_remaining"),
            "progress_pct": state.get("progress_pct", 0.0),
            "cycles_completed": state.get("cycles_completed", 0),
            "expected_cycles": state.get("expected_cycles", 0),
            "total_expected_cycles": state.get("total_expected_cycles", 0),
            "predictions_made": state.get("predictions_made", 0),
            "predictions_evaluated": state.get("predictions_evaluated", 0),
            "fake_trades": state.get("fake_trades", 0),
            "skipped_trades": state.get("skipped_trades", 0),
            "uptime_pct": state.get("uptime_pct", 0.0),
            "current_phase": state.get("current_phase", "not started"),
            "status": state.get("status", "NOT STARTED"),
            "symbols_monitored": state.get("symbols_monitored", 0),
            "day_timeline": timeline,
        }

    def status(self) -> Dict[str, Any]:
        """The 'what is it doing right now' panel."""
        now = _read_json(_NOW_PATH)
        run = self.db.current_bot_run() or {}
        state = _read_json(_LEARNING_STATE)
        return {
            "bot_running": self._observer_live(run),
            "bot_status": run.get("status", "never started"),
            "cycle": now.get("cycle", run.get("cycles", 0)),
            "current_step": now.get("current_step", "idle"),
            "current_symbol": now.get("current_symbol", ""),
            "started_at": now.get("started_at"),
            "next_cycle_at": now.get("next_cycle_at"),
            "errors_this_cycle": now.get("errors_this_cycle", 0),
            "steps": now.get("steps", []),
            "symbol_index": now.get("symbol_index", 0),
            "symbol_total": now.get("symbol_total", 0),
            "updated_at": now.get("updated_at"),
            "phase": state.get("current_phase", "—"),
            "learning_status": state.get("status", "—"),
            "data_source": now.get("data_source", "simulated"),
        }

    def logs(self, lines: int = 200) -> Dict[str, Any]:
        """Tail of the observer's log file — the live 'terminal' view."""
        n = max(1, min(int(lines), 1000))
        return {
            "file": "logs/daytrade.log",
            "exists": _LOG_FILE.exists(),
            "lines": _tail(_LOG_FILE, n),
        }

    def price_chart(self, symbol: str = "BTCUSDT",
                    range_key: str = "1D") -> Dict[str, Any]:
        """Real Binance price history for ``symbol`` over the given range.

        Read-only public market data (no key, no account) — the same source
        the bot observes. Cached briefly so range toggles stay snappy.
        """
        symbol = (symbol or "BTCUSDT").upper().strip()
        range_key = range_key if range_key in _CHART_RANGES else "1D"
        interval, limit = _CHART_RANGES[range_key]

        cache_key = f"{symbol}:{range_key}"
        cached = _CHART_CACHE.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < _CHART_TTL:
            return cached[1]

        try:
            with httpx.Client(base_url="https://data-api.binance.vision",
                              timeout=10.0) as client:
                resp = client.get("/api/v3/klines", params={
                    "symbol": symbol, "interval": interval, "limit": limit})
                resp.raise_for_status()
                klines = resp.json()
        except Exception as exc:  # noqa: BLE001 - a fetch failure is not fatal
            return {"symbol": symbol, "range": range_key, "points": [],
                    "error": f"could not reach Binance: {exc!r}"}

        points = [{"ts": int(k[6]), "price": float(k[4])} for k in klines]
        first = points[0]["price"] if points else 0.0
        last = points[-1]["price"] if points else 0.0
        change = ((last - first) / first * 100.0) if first else 0.0
        result = {
            "symbol": symbol, "range": range_key, "interval": interval,
            "points": points, "last": last,
            "change_pct": round(change, 2),
        }
        _CHART_CACHE[cache_key] = (now, result)
        return result

    def db_writes(self, lines: int = 300) -> Dict[str, Any]:
        """Tail of the database write-log — every row the bot writes, live."""
        n = max(1, min(int(lines), 2000))
        return {
            "file": "logs/db-writes.log",
            "exists": _DBWRITE_LOG.exists(),
            "lines": _tail(_DBWRITE_LOG, n),
        }

    def regimes(self) -> Dict[str, Any]:
        return regime_metrics(self.db.outcomes(limit=8000),
                              self.db.regime_periods(limit=3000))

    def calibration(self) -> Dict[str, Any]:
        return confidence_calibration(self.db.outcomes(limit=8000))

    def readiness(self) -> Dict[str, Any]:
        latest = self.db.latest_readiness() or {}
        return {
            "score": latest.get("score", 0.0),
            "level": latest.get("level", "NOT ENOUGH DATA"),
            "capped": bool(latest.get("capped")),
            "day_number": latest.get("day_number", 0),
            "breakdown": _json_field(latest.get("breakdown")) or {},
            "blockers": _json_field(latest.get("blockers")) or [],
            "history": [{"ts": r["ts"], "score": r["score"]}
                        for r in self.db.readiness_history(200)],
            "notice": "A high score never means 'safe to invest' — only "
                      "'strong paper performance, still not guaranteed'.",
        }

    def learning(self) -> Dict[str, Any]:
        return learning_metrics(self.db)

    def activity(self, limit: int = 60) -> List[Dict[str, Any]]:
        return self.db.recent_activity(limit)

    def gates(self) -> Dict[str, Any]:
        """Strategy-gate summary: how often each decision gate blocked a trade.

        Counts the Phase 1-4 gates from the recent activity feed so the
        dashboard can show the strategy overhaul actually working.
        """
        rows = self.db.recent_activity(limit=800)
        regime = calibration = meta = 0
        meta_status = "warming up"
        meta_detail = ""
        for r in rows:
            event = r.get("event", "") or ""
            if event.startswith("regime gate blocked"):
                regime += 1
            elif event.startswith("calibration gate blocked"):
                calibration += 1
            elif event.startswith("meta-model blocked"):
                meta += 1
            elif event == "meta-model retrained" and meta_status == "warming up":
                meta_status = "trained"
                meta_detail = r.get("detail", "") or ""
        return {
            "regime_blocks": regime,
            "calibration_blocks": calibration,
            "meta_blocks": meta,
            "meta_status": meta_status,
            "meta_detail": meta_detail,
        }

    def learning_summary(self) -> Dict[str, Any]:
        """Plain-language 'what is the bot learning' summary for the dashboard.

        Bundles the bot's accumulated lessons — prediction accuracy, which
        market regimes it understands, whether its confidence is honest, what
        the meta-model figured out, and what the gates blocked — into short,
        readable cards. Deliberately honest: small samples are flagged as
        such, and implausibly-high accuracy is called out, not celebrated.
        """
        outcomes = self.db.outcomes(limit=8000)
        memory = build_prediction_memory(outcomes)
        gates = self.gates()
        closed = self.db.closed_paper_trades(limit=5000)
        wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        prog = self.progress()
        evaluated = memory.total
        cards: List[Dict[str, Any]] = []

        # 1) Direction accuracy.
        acc = memory.overall_accuracy * 100
        if evaluated < 20:
            cards.append({"icon": "🎯", "tone": "neutral",
                          "title": "Predicting which way the price moves",
                          "items": [f"Only {evaluated} predictions checked so "
                                    "far — far too early to tell. It needs a "
                                    "few hours of data before this means "
                                    "anything."]})
        else:
            items = [f"Right {acc:.0f}% of the time across {evaluated} checked "
                     f"predictions — about {acc / 10:.1f} in every 10."]
            if acc >= 75:
                items.append("⚠ Suspiciously high for crypto — treat it with "
                             "caution until the sample is much larger.")
                tone = "warn"
            elif acc >= 53:
                items.append("Slightly better than a coin flip — a faint edge.")
                tone = "good"
            elif acc >= 47:
                items.append("Essentially a coin flip — no clear edge yet.")
                tone = "neutral"
            else:
                items.append("Worse than a coin flip so far.")
                tone = "bad"
            cards.append({"icon": "🎯", "tone": tone,
                          "title": "Predicting which way the price moves",
                          "items": items})

        # 2) Per-regime understanding.
        regimes = sorted((g for g in memory.by_condition.values()
                          if g.samples >= 8), key=lambda g: -g.accuracy)
        if regimes:
            rows = []
            for g in regimes:
                a = g.accuracy * 100
                verdict = ("has an edge here" if a >= 53 else
                           "loses here" if a < 47 else "no clear edge")
                rows.append(f"{g.label}: {a:.0f}% right over {g.samples} "
                            f"calls — {verdict}")
        else:
            rows = ["Not enough data per market type yet."]
        cards.append({"icon": "🌦", "tone": "neutral",
                      "title": "Which market conditions it understands",
                      "items": rows})

        # 3) Confidence honesty.
        rows = []
        for g in sorted(memory.confidence_buckets.values(),
                        key=lambda g: g.mean_confidence):
            if g.samples >= 5:
                rows.append(f"When it says ~{g.mean_confidence * 100:.0f}% "
                            f"sure, it is really right {g.accuracy * 100:.0f}% "
                            f"of the time ({g.samples} calls).")
        cards.append({"icon": "⚖️", "tone": "neutral",
                      "title": "Is its stated confidence honest?",
                      "items": rows or ["Not enough data yet."]})

        # 4) The meta-model.
        if gates["meta_status"] == "trained":
            cards.append({"icon": "🧠", "tone": "good",
                          "title": "The precision filter (meta-model)",
                          "items": [
                              f"Trained — {gates['meta_detail']}.",
                              "It learned that most quick long setups never "
                              "reach their target, so it now rejects the weak "
                              "ones.",
                              f"Blocked {gates['meta_blocks']} trades it judged "
                              "too unlikely to win."]})
        else:
            cards.append({"icon": "🧠", "tone": "neutral",
                          "title": "The precision filter (meta-model)",
                          "items": ["Still warming up — it trains once the bot "
                                    "has gathered enough history (first ~30 "
                                    "cycles)."]})

        # 5) What the gates blocked.
        cards.append({"icon": "🛡", "tone": "neutral",
                      "title": "Trades it stopped itself from taking",
                      "items": [
                          f"Regime gate: {gates['regime_blocks']} blocked "
                          "(weak market type)",
                          f"Calibration gate: {gates['calibration_blocks']} "
                          "blocked (overconfident signal)",
                          f"Meta-model gate: {gates['meta_blocks']} blocked "
                          "(low win chance)"]})

        # 6) Trades actually taken.
        if closed:
            wr = wins / len(closed) * 100
            items = [f"{len(closed)} paper trades closed, {wins} winners "
                     f"({wr:.0f}% win rate)."]
        else:
            items = ["No trades closed yet — it is being very selective."]
        items.append("A win rate only means something after 30-50+ trades.")
        cards.append({"icon": "📈", "tone": "neutral",
                      "title": "Paper trades it has actually taken",
                      "items": items})

        return {
            "day": prog.get("current_day"),
            "target_days": prog.get("target_days"),
            "evaluated": evaluated,
            "cards": cards,
        }

    def predictions(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.db.recent_predictions(limit=limit)

    def daily_reports(self) -> Dict[str, Any]:
        files = sorted((p.name for p in _DAILY_DIR.glob("*.md")), reverse=True) \
            if _DAILY_DIR.exists() else []
        return {"metrics": self.db.daily_metrics(), "report_files": files}

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _mini(h: Dict[str, Any]) -> Dict[str, Any]:
        return {"symbol": h.get("symbol"), "safety_score": h.get("safety_score"),
                "status": h.get("status")}

    def _latest_prediction_per_symbol(self) -> Dict[str, Dict[str, Any]]:
        preds = self.db.recent_predictions(limit=400)
        out: Dict[str, Dict[str, Any]] = {}
        for p in preds:  # recent_predictions is newest-first
            out.setdefault(p["symbol"], p)
        return out

    def _observer_live(self, run: Dict[str, Any]) -> bool:
        if run.get("status") != "running":
            return False
        hb = run.get("last_heartbeat_ts")
        if not hb:
            return False
        try:
            delta = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(hb)).total_seconds()
        except (ValueError, TypeError):
            return False
        return delta < _HEARTBEAT_STALE_SECONDS

    @staticmethod
    def _latest_cycle() -> Dict[str, Any]:
        if _LATEST_CYCLE.exists():
            try:
                return json.loads(_LATEST_CYCLE.read_text())
            except (ValueError, OSError):
                return {}
        return {}

    def close(self) -> None:
        self.db.close()
