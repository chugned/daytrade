"""Tests for the cross-asset CASCADE_EXHAUSTION edge validation.

The session research found a +9.7 bp forward-return edge on SOL when the
30-min forward window followed a CASCADE_EXHAUSTION bar (n=101 events,
90-day × 1m sample). This needs OOS confirmation on other symbols and
on a fresh time window. Validation function lives at
``daytrade.research.cascade_validation``.

TDD: these tests exercise the function's contract (inputs → outputs +
shape + error handling). The empirical truth — whether the edge actually
exists — is reported by the script in ``scripts/sweep_cascade_validation.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from daytrade.models import OHLCV
from daytrade.research.cascade_validation import (
    SymbolValidation,
    validate_cascade_edge,
)


# ---------------------------------------------------------------------------
# Synthetic data helpers — deterministic, no network
# ---------------------------------------------------------------------------

def _candle(i: int, o: float, c: float, vol: float,
            h: float | None = None, l: float | None = None,
            symbol: str = "TESTUSDT") -> OHLCV:
    hi = h if h is not None else max(o, c) * 1.0005
    lo = l if l is not None else min(o, c) * 0.9995
    return OHLCV(
        symbol=symbol,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
        open=o, high=hi, low=lo, close=c, volume=vol,
    )


def _flat_then_exhaustion(symbol: str = "TESTUSDT", n_pre: int = 30,
                          n_post: int = 60) -> List[OHLCV]:
    """N quiet bars, then a CASCADE_ACTIVE bar, then a CASCADE_EXHAUSTION
    bar, then a steady rebound (so forward-return at exhaustion is +ve)."""
    out: List[OHLCV] = []
    p = 100.0
    # Quiet prefix
    for i in range(n_pre):
        nxt = p * (1.0 + (0.0002 if i % 2 == 0 else -0.0002))
        out.append(_candle(i, p, nxt, 1000.0, symbol=symbol))
        p = nxt
    # Cascade-active bar (large bearish body + volume spike)
    cascade_open = p
    cascade_close = p * 0.97
    out.append(_candle(n_pre, o=cascade_open, c=cascade_close, vol=8000.0,
                       h=cascade_open * 1.0005, l=cascade_close * 0.999,
                       symbol=symbol))
    # Exhaustion bar (long lower wick + still-elevated volume)
    px = cascade_close
    exhaustion_open = px * 1.000
    exhaustion_close = px * 1.002
    out.append(_candle(n_pre + 1, o=exhaustion_open, c=exhaustion_close,
                       vol=6000.0, h=px * 1.003, l=px * 0.980,
                       symbol=symbol))
    # Rebound — straight up to make forward return clearly positive
    p = exhaustion_close
    for j in range(n_post):
        nxt = p * 1.001  # +10 bp per bar
        out.append(_candle(n_pre + 2 + j, p, nxt, 1500.0, symbol=symbol))
        p = nxt
    return out


# ---------------------------------------------------------------------------
# Function contract
# ---------------------------------------------------------------------------

def test_returns_symbolvalidation_for_each_symbol():
    """Given candles for symbols, returns one SymbolValidation per symbol."""
    candles_by_symbol = {
        "A": _flat_then_exhaustion(symbol="A"),
        "B": _flat_then_exhaustion(symbol="B"),
    }
    results = validate_cascade_edge(candles_by_symbol, horizon_minutes=30)
    assert len(results) == 2
    syms = {r.symbol for r in results}
    assert syms == {"A", "B"}
    for r in results:
        assert isinstance(r, SymbolValidation)


def test_detects_exhaustion_event_in_synthetic_data():
    """At least one exhaustion event should be found in a fixture that
    contains a textbook cascade → exhaustion → rebound sequence."""
    candles_by_symbol = {"TEST": _flat_then_exhaustion()}
    [result] = validate_cascade_edge(candles_by_symbol, horizon_minutes=30)
    assert result.exhaustion_count >= 1


def test_exhaustion_with_rebound_has_positive_mean_return():
    """When the post-exhaustion bars genuinely rebound, mean forward
    return should be positive — confirming the function measures what
    it claims to measure."""
    candles_by_symbol = {"TEST": _flat_then_exhaustion(n_post=60)}
    [result] = validate_cascade_edge(candles_by_symbol, horizon_minutes=30)
    assert result.exhaustion_count >= 1
    assert result.mean_forward_return_bps > 0
    # Should be sizeable — 30 bars of +10bp = +300bp rebound is the floor
    assert result.mean_forward_return_bps > 50


def test_baseline_forward_return_computed_for_non_exhaustion_bars():
    """``baseline_mean_forward_return_bps`` reports the mean forward return
    across ALL bars (not just exhaustion ones) for comparison."""
    candles_by_symbol = {"TEST": _flat_then_exhaustion()}
    [result] = validate_cascade_edge(candles_by_symbol, horizon_minutes=30)
    # Baseline should be defined (we have many bars)
    assert result.baseline_mean_forward_return_bps is not None


def test_zero_events_yields_none_metrics_not_crash():
    """A symbol with no exhaustion events must return SymbolValidation
    with exhaustion_count=0 and mean=None — not raise."""
    # Pure quiet — no cascade footprint
    quiet = []
    p = 100.0
    for i in range(120):
        nxt = p * (1.0 + (0.0001 if i % 2 == 0 else -0.0001))
        quiet.append(_candle(i, p, nxt, 1000.0, symbol="Q"))
        p = nxt
    [result] = validate_cascade_edge({"Q": quiet}, horizon_minutes=30)
    assert result.exhaustion_count == 0
    assert result.mean_forward_return_bps is None


def test_horizon_minutes_changes_window():
    """A longer horizon should produce a different mean (the rebound
    integrates over more bars)."""
    candles_by_symbol = {"TEST": _flat_then_exhaustion(n_post=60)}
    [short] = validate_cascade_edge(candles_by_symbol, horizon_minutes=5)
    [long] = validate_cascade_edge(candles_by_symbol, horizon_minutes=30)
    # Both detected the same event
    assert short.exhaustion_count == long.exhaustion_count >= 1
    # 30-minute window captures more rebound than 5-minute window
    assert long.mean_forward_return_bps > short.mean_forward_return_bps


def test_rejects_empty_input():
    with pytest.raises(ValueError):
        validate_cascade_edge({}, horizon_minutes=30)


def test_rejects_invalid_horizon():
    with pytest.raises(ValueError):
        validate_cascade_edge({"X": _flat_then_exhaustion()}, horizon_minutes=0)
