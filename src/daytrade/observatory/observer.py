"""The continuous Market Safety Observer.

Runs forever (until Ctrl+C). Each cycle it, for every healthy watchlist
symbol: fetches data, runs the full analysis pipeline, scores market safety,
records a prediction, steps the paper-trading simulation, and evaluates older
predictions against what actually happened. Everything is written to the
SQLite database and the log file.

It is built to survive: a per-cycle exception is logged and the loop
continues; signals trigger a graceful shutdown; all state lives in the
database, so a restart resumes cleanly (open paper positions are reloaded).

This is observation and paper simulation only — no real orders, ever.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..config.schema import AppConfig
from ..exchanges.base import ExchangeError
from ..ml.meta import MetaLabelModel
from ..models import Action
from ..pipeline import AnalysisPipeline
from ..risk import RiskEngine, position_size
from ..runtime import add_file_logging, get_logger
from ..watchlist import WatchlistScreener, extract_metrics
from .alerts import LEVEL_CRITICAL, Alert, AlertManager, build_condition_alerts
from .calibration import ConfidenceCalibrator
from .daily_report import write_daily_report
from .database import ObservatoryDB
from .feed import LiveMockFeed
from .funding import extreme_funding_blocks_buy, fetch_current_funding_rate
from .metrics import roll_up_day
from .multi_timeframe import check_higher_tf_alignment
from .prediction_tracker import build_prediction_memory, evaluate_prediction
from .readiness import ReadinessInputs, compute_readiness
from .regime_gate import evaluate_regime_gate
from .safety_score import SafetyInputs, aggregate_safety, compute_safety_score

_log = get_logger("observatory.observer")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOG_FILE = _REPO_ROOT / "logs" / "daytrade.log"
_OBSERVER_REPORTS = _REPO_ROOT / "reports" / "observer"
_NOW_PATH = _REPO_ROOT / "data" / "now.json"

# The 10 steps of one observation cycle (for the dashboard "Now" panel).
CYCLE_STEPS = [
    "Fetching market data",
    "Validating liquidity",
    "Running technical analysis",
    "Running orderbook analysis",
    "Running macro/regime analysis",
    "Creating prediction",
    "Simulating trade",
    "Updating outcomes",
    "Saving metrics",
    "Waiting for next cycle",
]


@dataclass
class CycleSummary:
    """A one-line-per-cycle summary of what the observer did."""

    cycle: int
    timestamp: str
    symbols_observed: int = 0
    tradeable: int = 0
    predictions_made: int = 0
    predictions_evaluated: int = 0
    open_trades: int = 0
    closed_this_cycle: int = 0
    global_score: float = 50.0
    global_status: str = "WAIT"
    global_condition: str = "MIXED"
    equity: float = 0.0
    drawdown_pct: float = 0.0
    recent_accuracy: Optional[float] = None
    alerts: List[str] = field(default_factory=list)


class Observer:
    """The continuous observatory engine."""

    def __init__(
        self,
        config: AppConfig,
        watchlist_config,
        db: Optional[ObservatoryDB] = None,
        feed: Optional[LiveMockFeed] = None,
        model=None,
        learning_session=None,
        broker=None,
    ) -> None:
        self.config = config
        self.watchlist_config = watchlist_config
        self.db = db or ObservatoryDB()
        self.feed = feed or LiveMockFeed()
        self.pipeline = AnalysisPipeline(config, model)
        self.screener = WatchlistScreener(watchlist_config)
        self.alerts = AlertManager(db=self.db, allow_network=config.runtime.allow_network)
        # Optional 30-day learning session (set by `daytrade learn`).
        self.learning_session = learning_session
        # Trading broker — paper by default (writes straight to DB,
        # behaviour identical to pre-refactor). Pass a LiveBrokerAdapter
        # to route through a real exchange. The Observer doesn't care
        # which it is.
        if broker is None:
            from .trading_broker import DBPaperBroker

            broker = DBPaperBroker(
                self.db,
                fee_bps=self.config.risk.fee_bps,
                slippage_rate=0.0004,
            )
        self._broker = broker

        self._run_id: Optional[int] = None
        self._cycle = 0
        self._stop = False
        self._interval = 300
        self._last_day: Optional[str] = None
        self._starting_cash = config.paper.starting_cash
        # symbol -> open paper position {trade_id, qty, entry, stop, target}
        self._open: Dict[str, Dict[str, float]] = {}
        self._risk = RiskEngine(config.risk, self._starting_cash)
        # QA-HIGH-2: seed peak from the DB so drawdown reports survive
        # a restart honestly. Falls back to starting_cash when no
        # history exists (fresh bot) OR when the DB doesn't expose the
        # method (older schemas, unit-test MagicMock fakes).
        try:
            historic_peak = float(self.db.historical_peak_equity())
        except (AttributeError, TypeError, ValueError, Exception):  # noqa: BLE001
            historic_peak = 0.0
        self._peak_equity = max(self._starting_cash, historic_peak)
        # Push notifier — picks Telegram / ntfy / log based on env vars.
        from ..ops import Level as _NotifyLevel
        from ..ops import build_notifier

        self._notify = build_notifier()
        self._NotifyLevel = _NotifyLevel
        # Meta-labelling model (Phase 4): a precision filter on BUY signals,
        # retrained periodically on pooled triple-barrier outcomes.
        self._meta = MetaLabelModel()
        self._meta_retrain_every = 30
        self._meta_candles: Dict[str, list] = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Begin a run: recover from any prior crash, register this run."""
        add_file_logging(str(_LOG_FILE))
        _OBSERVER_REPORTS.mkdir(parents=True, exist_ok=True)
        crashed = self.db.mark_dangling_runs_crashed()
        if crashed:
            _log.warning("recovered %d crashed/abandoned prior run(s)", crashed)
        # Startup reconciliation — verify the local DB state is consistent
        # before the bot starts acting on it. Anomalies are logged loudly so
        # they don't pass silently into another run.
        from ..ops import reconcile_paper_state

        report = reconcile_paper_state(self.db)
        if not report.ok:
            _log.warning("startup reconciliation FAILED: %s", report.summary())
            for issue in report.anomalies:
                _log.warning("  reconciliation anomaly: %s", issue)
        else:
            _log.info("startup reconciliation OK: %s", report.summary())
        self._run_id = self.db.start_bot_run(pid=os.getpid())
        self._reload_open_positions()
        _log.info(
            "observer run #%d started (pid=%d), %d open position(s) reloaded",
            self._run_id,
            os.getpid(),
            len(self._open),
        )

    def stop(self, status: str = "stopped") -> None:
        if self._run_id is not None:
            self.db.stop_bot_run(self._run_id, status)
        _log.info("observer run #%d %s after %d cycle(s)", self._run_id, status, self._cycle)

    def _reload_open_positions(self) -> None:
        """Restart-safety: re-adopt paper positions left open by a prior run."""
        for trade in self.db.open_paper_trades():
            self._open[trade["symbol"]] = {
                "trade_id": trade["id"],
                "qty": trade["quantity"],
                "entry": trade["entry_price"],
                "stop": trade["stop"],
                "target": trade["target"],
            }

    # -- the cycle -----------------------------------------------------------

    #: Max workers for the per-cycle feed-warmer pool. Hard-capped at
    #: 4 per CLAUDE.md's anti-pattern rules (never spawn N OS threads
    #: per ticker — the host's thread budget is finite). 4 workers ×
    #: 12 symbols × 3 endpoints = 9 sequential rounds per worker
    #: instead of 36, ~4x speedup on the network portion of a cycle.
    _FEED_WARM_WORKERS = 4

    def _warm_feed_parallel(self, symbols: List[str], now: datetime) -> None:
        """Pre-fetch (candles, orderbook, tick) for every symbol in
        parallel so the sequential analysis loop hits the warm cache.
        Best-effort — individual symbol failures are logged but don't
        stop the cycle (the per-symbol analysis loop will hit the same
        failure with proper error handling)."""
        if not symbols or len(symbols) <= 1:
            return  # nothing to parallelise

        def _warm_one(symbol: str) -> None:
            # Each call populates the feed's TTL cache. We swallow
            # exceptions because the real per-symbol error handling
            # happens inside _observe_symbol — this is just warming.
            try:
                self.feed.candles_at(symbol, now, n_bars=240)
                self.feed.orderbook_at(symbol, now)
                self.feed.tick_at(symbol, now)
            except Exception:  # noqa: BLE001
                pass

        workers = min(self._FEED_WARM_WORKERS, len(symbols))
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="feed-warm"
        ) as exe:
            # list() forces completion before we return
            list(exe.map(_warm_one, symbols))

    def run_once(self, now: Optional[datetime] = None) -> CycleSummary:
        """Execute exactly one observation cycle and return its summary."""
        now = now or datetime.now(timezone.utc)
        self._cycle += 1
        self._errors_this_cycle = 0
        summary = CycleSummary(cycle=self._cycle, timestamp=now.isoformat())
        self._set_now("Fetching market data", "", now)
        self._activity(f"cycle {self._cycle} started", level="info")

        memory = build_prediction_memory(self.db.outcomes(limit=500))
        recent_accuracy = memory.overall_accuracy if memory.total >= 10 else None
        summary.recent_accuracy = recent_accuracy

        # Calibrate stated confidence against the strategy's own track record.
        calibrator = ConfidenceCalibrator.fit(
            (o.get("confidence"), o.get("directionally_correct"))
            for o in self.db.outcomes(limit=3000)
        )

        equity = self._equity(now)
        self._risk.observe_equity(now, equity)

        assessments = []
        illiquid: List[str] = []
        watch = self.watchlist_config.symbols

        # SPEED: warm the feed cache in parallel before the sequential
        # per-symbol analysis loop. Each _observe_symbol then reads
        # from the cache for free (no network) instead of doing 3
        # serial HTTP round-trips. ~4x speedup on cycles dominated by
        # the network portion (10+ symbols × ~hundreds of ms each).
        #
        # Workers capped at 4 per the project's anti-pattern doc
        # (CLAUDE.md "DO NOT reintroduce parallel-thread fetch over
        # the symbol universe" — except with a hard small cap, which
        # is what this is). httpx.Client + the feed's cache lock are
        # both thread-safe.
        self._warm_feed_parallel(watch, now)

        for idx, symbol in enumerate(watch, start=1):
            self._set_now(
                "Running technical analysis", symbol, now, symbol_index=idx, symbol_total=len(watch)
            )
            try:
                assessment = self._observe_symbol(
                    symbol, now, memory, recent_accuracy, equity, calibrator
                )
            except Exception as exc:  # noqa: BLE001 - one symbol must not kill the cycle
                self.db.insert_error(f"observe:{symbol}", repr(exc))
                self._errors_this_cycle += 1
                _log.exception("error observing %s", symbol)
                continue
            summary.symbols_observed += 1
            if assessment is not None:
                assessments.append(assessment)
                summary.tradeable += 1
                if assessment.condition == "ILLIQUID":
                    illiquid.append(symbol)
            summary.predictions_made += 1

        # Periodically retrain the meta-labelling model on pooled history.
        self._maybe_retrain_meta()

        # Evaluate matured predictions against reality.
        self._set_now("Updating outcomes", "", now)
        summary.predictions_evaluated = self._evaluate_predictions(now)
        if summary.predictions_evaluated:
            self._activity(
                f"{summary.predictions_evaluated} prediction(s) " "evaluated against reality",
                level="info",
            )

        # Manage open paper positions (stop / target exits).
        summary.closed_this_cycle = self._manage_positions(now)
        summary.open_trades = len(self._open)

        equity = self._equity(now)
        summary.equity = round(equity, 2)
        self._peak_equity = max(self._peak_equity, equity)
        drawdown = (
            (self._peak_equity - equity) / self._peak_equity if self._peak_equity > 0 else 0.0
        )
        summary.drawdown_pct = round(drawdown, 4)

        # Global safety score.
        self._set_now("Saving metrics", "", now)
        global_assessment = aggregate_safety(assessments)
        summary.global_score = global_assessment.score
        summary.global_status = global_assessment.status
        summary.global_condition = global_assessment.condition
        self.db.insert_safety_score(
            ts=now.isoformat(),
            score=global_assessment.score,
            status=global_assessment.status,
            condition=global_assessment.condition,
            reasons=global_assessment.reasons,
            breakdown=global_assessment.breakdown,
            equity=round(equity, 2),
            drawdown_pct=round(drawdown, 4),
        )
        # Per-cycle regime record (drives the regime timeline).
        day_no = self.learning_session.day_number(now) if self.learning_session else 0
        self.db.insert_regime_period(
            ts=now.isoformat(),
            day_number=day_no,
            condition=global_assessment.condition,
            regime=global_assessment.condition.lower(),
            safety_score=global_assessment.score,
        )

        # Alerts.
        alerts = build_condition_alerts(
            global_condition=global_assessment.condition,
            illiquid_symbols=illiquid,
            paper_drawdown_pct=drawdown,
            max_drawdown_pct=self.config.risk.max_daily_loss_pct,
            recent_accuracy=recent_accuracy,
            now=now,
        )
        for alert in alerts:
            if self.alerts.emit(alert):
                summary.alerts.append(f"{alert.kind}: {alert.message}")
                self.db.insert_alert(alert.level, alert.kind, alert.message)
                self._activity(f"alert: {alert.message}", level=alert.level)

        # Learning-session bookkeeping: progress, readiness, day rollover.
        if self.learning_session is not None:
            self._learning_cycle(now, summary, drawdown)

        # Heartbeat + per-cycle report artifact.
        if self._run_id is not None:
            self.db.heartbeat(self._run_id, self._cycle)
        self._write_cycle_report(summary)
        self._set_now("Waiting for next cycle", "", now, done=True)
        _log.info(
            "cycle %d: score=%.0f %s/%s | %d tradeable | equity=%.0f " "dd=%.1f%%",
            summary.cycle,
            summary.global_score,
            summary.global_status,
            summary.global_condition,
            summary.tradeable,
            summary.equity,
            summary.drawdown_pct * 100,
        )
        return summary

    def _observe_symbol(
        self,
        symbol: str,
        now: datetime,
        memory,
        recent_accuracy: Optional[float],
        equity: float,
        calibrator: Optional[ConfidenceCalibrator] = None,
    ):
        """Observe one symbol: analyse, score, record, maybe paper-trade."""
        candles = self.feed.candles_at(symbol, now, n_bars=240)
        orderbook = self.feed.orderbook_at(symbol, now)
        tick = self.feed.tick_at(symbol, now)
        # Staleness guard — never base a decision on data older than the
        # configured floor. Returns early so the symbol simply gets no
        # action this cycle (no prediction, no trade).
        age = (now - candles[-1].timestamp).total_seconds()
        max_age = self.config.runtime.max_data_age_seconds
        if age > max_age:
            self._activity(
                f"skipped {symbol}", f"stale data: latest candle {age:.0f}s old > " f"{max_age}s"
            )
            return None
        price = candles[-1].close
        # Keep this symbol's candles for the next meta-model retrain.
        self._meta_candles[symbol] = candles

        result = self.pipeline.analyze(candles, orderbook, reference_price=price)
        tech, micro, macro = result.technical, result.microstructure, result.macro
        decision = result.decision

        # --- watchlist health screening ---
        metrics = extract_metrics(symbol, tick, orderbook, candles)
        screening = self.screener.screen_one(symbol, tick, orderbook, candles)

        liquidity_notional = orderbook.notional_depth("bid") + orderbook.notional_depth("ask")
        panic = (
            macro.regime_label in ("panic", "war", "exchange_collapse")
            or result.kill_switch.macro_triggered
        )
        spread_bps = micro.spread_bps or 0.0
        slippage_bps = self.config.risk.base_slippage_bps + spread_bps * 0.5
        trend_strength = min(1.2, abs(tech.trend_slope or 0.0) / 0.002)
        sym_accuracy = None
        if symbol in memory.by_symbol and memory.by_symbol[symbol].samples >= 5:
            sym_accuracy = memory.by_symbol[symbol].accuracy

        safety = compute_safety_score(
            SafetyInputs(
                trend_strength=trend_strength,
                volatility=tech.volatility or 0.0,
                liquidity_notional=liquidity_notional,
                spread_bps=spread_bps,
                imbalance=micro.imbalance,
                chop=micro.chop_zone,
                slippage_estimate_bps=slippage_bps,
                panic=panic,
                recent_accuracy=sym_accuracy if sym_accuracy is not None else recent_accuracy,
                paper_drawdown_pct=0.0,
                prediction_reliability=recent_accuracy,
            )
        )

        # --- persist snapshot, prediction, symbol health ---
        # Timestamps use the OBSERVATION time so prediction outcomes can be
        # evaluated against the feed at exactly predicted_ts + horizon.
        ts = now.isoformat()
        self.db.insert_snapshot(
            ts=ts,
            symbol=symbol,
            price=price,
            rsi=tech.rsi,
            macd=tech.macd,
            volatility=tech.volatility,
            trend_slope=tech.trend_slope,
            spread_bps=spread_bps,
            imbalance=micro.imbalance,
            chop=int(micro.chop_zone),
            liquidity_notional=liquidity_notional,
            regime=micro.regime.value,
        )

        prediction_id = self.db.insert_prediction(
            ts=ts,
            symbol=symbol,
            direction=decision.action.value,
            confidence=decision.confidence,
            entry=decision.entry,
            stop=decision.stop,
            target=decision.target,
            market_condition=safety.condition,
            fused_score=decision.fused_score,
            reasons=decision.reasoning,
        )

        status = self._symbol_status(safety, screening, decision)
        self.db.insert_symbol_health(
            ts=ts,
            symbol=symbol,
            price=price,
            volume_24h=metrics.volume_24h_usd,
            spread_bps=spread_bps,
            book_notional=liquidity_notional,
            healthy=int(screening.approved),
            rejections=screening.rejections,
            recent_accuracy=sym_accuracy,
            safety_score=safety.score,
            status=status,
        )

        # --- activity feed ---
        if not screening.approved:
            reason = screening.rejections[0] if screening.rejections else "filtered"
            self._activity(f"skipped {symbol}", reason)
        elif decision.action.value != "hold":
            self._activity(
                f"prediction created for {symbol}",
                f"{decision.action.value.upper()} " f"conf {decision.confidence:.0%}",
            )
        else:
            self._activity(f"scanning {symbol}", f"condition {safety.condition}")

        # --- regime gate: only trade regimes with a proven edge ---
        regime_gate = evaluate_regime_gate(
            safety.condition,
            memory,
            self.config.gating.min_regime_accuracy,
            self.config.gating.regime_min_samples,
        )

        # --- meta-labelling gate: P(this long hits target before stop) ---
        meta_proba: Optional[float] = None
        if self._meta.is_trained:
            try:
                meta_proba = self._meta.predict_win_proba(candles, self.config)
            except Exception:  # noqa: BLE001 - a scoring failure never blocks
                meta_proba = None

        # --- paper-trading simulation step (entry only; exits in _manage) ---
        self._maybe_open_position(
            symbol,
            decision,
            screening,
            price,
            liquidity_notional,
            equity,
            now,
            prediction_id,
            regime_gate,
            calibrator,
            meta_proba,
        )
        return safety

    def _maybe_retrain_meta(self) -> None:
        """Retrain the meta-labelling model on this cycle's pooled candles.

        Runs on the first cycle and every ``_meta_retrain_every`` cycles. A
        training failure is logged but never aborts the cycle.
        """
        if not self._meta_candles:
            return
        due = self._cycle == 1 or self._cycle % self._meta_retrain_every == 0
        if not due:
            return
        try:
            result = self._meta.train(list(self._meta_candles.values()), self.config)
        except Exception as exc:  # noqa: BLE001 - training must never kill a cycle
            _log.warning("meta-model retrain failed: %s", exc)
            return
        if result is not None:
            self._activity(
                "meta-model retrained",
                f"{result.samples} samples · base win rate " f"{result.base_win_rate:.0%}",
            )

    #: Cap on per-cycle evaluations — keeps the cycle moving even when a big
    #: backlog (e.g. delisted-pair predictions that will never resolve) is
    #: pending. The backlog drains over subsequent cycles instead of blocking.
    _EVAL_PER_CYCLE = 200

    def _evaluate_predictions(self, now: datetime) -> int:
        """Score every matured-but-unevaluated prediction against reality."""
        evaluated = 0
        for prediction in self.db.unevaluated_predictions(limit=self._EVAL_PER_CYCLE):
            try:
                outcome, fully = evaluate_prediction(prediction, self.feed, now)
            except ExchangeError as exc:
                # A missing kline (data gap, delisted pair) used to retry every
                # cycle and pile up. Mark it evaluated so it falls out of the
                # queue — if Binance cannot return the kline now, it never will.
                _log.warning(
                    "skipped prediction %s — no market data, " "marking evaluated: %s",
                    prediction.get("id"),
                    exc,
                )
                try:
                    self.db.mark_prediction_evaluated(prediction["id"])
                except Exception:  # noqa: BLE001 - never let bookkeeping crash a cycle
                    pass
                continue
            if outcome is None:
                continue
            # QA-HIGH-1: single transaction so a crash between the
            # upsert and the evaluated flag does NOT cause re-work
            # next cycle.
            if fully:
                self.db.upsert_outcome_and_mark_evaluated(prediction["id"], **outcome)
            else:
                self.db.upsert_outcome(prediction["id"], **outcome)
            evaluated += 1
        return evaluated

    # -- learning session ----------------------------------------------------

    def _learning_cycle(self, now: datetime, summary: CycleSummary, drawdown: float) -> None:
        """Per-cycle learning bookkeeping: progress, readiness, day rollover."""
        session = self.learning_session
        session.cycles_completed = self._cycle
        if session.session_id is not None:
            self.db.update_learning_session(session.session_id, self._cycle)

        counts = {
            "symbols_monitored": len(self.watchlist_config.symbols),
            "predictions_made": self.db.count("predictions"),
            "predictions_evaluated": self.db.count("prediction_outcomes"),
            "fake_trades": self.db.count("paper_trades"),
            "skipped_trades": sum(
                1
                for h in self.db.latest_symbol_health()
                if h.get("status") != "GOOD PAPER CONDITIONS"
            ),
        }
        status = "PAPER TRADING" if self._open else "OBSERVING"
        session.save_state(now, counts, status)

        readiness = self._compute_readiness(now, drawdown)
        self.db.insert_readiness(
            ts=now.isoformat(),
            score=readiness.score,
            level=readiness.level,
            capped=int(readiness.capped),
            day_number=readiness.day_number,
            breakdown=readiness.breakdown,
            blockers=readiness.blockers,
        )

        # Day rollover: aggregate the previous day, write its report.
        today = now.date().isoformat()
        if self._last_day is None:
            self._last_day = today
        elif today != self._last_day:
            self._day_rollover(self._last_day, now)
            self._last_day = today

    def _compute_readiness(self, now: datetime, drawdown: float):
        """Build readiness inputs from the session + database and score them."""
        session = self.learning_session
        memory = build_prediction_memory(self.db.outcomes(limit=8000))
        regimes = {r.get("condition") for r in self.db.regime_periods(limit=8000)}
        accs = [g.accuracy for g in memory.by_condition.values() if g.samples >= 3]
        spread = (max(accs) - min(accs)) if len(accs) >= 2 else 0.0
        errors = self.db.recent_errors(limit=4000)
        api_failures = sum(
            1
            for e in errors
            if "api" in (e.get("context") or "").lower()
            or "exchange" in (e.get("context") or "").lower()
        )
        return compute_readiness(
            ReadinessInputs(
                day_number=session.day_number(now),
                target_days=session.target_days,
                predictions_evaluated=memory.total,
                uptime_pct=session.uptime_pct(now),
                max_drawdown_pct=drawdown * 100.0,
                overall_accuracy=memory.overall_accuracy,
                false_confidence_count=len(memory.false_confidence_warnings()),
                regimes_observed=len([r for r in regimes if r]),
                regime_accuracy_spread=spread,
                api_failures=api_failures,
            )
        )

    def _day_rollover(self, day_date: str, now: datetime) -> None:
        """Aggregate a completed day and write its daily report."""
        session = self.learning_session
        day_number = max(1, session.day_number(now) - 1)
        try:
            # Use the realistic cycle ceiling so a 24/7-running bot reads
            # green, not red (see learning.REALISTIC_CYCLE_SECONDS).
            from .learning import REALISTIC_CYCLE_SECONDS

            effective = max(float(session.interval_seconds), float(REALISTIC_CYCLE_SECONDS))
            metric = roll_up_day(self.db, day_date, day_number, int(86_400 / effective))
            self.db.upsert_daily_metric(day_date, **metric)
            write_daily_report(self.db, day_date)
            self._activity(
                f"daily report generated for {day_date}", f"day {day_number}", level="info"
            )
            _log.info("day %d rolled up (%s): %s", day_number, day_date, metric.get("status"))
            # QA-HIGH-3: prune old high-volume tables + checkpoint the
            # WAL so the DB doesn't grow ~16 MB/day forever. Best-effort.
            try:
                pruned = self.db.prune_old(days=30)
                total = sum(pruned.values())
                if total:
                    _log.info("pruned %d old rows: %s", total, pruned)
            except Exception as exc:  # noqa: BLE001
                _log.warning("prune_old failed: %s", exc)
        except Exception as exc:  # noqa: BLE001 - rollover must not crash the loop
            self.db.insert_error("day_rollover", repr(exc))

    def _activity(self, event: str, detail: str = "", level: str = "info") -> None:
        """Record one live-activity-feed event (best-effort)."""
        try:
            self.db.insert_activity(event, detail, level, self._cycle)
        except Exception:  # noqa: BLE001 - activity logging must never crash
            pass

    def _set_now(
        self,
        step: str,
        symbol: str,
        now: datetime,
        done: bool = False,
        symbol_index: int = 0,
        symbol_total: int = 0,
    ) -> None:
        """Write the live 'what is it doing right now' state to data/now.json.

        QA-HIGH-4: write atomically via tempfile + os.replace so a
        concurrent dashboard read never sees a truncated JSON. Before
        this fix the dashboard's `_read_json` would intermittently get
        ValueError and report 'idle' for one refresh cycle.
        """
        try:
            next_cycle = (now + timedelta(seconds=self._interval)).isoformat() if done else None
            _NOW_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {
                    "cycle": self._cycle,
                    "started_at": now.isoformat(),
                    "current_step": step,
                    "current_symbol": symbol,
                    "symbol_index": symbol_index,
                    "symbol_total": symbol_total,
                    "next_cycle_at": next_cycle,
                    "errors_this_cycle": getattr(self, "_errors_this_cycle", 0),
                    "data_source": getattr(self.feed, "source", "simulated"),
                    "steps": CYCLE_STEPS,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            tmp_path = _NOW_PATH.with_suffix(".json.tmp")
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, _NOW_PATH)
        except OSError:  # pragma: no cover
            pass

    # -- paper trading -------------------------------------------------------

    def _maybe_open_position(
        self,
        symbol: str,
        decision,
        screening,
        price: float,
        liquidity_notional: float,
        equity: float,
        now: datetime,
        prediction_id: int,
        regime_gate=None,
        calibrator: Optional[ConfidenceCalibrator] = None,
        meta_proba: Optional[float] = None,
    ) -> None:
        if symbol in self._open or not screening.approved:
            return
        if decision.action is not Action.BUY or decision.kill_switch_active:
            return
        if not (decision.entry and decision.stop and decision.target):
            return
        # Regime gate: skip a BUY in a regime with no proven edge (Phase 2).
        if regime_gate is not None and not regime_gate.allowed:
            self._activity(f"regime gate blocked {symbol}", regime_gate.reason)
            return
        # Calibration gate: skip a BUY whose calibrated win probability —
        # the strategy's stated confidence corrected against its own track
        # record — is below the floor (Phase 3).
        if calibrator is not None:
            calibrated = calibrator.calibrate(decision.confidence)
            floor = self.config.gating.min_calibrated_confidence
            if calibrated < floor:
                self._activity(
                    f"calibration gate blocked {symbol}",
                    f"calibrated win prob {calibrated:.0%} below {floor:.0%} "
                    f"(stated {decision.confidence:.0%})",
                )
                return
        # Meta-labelling gate: skip a BUY unless the precision model scores it
        # well above its own base win rate (Phase 4 — the de Prado meta-label
        # filter). The floor is RELATIVE to the base rate, so it stays
        # reachable whatever the timeframe — a fixed probability floor was
        # unreachable once the base rate fell to ~6%.
        if meta_proba is not None and self._meta.base_win_rate is not None:
            base = self._meta.base_win_rate
            floor = base * self.config.gating.meta_label_edge_multiple
            if meta_proba < floor:
                self._activity(
                    f"meta-model blocked {symbol}",
                    f"win prob {meta_proba:.0%} below {floor:.0%} " f"(base rate {base:.0%})",
                )
                return
        # Multi-timeframe alignment gate — Tier-1 of the 10x research
        # roadmap. Off by default; flip on via gating.require_higher_tf_alignment.
        if self.config.gating.require_higher_tf_alignment:
            candles_for_mtf = self._meta_candles.get(symbol)
            if candles_for_mtf:
                mtf = check_higher_tf_alignment(
                    candles_for_mtf,
                    decision.action.value,
                    min_slope=self.config.gating.higher_tf_min_slope,
                )
                if not mtf.aligned:
                    self._activity(f"higher-TF gate blocked {symbol}", mtf.reason)
                    return
        # Funding-rate gate — opt-in sentiment signal from the perp market.
        # Extreme positive funding = crowded long positions = pullback risk.
        if self.config.gating.use_funding_rate_gate:
            snap = fetch_current_funding_rate(symbol)
            if snap is not None:
                block, reason = extreme_funding_blocks_buy(
                    snap.rate,
                    extreme_positive=self.config.gating.funding_extreme_positive,
                    extreme_negative=self.config.gating.funding_extreme_negative,
                )
                if block:
                    self._activity(f"funding gate blocked {symbol}", reason)
                    return
        permission = self._risk.evaluate_entry(
            equity, open_positions=len(self._open), bar_index=self._cycle
        )
        if not permission.allowed:
            return
        sizing = position_size(equity, decision.entry, decision.stop, self.config.risk)
        if not sizing.is_tradeable:
            return
        try:
            opened = self._broker.open_long(
                symbol=symbol,
                quantity=sizing.quantity,
                entry_price=decision.entry,
                stop=decision.stop,
                target=decision.target,
                timestamp=now,
            )
        except Exception as exc:  # noqa: BLE001 - one failed trade must not
            # crash the cycle; live brokers can fail for many reasons.
            _log.warning("broker.open_long failed for %s: %s", symbol, exc)
            self._activity(f"broker open refused {symbol}", str(exc), level="warning")
            return
        self._open[symbol] = {
            "trade_id": opened.trade_id,
            "qty": opened.fill_quantity,
            "entry": opened.fill_price,
            "stop": decision.stop,
            "target": decision.target,
            "opened_cycle": self._cycle,
        }
        self._activity(
            f"paper trade opened: {symbol}",
            f"qty {opened.fill_quantity:.4f} @ " f"{opened.fill_price:.4f} (sim)",
        )
        _log.info(
            "paper-opened %s qty=%.6f entry=%.4f (sim)",
            symbol,
            opened.fill_quantity,
            opened.fill_price,
        )
        try:
            self._notify.notify(
                f"Paper trade opened — {symbol}",
                f"qty {sizing.quantity:.4f} @ {decision.entry:.4f}\n"
                f"stop {decision.stop:.4f} target {decision.target:.4f}",
                self._NotifyLevel.INFO,
            )
        except Exception:  # noqa: BLE001 - notify must never break a trade
            pass

    def _manage_positions(self, now: datetime) -> int:
        """Close any open paper position whose stop or target was reached."""
        closed = 0
        max_hold = self.config.risk.max_hold_bars
        for symbol, pos in list(self._open.items()):
            # QA-CRIT-1: a Binance hiccup in feed.price_at used to crash
            # the whole cycle, leaving the rest of the open positions
            # un-evaluated (stop-loss enforcement silently skipped).
            # Fail-soft per-symbol and move on; the next cycle retries.
            try:
                price = self.feed.price_at(symbol, now)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "price_at(%s) failed; skipping management this " "cycle: %s", symbol, exc
                )
                continue
            exit_price: Optional[float] = None
            if price <= pos["stop"]:
                exit_price = pos["stop"]
            elif price >= pos["target"]:
                exit_price = pos["target"]
            elif max_hold > 0 and self._cycle - pos.get("opened_cycle", self._cycle) >= max_hold:
                # Triple-barrier vertical: time-stop a stalled position.
                exit_price = price
            if exit_price is None:
                continue
            qty = pos["qty"]
            try:
                close_info = self._broker.close_long(
                    trade_id=pos["trade_id"],
                    symbol=symbol,
                    quantity=qty,
                    entry_price=pos["entry"],
                    exit_price=exit_price,
                    timestamp=now,
                )
            except Exception as exc:  # noqa: BLE001 — fail-closed: keep the
                # position open and surface the error rather than losing
                # track of an in-flight trade.
                _log.error("broker.close_long failed for %s: %s", symbol, exc)
                self._activity(f"broker close failed {symbol}", str(exc), level="error")
                continue
            pnl = close_info.pnl
            exit_price = close_info.fill_price  # live brokers may differ
            self._risk.register_trade_close(pnl, self._cycle)
            del self._open[symbol]
            closed += 1
            _log.info("paper-closed %s exit=%.4f pnl=%.2f (sim)", symbol, exit_price, pnl)
            try:
                lvl = self._NotifyLevel.WARN if pnl < 0 else self._NotifyLevel.INFO
                self._notify.notify(
                    f"Paper trade closed — {symbol}",
                    f"exit {exit_price:.4f} · PnL €{pnl:+.2f}",
                    lvl,
                )
            except Exception:  # noqa: BLE001
                pass
        return closed

    def _equity(self, now: datetime) -> float:
        """Simulated equity = cash + realised PnL + open unrealised PnL."""
        # QA-HIGH-6: use SUM over the full table, not a 500-row window.
        # After 500 closed trades the windowed approach silently
        # under-reported realised PnL and broke equity / drawdown.
        realized = self.db.total_realised_pnl()
        unrealized = 0.0
        for symbol, pos in self._open.items():
            # QA-CRIT-1: a Binance hiccup in feed.price_at used to crash
            # the whole cycle. Fall back to the entry price (treat the
            # position as flat for this snapshot) so equity is still
            # reportable. The next cycle will re-price normally.
            try:
                price = self.feed.price_at(symbol, now)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "price_at(%s) failed in equity calc; using " "entry as fallback: %s",
                    symbol,
                    exc,
                )
                price = pos["entry"]
            unrealized += (price - pos["entry"]) * pos["qty"]
        return self._starting_cash + realized + unrealized

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _symbol_status(safety, screening, decision) -> str:
        """Map a symbol's state to a dashboard status label."""
        if not screening.approved:
            if any("liquidity" in r or "orderbook" in r for r in screening.rejections):
                return "TOO ILLIQUID"
            return "WATCH ONLY"
        if safety.condition == "PANIC":
            return "PANIC"
        if safety.condition == "ILLIQUID":
            return "TOO ILLIQUID"
        if safety.condition == "CHOPPY":
            return "TOO CHOPPY"
        if decision.kill_switch_active:
            return "WATCH ONLY"
        if safety.score >= 65:
            return "GOOD PAPER CONDITIONS"
        return "WATCH ONLY"

    def _write_cycle_report(self, summary: CycleSummary) -> None:
        """Write the cycle summary to reports/observer/ (latest + run log)."""
        try:
            (_OBSERVER_REPORTS / "latest.json").write_text(json.dumps(asdict(summary), indent=2))
            run_log = _OBSERVER_REPORTS / f"run_{self._run_id}.jsonl"
            with run_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(summary)) + "\n")
        except OSError as exc:  # pragma: no cover - disk issues must not crash
            _log.warning("could not write cycle report: %s", exc)

    # -- the forever loop ----------------------------------------------------

    #: QA-CRIT-4: act on consecutive_failures. Without these limits a
    #: persistently-failing cycle (e.g. Binance returning 5xx for
    #: hours) would loop forever — once per cycle (default 300s) it
    #: would re-hit every endpoint, write a CRITICAL alert, and
    #: contribute to API rate-limit pressure.
    _BACKOFF_THRESHOLD = 3  # start exponential backoff after this many
    _BACKOFF_MAX_SECONDS = 1800  # 30 min cap on extra sleep per cycle
    _ABORT_THRESHOLD = 50  # after this many in a row, give up

    def run_forever(self, interval: int = 300) -> None:
        """Run cycles every ``interval`` seconds.

        Stops on a signal, or — in a learning session — once the configured
        observation window (e.g. 30 days) has fully elapsed.

        If cycles fail repeatedly, exponential backoff kicks in after
        ``_BACKOFF_THRESHOLD`` and the run aborts after
        ``_ABORT_THRESHOLD`` consecutive failures — preventing a
        permafail loop from hammering the data feed.
        """
        self._install_signal_handlers()
        self._interval = interval
        self.start()
        consecutive_failures = 0
        try:
            while not self._stop:
                if self.learning_session is not None and self.learning_session.is_complete(
                    datetime.now(timezone.utc)
                ):
                    _log.info("learning window complete — stopping observer")
                    self._learning_complete = True
                    break
                # SPEED FIX: cycle period is now ``interval`` wall-clock seconds
                # (was: ``interval`` extra sleep AFTER the work, which meant
                # the actual cycle = work_time + interval, and the bot
                # silently overshot its target cadence by the analysis time).
                cycle_started = time.monotonic()
                try:
                    self.run_once()
                    consecutive_failures = 0
                except Exception as exc:  # noqa: BLE001 - crash recovery
                    consecutive_failures += 1
                    self.db.insert_error("cycle", repr(exc))
                    _log.exception("cycle failed (%d in a row)", consecutive_failures)
                    self.alerts.emit(
                        Alert(
                            LEVEL_CRITICAL,
                            "crash",
                            f"observer cycle crashed: {exc!r}",
                            datetime.now(timezone.utc),
                        )
                    )
                    # Abort outright after sustained failure — preserves
                    # the upstream data feed from a hot loop and signals
                    # to watchdogs that this run is unhealthy.
                    if consecutive_failures >= self._ABORT_THRESHOLD:
                        _log.error(
                            "%d consecutive cycle failures — aborting run; "
                            "watchdog can restart cleanly",
                            consecutive_failures,
                        )
                        self._stop = True
                        break
                # Backoff: extra sleep on top of `interval` once we
                # cross the threshold, capped at _BACKOFF_MAX_SECONDS.
                extra_sleep = 0.0
                if consecutive_failures > self._BACKOFF_THRESHOLD:
                    n = consecutive_failures - self._BACKOFF_THRESHOLD
                    extra_sleep = min(
                        self._BACKOFF_MAX_SECONDS,
                        interval * (2 ** min(n, 10)),
                    )
                    _log.warning(
                        "backoff: sleeping an extra %.0f s " "(consecutive failures = %d)",
                        extra_sleep,
                        consecutive_failures,
                    )
                # Sleep in short slices so Ctrl+C is responsive.
                # ``total`` is computed as (target cycle period) MINUS
                # (work elapsed), so the bot hits its configured cadence
                # when work fits in interval, and runs back-to-back with
                # no sleep when work exceeds interval (overshoot is
                # logged via cycle_overshoot_seconds in db_writes).
                elapsed = time.monotonic() - cycle_started
                total = max(0.0, interval + extra_sleep - elapsed)
                if elapsed > interval and extra_sleep == 0:
                    _log.warning(
                        "cycle overshot interval: work=%.1fs interval=%ds — "
                        "consider widening --interval or reducing universe",
                        elapsed, interval,
                    )
                slept = 0.0
                while slept < total and not self._stop:
                    time.sleep(min(1.0, total - slept))
                    slept += 1.0
        finally:
            final = (
                "completed"
                if getattr(self, "_learning_complete", False)
                else ("stopped" if self._stop else "crashed")
            )
            if self.learning_session is not None and self.learning_session.session_id is not None:
                self.db.update_learning_session(
                    self.learning_session.session_id,
                    self._cycle,
                    status="completed" if final == "completed" else "stopped",
                )
            self.stop(final)
            self.db.close()

    def _install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            _log.info("signal %d received — shutting down gracefully", signum)
            self._stop = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except ValueError:  # pragma: no cover - not on main thread (tests)
                pass
