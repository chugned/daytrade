"""Deterministic mock macro analyzer.

No network, no LLM — given the same inputs it always returns the same macro
read. It works two ways:

* **explicit scenario** — caller names a regime (used by the demo and tests);
* **derived** — the analyzer infers a regime from recent price action
  (trend strength + realized volatility), so it still behaves like an
  "analyst" when no scenario is supplied.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..indicators import core
from ..indicators.frame import ohlcv_to_frame
from ..models import OHLCV, MacroSignal
from .scenarios import MacroScenario, get_scenario


class MockMacroAnalyzer:
    """Deterministic macro analyzer."""

    source = "mock"

    def analyze(
        self,
        symbol: str,
        candles: List[OHLCV],
        scenario: Optional[str] = None,
    ) -> MacroSignal:
        """Produce a macro signal.

        Args:
            scenario: optional explicit regime name (see ``macro.scenarios``).
                When omitted the regime is derived from ``candles``.
        """
        ts = candles[-1].timestamp if candles else None
        if scenario is not None:
            sc = get_scenario(scenario)
            return self._to_signal(symbol, ts, sc, derived=False)

        sc = self._derive(candles)
        return self._to_signal(symbol, ts, sc, derived=True)

    def _derive(self, candles: List[OHLCV]) -> MacroScenario:
        """Infer a macro scenario from recent price behaviour."""
        if not candles or len(candles) < 30:
            return get_scenario("neutral")
        frame = ohlcv_to_frame(candles)
        close = frame["close"]
        window = min(60, len(close) - 1)
        trailing_return = float(close.iloc[-1] / close.iloc[-window] - 1.0)
        vol = core.volatility(close, min(20, window)).dropna()
        realized_vol = float(vol.iloc[-1]) if not vol.empty else 0.0

        # High volatility dominates: it signals stress regardless of direction.
        if realized_vol > 0.020:
            return get_scenario("panic" if trailing_return < 0 else "risk_off")
        if trailing_return > 0.05 and realized_vol < 0.010:
            return get_scenario("institutional_buying")
        if trailing_return > 0.015:
            return get_scenario("risk_on")
        if trailing_return < -0.05:
            return get_scenario("risk_off")
        return get_scenario("neutral")

    def _to_signal(self, symbol: str, ts, sc: MacroScenario, derived: bool) -> MacroSignal:
        how = "derived from price action" if derived else "explicit scenario"
        return MacroSignal(
            symbol=symbol,
            timestamp=ts if ts is not None else 0,
            bias=sc.bias,
            score=float(np.clip(sc.score, -1.0, 1.0)),
            confidence=sc.confidence,
            reasoning=[
                f"Macro regime: {sc.label} ({how})",
                sc.headline,
                f"Risk level: {sc.risk_level.value}",
            ],
            risk_level=sc.risk_level,
            regime_label=sc.label,
            source=self.source,
            headline=sc.headline,
        )
