"""Optional Gemini-backed macro analyzer.

Used only when ``macro.source == "gemini"``, ``GEMINI_API_KEY`` is set and
``runtime.allow_network`` is true. It asks Gemini to classify the current
regime into one of the canonical scenario labels; the numeric bias/score/risk
still come from the audited :mod:`scenarios` table, so an LLM hallucination
cannot inject arbitrary numbers into the pipeline.

Any failure raises :class:`MacroUnavailable`; the engine then falls back to
the deterministic mock analyzer.
"""

from __future__ import annotations

import json
from typing import List

import httpx

from ..models import OHLCV, MacroSignal
from ..runtime import get_logger
from .scenarios import SCENARIOS, get_scenario

_log = get_logger("macro.gemini")

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/" "gemini-2.0-flash:generateContent"
)


class MacroUnavailable(RuntimeError):
    """Raised when the Gemini analyzer cannot produce a result."""


class GeminiMacroAnalyzer:
    """Macro analyzer that classifies the regime via the Gemini API."""

    source = "gemini"

    def __init__(self, api_key: str, allow_network: bool = False, timeout: float = 10.0) -> None:
        if not api_key:
            raise MacroUnavailable("no GEMINI_API_KEY configured")
        self._api_key = api_key
        self._allow_network = allow_network
        self._timeout = timeout

    def analyze(
        self, symbol: str, candles: List[OHLCV], scenario: str | None = None
    ) -> MacroSignal:
        if scenario is not None:
            # An explicit scenario bypasses the LLM entirely.
            sc = get_scenario(scenario)
        else:
            if not self._allow_network:
                raise MacroUnavailable("network disabled — cannot reach Gemini")
            label = self._classify(symbol, candles)
            sc = get_scenario(label)

        ts = candles[-1].timestamp if candles else 0
        return MacroSignal(
            symbol=symbol,
            timestamp=ts,
            bias=sc.bias,
            score=sc.score,
            confidence=sc.confidence,
            risk_level=sc.risk_level,
            regime_label=sc.label,
            source=self.source,
            headline=sc.headline,
            reasoning=[
                f"Macro regime: {sc.label} (classified by Gemini)",
                sc.headline,
                f"Risk level: {sc.risk_level.value}",
            ],
        )

    def _classify(self, symbol: str, candles: List[OHLCV]) -> str:
        labels = ", ".join(sorted(SCENARIOS))
        recent = candles[-30:]
        closes = [round(c.close, 2) for c in recent]
        prompt = (
            "You are a macro market-regime classifier. Given recent closing "
            f"prices for {symbol}: {closes}\n"
            f"Classify the current macro regime as exactly one of: {labels}.\n"
            'Respond ONLY with JSON: {"regime": "<label>"}'
        )
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(_ENDPOINT, params={"key": self._api_key}, json=body)
                resp.raise_for_status()
                data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            label = json.loads(_extract_json(text))["regime"]
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            raise MacroUnavailable(f"Gemini request failed: {exc}") from exc
        if label not in SCENARIOS:
            _log.warning("Gemini returned unknown regime %r — using neutral", label)
            return "neutral"
        return label


def _extract_json(text: str) -> str:
    """Pull the first {...} block out of an LLM response."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in Gemini response")
    return text[start : end + 1]
