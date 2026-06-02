"""Kill-switch tests — macro and micro circuit breakers."""

from __future__ import annotations

from daytrade.config import KillSwitchConfig
from daytrade.models import (
    Bias,
    MacroSignal,
    MarketRegime,
    MicrostructureSignal,
    RiskLevel,
)
from daytrade.safety import evaluate_kill_switch


def _macro(regime="neutral", risk=RiskLevel.MEDIUM):
    return MacroSignal(
        symbol="BTC",
        timestamp=0,
        bias=Bias.NEUTRAL,
        score=0.0,
        confidence=0.5,
        risk_level=risk,
        regime_label=regime,
    )


def _micro(spread=4.0, chop=False, thin=False):
    return MicrostructureSignal(
        symbol="BTC",
        timestamp=0,
        bias=Bias.NEUTRAL,
        score=0.0,
        confidence=0.5,
        spread_bps=spread,
        regime=MarketRegime.RANGE,
        chop_zone=chop,
        thin_liquidity=thin,
    )


CFG = KillSwitchConfig()


def test_clear_when_calm():
    result = evaluate_kill_switch(_macro(), _micro(), CFG)
    assert not result.active


def test_macro_kill_on_exchange_collapse():
    result = evaluate_kill_switch(_macro("exchange_collapse"), _micro(), CFG)
    assert result.active and result.macro_triggered


def test_macro_kill_on_war():
    result = evaluate_kill_switch(_macro("war"), _micro(), CFG)
    assert result.active and result.macro_triggered


def test_macro_kill_on_panic():
    result = evaluate_kill_switch(_macro("panic"), _micro(), CFG)
    assert result.active and result.macro_triggered


def test_macro_kill_on_extreme_risk():
    result = evaluate_kill_switch(_macro(risk=RiskLevel.EXTREME), _micro(), CFG)
    assert result.active and result.macro_triggered


def test_micro_kill_on_chop():
    result = evaluate_kill_switch(_macro(), _micro(chop=True), CFG)
    assert result.active and result.micro_triggered


def test_micro_kill_on_thin_liquidity():
    result = evaluate_kill_switch(_macro(), _micro(thin=True), CFG)
    assert result.active and result.micro_triggered


def test_micro_kill_on_extreme_spread():
    result = evaluate_kill_switch(_macro(), _micro(spread=99.0), CFG)
    assert result.active and result.micro_triggered


def test_reasons_are_tagged():
    result = evaluate_kill_switch(_macro("panic"), _micro(chop=True), CFG)
    assert any(r.startswith("[macro]") for r in result.reasons)
    assert any(r.startswith("[micro]") for r in result.reasons)
