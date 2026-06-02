"""AI fusion engine tests."""

from __future__ import annotations

import pytest

from daytrade.config import FusionConfig
from daytrade.fusion import FusionEngine
from daytrade.models import (
    Action,
    MacroSignal,
    MicrostructureSignal,
    MLSignal,
    RiskLevel,
    TechnicalSignal,
)
from daytrade.safety.killswitch import KillSwitchResult

TS = "2026-01-01T00:00:00Z"


def _signals(t=0.0, m=0.0, x=0.0, ml=0.0, tc=0.7, mc=0.7, xc=0.7, mlc=0.7):
    return (
        TechnicalSignal(symbol="BTC", timestamp=TS, score=t, confidence=tc),
        MicrostructureSignal(symbol="BTC", timestamp=TS, score=m, confidence=mc),
        MacroSignal(symbol="BTC", timestamp=TS, score=x, confidence=xc, risk_level=RiskLevel.LOW),
        MLSignal(symbol="BTC", timestamp=TS, score=ml, confidence=mlc),
    )


def _decide(engine, signals, ref=100.0, atr=None, kill=None):
    kill = kill or KillSwitchResult(active=False)
    return engine.decide("BTC", TS, *signals, reference_price=ref, kill_switch=kill, atr=atr)


def test_strong_bullish_signals_produce_buy():
    engine = FusionEngine()
    d = _decide(engine, _signals(t=0.8, m=0.7, x=0.8, ml=0.7))
    assert d.action is Action.BUY
    assert d.entry < d.reference_price < d.target


def test_strong_bearish_signals_produce_sell():
    engine = FusionEngine()
    d = _decide(engine, _signals(t=-0.8, m=-0.7, x=-0.8, ml=-0.7))
    assert d.action is Action.SELL
    assert d.target < d.reference_price < d.entry


def test_weak_signals_produce_hold():
    engine = FusionEngine()
    d = _decide(engine, _signals(t=0.05, m=-0.03, x=0.02, ml=0.01))
    assert d.action is Action.HOLD


def test_kill_switch_forces_hold_despite_strong_score():
    engine = FusionEngine()
    kill = KillSwitchResult(active=True, reasons=["test"])
    d = _decide(engine, _signals(t=0.9, m=0.9, x=0.9, ml=0.9), kill=kill)
    assert d.action is Action.HOLD
    assert d.kill_switch_active


def test_low_confidence_forces_hold():
    """A strong score but uniformly low confidence downgrades to HOLD."""
    cfg = FusionConfig()
    engine = FusionEngine(cfg)
    d = _decide(engine, _signals(t=0.6, m=0.6, x=0.6, ml=0.6, tc=0.05, mc=0.05, xc=0.05, mlc=0.05))
    assert d.action is Action.HOLD


def test_fused_score_in_bounds():
    engine = FusionEngine()
    d = _decide(engine, _signals(t=0.9, m=-0.9, x=0.5, ml=-0.2))
    assert -1.0 <= d.fused_score <= 1.0
    assert 0.0 <= d.confidence <= 1.0


def test_confidence_weighting_discounts_uncertain_layer():
    """A layer with zero confidence should not move the fused score."""
    engine = FusionEngine()
    with_ml = _decide(engine, _signals(t=0.4, m=0.4, x=0.4, ml=-1.0, mlc=0.0))
    without_ml = _decide(engine, _signals(t=0.4, m=0.4, x=0.4, ml=0.0, mlc=0.0))
    assert with_ml.fused_score == pytest.approx(without_ml.fused_score)


def test_volatility_floor_sets_minimum_levels():
    """In a calm market the volatility floor sets the stop distance."""
    cfg = FusionConfig()
    engine = FusionEngine(cfg)
    d = _decide(
        engine, _signals(t=0.8, m=0.7, x=0.8, ml=0.7), ref=100_000.0, atr=1.0
    )  # tiny ATR -> floor binds
    unit = 100_000.0 * cfg.min_volatility_fraction
    assert d.reference_price - d.entry == pytest.approx(cfg.entry_offset_vol_mult * unit, rel=1e-6)
