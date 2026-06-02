"""Macro context engine — selects an analyzer and guarantees a result.

The engine honours ``config.macro.source`` but never lets an optional
dependency break the pipeline: if Gemini is requested but unavailable, it
falls back to the deterministic mock analyzer and says so.
"""

from __future__ import annotations

import os
from typing import List, Optional

from ..config.schema import AppConfig
from ..models import OHLCV, MacroSignal
from ..runtime import get_logger
from .gemini import GeminiMacroAnalyzer, MacroUnavailable
from .mock import MockMacroAnalyzer

_log = get_logger("macro.engine")


class MacroEngine:
    """Produces a :class:`MacroSignal`, with graceful Gemini -> mock fallback."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._mock = MockMacroAnalyzer()
        self._gemini: Optional[GeminiMacroAnalyzer] = None

        if config.macro.source == "gemini":
            api_key = os.environ.get("GEMINI_API_KEY", "")
            try:
                self._gemini = GeminiMacroAnalyzer(
                    api_key=api_key,
                    allow_network=config.runtime.allow_network,
                )
            except MacroUnavailable as exc:
                _log.warning("Gemini unavailable (%s) — using mock macro", exc)

    def analyze(
        self,
        symbol: str,
        candles: List[OHLCV],
        scenario: Optional[str] = None,
    ) -> MacroSignal:
        """Return a macro signal for ``symbol``.

        Args:
            scenario: explicit regime override (demo / tests). When set, even
                the Gemini analyzer skips the network and uses the preset.
        """
        if self._gemini is not None:
            try:
                return self._gemini.analyze(symbol, candles, scenario)
            except MacroUnavailable as exc:
                _log.warning("Gemini analyze failed (%s) — falling back", exc)
        return self._mock.analyze(symbol, candles, scenario)
