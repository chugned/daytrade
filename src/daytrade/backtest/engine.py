"""Backtesting & simulation engine.

Replays a candle series bar by bar. At each bar the *same* analysis pipeline
the live path uses produces a decision; positions are opened, then closed when
price reaches the stop or the target. Every fill pays realistic, adverse
fees and slippage.

BACKTESTS ARE NOT REALITY. This engine is deliberately pessimistic — stop and
target hit in the same bar is resolved as the stop, fills always slip against
you — and it still cannot model competition, your own market impact at scale,
or the fact that the future is not the past. Treat every metric as an
optimistic upper bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from ..config.schema import AppConfig
from ..exchanges.mock import build_orderbook
from ..ml.model import PredictiveModel
from ..models import OHLCV, Action, BacktestMetrics, Side
from ..paper.broker import PaperBroker, TradeRecord
from ..pipeline import AnalysisPipeline
from ..risk.engine import RiskEngine
from ..runtime import get_logger

_log = get_logger("backtest")
_EPS = 1e-12


@dataclass
class _OpenTrade:
    """Bookkeeping for the position currently open in the backtest."""

    entry_price: float
    stop: float
    target: float
    quantity: float
    bar_opened: int


@dataclass
class BacktestResult:
    """A completed backtest: headline metrics plus the raw equity curve."""

    metrics: BacktestMetrics
    equity_curve: List[float] = field(default_factory=list)
    decisions: int = 0
    holds: int = 0
    # reason category -> count, for skipped (non-executed) entry opportunities
    skipped_reasons: Dict[str, int] = field(default_factory=dict)
    # closed round-trip trades, for best/worst-decision reporting
    trades: List[TradeRecord] = field(default_factory=list)


def _max_drawdown(equity: List[float]) -> float:
    """Largest peak-to-trough decline of the equity curve, as a fraction."""
    peak = -np.inf
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak)
    return max_dd


def _sharpe_like(equity: List[float]) -> float:
    """Mean/std of per-bar returns, scaled by sqrt(bars). NOT annualized."""
    if len(equity) < 3:
        return 0.0
    arr = np.asarray(equity, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    std = float(rets.std())
    if std <= _EPS:
        return 0.0
    return float(rets.mean() / std * np.sqrt(len(rets)))


class Backtester:
    """Bar-by-bar simulation over a candle series."""

    #: Bars of history fed to each analysis pass. Indicators have a finite
    #: lookback, so a bounded window keeps the backtest O(n) instead of O(n^2)
    #: without changing any indicator value.
    HISTORY_WINDOW: int = 300

    def __init__(self, config: AppConfig, model: Optional[PredictiveModel] = None) -> None:
        self.config = config
        self.pipeline = AnalysisPipeline(config, model)

    def run(self, candles: List[OHLCV]) -> BacktestResult:
        """Run the backtest and return metrics + the equity curve."""
        warmup = self.config.backtest.warmup_bars
        if len(candles) <= warmup + 10:
            raise ValueError(f"need > {warmup + 10} candles for a backtest, got {len(candles)}")

        broker = PaperBroker(self.config.paper.starting_cash, self.config.paper.base_currency)
        risk = RiskEngine(self.config.risk, self.config.paper.starting_cash)
        symbol = candles[-1].symbol

        max_hold = self.config.risk.max_hold_bars
        equity_curve: List[float] = []
        open_trade: Optional[_OpenTrade] = None
        bars_in_position = 0
        n_decisions = 0
        n_holds = 0
        skipped: Dict[str, int] = {}

        def _skip(reason: str) -> None:
            nonlocal n_holds
            n_holds += 1
            skipped[reason] = skipped.get(reason, 0) + 1

        for i in range(warmup, len(candles)):
            bar = candles[i]
            history = candles[max(0, i + 1 - self.HISTORY_WINDOW) : i + 1]
            mark = {symbol: bar.close}
            risk.observe_equity(bar.timestamp, broker.equity(mark))

            # --- manage an open position: stop / target checks first ---
            if open_trade is not None:
                bars_in_position += 1
                exit_price = self._exit_price(bar, open_trade)
                if exit_price is None and max_hold > 0 and i - open_trade.bar_opened >= max_hold:
                    # Triple-barrier vertical: time-stop a stalled position.
                    exit_price = bar.close
                if exit_price is not None:
                    liq = self._liquidity(bar.close)
                    broker.submit_market_order(
                        order_id=f"exit-{i}",
                        symbol=symbol,
                        side=Side.SELL,
                        quantity=open_trade.quantity,
                        reference_price=exit_price,
                        available_liquidity=liq,
                        risk_config=self.config.risk,
                        timestamp=bar.timestamp,
                    )
                    # Feed the closed-trade result to the risk engine so a
                    # loss arms the post-loss cooldown.
                    if broker.closed_trades:
                        risk.register_trade_close(broker.closed_trades[-1].pnl, i)
                    open_trade = None

            # --- look for a new entry only when flat ---
            if open_trade is None:
                book = self._book(symbol, bar.close)
                result = self.pipeline.analyze(history, book, reference_price=bar.close)
                decision = result.decision
                n_decisions += 1

                open_count = 1 if broker.has_position(symbol) else 0
                permission = risk.evaluate_entry(
                    broker.equity(mark), open_positions=open_count, bar_index=i
                )

                if decision.action is not Action.BUY:
                    # Categorize why analysis did not produce a BUY.
                    if result.kill_switch.active:
                        _skip("kill switch")
                    elif decision.confidence < self.config.fusion.min_confidence:
                        _skip("confidence below threshold")
                    else:
                        _skip("no directional edge")
                elif not permission.allowed:
                    _skip(f"risk control: {permission.reason.split(':')[0]}")
                elif not (decision.entry and decision.stop and decision.target):
                    _skip("missing price levels")
                else:
                    sizing = risk.size(broker.equity(mark), decision.entry, decision.stop)
                    if not sizing.is_tradeable:
                        _skip("position size too small")
                    else:
                        liq = self._liquidity(bar.close)
                        fill = broker.submit_market_order(
                            order_id=f"entry-{i}",
                            symbol=symbol,
                            side=Side.BUY,
                            quantity=sizing.quantity,
                            reference_price=decision.entry,
                            available_liquidity=liq,
                            risk_config=self.config.risk,
                            timestamp=bar.timestamp,
                        )
                        open_trade = _OpenTrade(
                            entry_price=fill.price,
                            stop=decision.stop,
                            target=decision.target,
                            quantity=fill.quantity,
                            bar_opened=i,
                        )

            equity_curve.append(broker.equity({symbol: bar.close}))

        # --- close any position still open at the end of the series ---
        last = candles[-1]
        if open_trade is not None:
            broker.submit_market_order(
                order_id="exit-final",
                symbol=symbol,
                side=Side.SELL,
                quantity=open_trade.quantity,
                reference_price=last.close,
                available_liquidity=self._liquidity(last.close),
                risk_config=self.config.risk,
                timestamp=last.timestamp,
            )
            equity_curve[-1] = broker.equity({symbol: last.close})

        metrics = self._metrics(symbol, candles, warmup, broker, equity_curve, bars_in_position)
        return BacktestResult(
            metrics=metrics,
            equity_curve=equity_curve,
            decisions=n_decisions,
            holds=n_holds,
            skipped_reasons=skipped,
            trades=broker.closed_trades,
        )

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _exit_price(bar: OHLCV, trade: _OpenTrade) -> Optional[float]:
        """Decide whether the bar triggers an exit, and at what price.

        If both stop and target are touched within the same bar, the *stop*
        is assumed to fill first — the conservative, pessimistic choice.
        """
        if bar.low <= trade.stop:
            return trade.stop
        if bar.high >= trade.target:
            return trade.target
        return None

    @staticmethod
    def _liquidity(price: float) -> float:
        """A fixed notional liquidity estimate, expressed in base units."""
        return 250_000.0 / price

    def _book(self, symbol: str, price: float):
        """Build a synthetic backtest orderbook with realistic depth.

        ``base_quantity`` is sized so total notional depth comfortably clears
        the thin-liquidity threshold — otherwise a low-priced synthetic
        instrument would look (spuriously) illiquid and trip the kill switch.
        """
        target_notional = self.config.microstructure.thin_liquidity_notional * 10.0
        base_qty = max(1.0, target_notional / (price * 6.0))
        return build_orderbook(
            symbol,
            price,
            exchange="backtest",
            depth=20,
            spread_bps=4.0,
            base_quantity=base_qty,
            jitter=0.0,
        )

    def _metrics(
        self,
        symbol: str,
        candles: List[OHLCV],
        warmup: int,
        broker: PaperBroker,
        equity_curve: List[float],
        bars_in_position: int,
    ) -> BacktestMetrics:
        trades = broker.closed_trades
        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))

        start_eq = self.config.paper.starting_cash
        end_eq = equity_curve[-1] if equity_curve else start_eq
        total_bars = len(candles) - warmup

        sharpe = _sharpe_like(equity_curve)
        warnings: List[str] = [
            "Backtests are NOT reality — metrics are an " "optimistic upper bound."
        ]
        if sharpe > self.config.backtest.sharpe_warn_threshold:
            warnings.append(
                f"Sharpe-like ratio {sharpe:.2f} exceeds "
                f"{self.config.backtest.sharpe_warn_threshold} — implausibly "
                "good; suspect overfitting or a modeling error."
            )
        if trades and len(wins) / len(trades) > 0.75:
            warnings.append(
                f"Win rate {len(wins) / len(trades):.0%} is suspiciously high "
                "for a realistic strategy."
            )
        if not trades:
            warnings.append("No trades were taken — strategy never found an edge.")

        return BacktestMetrics(
            symbol=symbol,
            start=candles[warmup].timestamp,
            end=candles[-1].timestamp,
            bars=total_bars,
            starting_equity=start_eq,
            ending_equity=max(end_eq, 0.0),
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            total_return_pct=(end_eq - start_eq) / start_eq * 100.0,
            win_rate=(len(wins) / len(trades)) if trades else 0.0,
            avg_win=(gross_win / len(wins)) if wins else 0.0,
            avg_loss=(-gross_loss / len(losses)) if losses else 0.0,
            profit_factor=(gross_win / gross_loss) if gross_loss > _EPS else 0.0,
            max_drawdown_pct=_max_drawdown(equity_curve) * 100.0,
            sharpe_like=sharpe,
            exposure_pct=(bars_in_position / total_bars) if total_bars else 0.0,
            total_fees=sum(f.fee for f in broker.fills),
            total_slippage=sum(f.slippage * f.quantity for f in broker.fills),
            warnings=warnings,
        )
