"""The analysis pipeline — wiring every layer into one decision.

This is the runtime flow from PLAN.md, Phase 17:

    consensus -> features -> signals -> ML -> fuse -> kill switch -> decision

A single :meth:`AnalysisPipeline.analyze` call turns a slice of market data
into an explainable :class:`TradingDecision`. The demo, the paper-trading
loop and the backtester all go through this exact path — there is no
"shortcut" decision logic anywhere else.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from .config.schema import AppConfig
from .fusion import FusionEngine
from .indicators import TechnicalEngine, core
from .indicators.frame import ohlcv_to_frame
from .macro import MacroEngine
from .microstructure import MicrostructureEngine
from .ml.model import PredictiveModel
from .models import (
    OHLCV,
    ConsensusPrice,
    MacroSignal,
    MicrostructureSignal,
    MLSignal,
    OrderBookSnapshot,
    TechnicalSignal,
    TradingDecision,
)
from .models.enums import Bias, ModelKind
from .safety.killswitch import KillSwitchResult, evaluate_kill_switch


@dataclass(frozen=True)
class PipelineResult:
    """Everything produced by one analysis pass — decision plus provenance."""

    decision: TradingDecision
    technical: TechnicalSignal
    microstructure: MicrostructureSignal
    macro: MacroSignal
    ml: MLSignal
    kill_switch: KillSwitchResult
    reference_price: float
    atr: Optional[float]
    consensus: Optional[ConsensusPrice] = None


class AnalysisPipeline:
    """Orchestrates all analysis layers into a trading decision."""

    def __init__(
        self,
        config: AppConfig,
        model: Optional[PredictiveModel] = None,
    ) -> None:
        self.config = config
        self.technical_engine = TechnicalEngine(config.indicators)
        self.microstructure_engine = MicrostructureEngine(config.microstructure)
        self.macro_engine = MacroEngine(config)
        self.fusion_engine = FusionEngine(config.fusion)
        self.model = model

    def _atr(self, candles: List[OHLCV]) -> Optional[float]:
        """Latest ATR(14) over the candle history, or None if undefined."""
        if len(candles) < 15:
            return None
        frame = ohlcv_to_frame(candles)
        atr_series = core.atr(frame["high"], frame["low"], frame["close"], 14)
        atr_series = atr_series.dropna()
        if atr_series.empty:
            return None
        value = float(atr_series.iloc[-1])
        return value if math.isfinite(value) and value > 0 else None

    def _ml_signal(self, candles: List[OHLCV]) -> MLSignal:
        if self.model is not None:
            return self.model.predict_signal(candles, self.config)
        # No model -> an honest neutral, zero-confidence signal.
        return MLSignal(
            symbol=candles[-1].symbol,
            timestamp=candles[-1].timestamp,
            bias=Bias.NEUTRAL,
            score=0.0,
            confidence=0.0,
            reasoning=["No ML model loaded — neutral signal"],
            prob_up=0.5,
            prob_down=0.5,
            model_kind=ModelKind(self.config.ml.model_kind).value,
            model_version="none",
            feature_count=0,
        )

    def analyze(
        self,
        candles: List[OHLCV],
        orderbook: OrderBookSnapshot,
        reference_price: float,
        macro_scenario: Optional[str] = None,
        consensus: Optional[ConsensusPrice] = None,
    ) -> PipelineResult:
        """Run the full pipeline and return a :class:`PipelineResult`.

        Args:
            reference_price: consensus/market price to anchor the decision.
            macro_scenario: optional explicit macro regime (demo / tests).
        """
        if not candles:
            raise ValueError("analyze requires at least one candle")
        symbol = candles[-1].symbol
        timestamp = candles[-1].timestamp

        technical = self.technical_engine.compute(candles)
        microstructure = self.microstructure_engine.compute(orderbook, candles)
        macro = self.macro_engine.analyze(symbol, candles, macro_scenario)
        ml = self._ml_signal(candles)

        kill_switch = evaluate_kill_switch(macro, microstructure, self.config.killswitch)
        atr = self._atr(candles)

        decision = self.fusion_engine.decide(
            symbol=symbol,
            timestamp=timestamp,
            technical=technical,
            microstructure=microstructure,
            macro=macro,
            ml=ml,
            reference_price=reference_price,
            kill_switch=kill_switch,
            atr=atr,
        )

        return PipelineResult(
            decision=decision,
            technical=technical,
            microstructure=microstructure,
            macro=macro,
            ml=ml,
            kill_switch=kill_switch,
            reference_price=reference_price,
            atr=atr,
            consensus=consensus,
        )
