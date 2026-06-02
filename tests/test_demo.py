"""Canonical demo reproduction test.

PLAN.md mandates a specific scenario. This test pins the platform's output to
those numbers so a regression anywhere in the pipeline is caught immediately.
"""

from __future__ import annotations

import pytest

from daytrade.config import load_config
from daytrade.demo import (
    DEMO_MACRO_SCENARIO,
    DEMO_REFERENCE_PRICE,
    build_demo_candles,
    build_demo_orderbook,
)
from daytrade.models import Action
from daytrade.pipeline import AnalysisPipeline


@pytest.fixture
def demo_result():
    cfg = load_config(load_dotenv_file=False)
    return AnalysisPipeline(cfg).analyze(
        build_demo_candles(),
        build_demo_orderbook(),
        reference_price=DEMO_REFERENCE_PRICE,
        macro_scenario=DEMO_MACRO_SCENARIO,
    )


def test_demo_action_is_buy(demo_result):
    assert demo_result.decision.action is Action.BUY


def test_demo_confidence_near_060(demo_result):
    assert demo_result.decision.confidence == pytest.approx(0.60, abs=0.03)


def test_demo_entry_price(demo_result):
    # Levels reflect the Phase-1 stop config (2x stop, raised vol floor).
    assert round(demo_result.decision.entry) == 103_020


def test_demo_stop_price(demo_result):
    assert round(demo_result.decision.stop) == 102_193


def test_demo_target_price(demo_result):
    assert round(demo_result.decision.target) == 104_261


def test_demo_rsi_oversold(demo_result):
    rsi = demo_result.technical.rsi
    assert rsi is not None and rsi < 30


def test_demo_macro_is_bullish(demo_result):
    assert demo_result.macro.confidence == pytest.approx(0.85)
    assert demo_result.macro.score > 0


def test_demo_orderbook_sell_heavy(demo_result):
    # "30% more sellers" => negative imbalance.
    assert demo_result.microstructure.imbalance < 0


def test_demo_is_deterministic():
    cfg = load_config(load_dotenv_file=False)
    a = AnalysisPipeline(cfg).analyze(
        build_demo_candles(),
        build_demo_orderbook(),
        reference_price=DEMO_REFERENCE_PRICE,
        macro_scenario=DEMO_MACRO_SCENARIO,
    )
    b = AnalysisPipeline(cfg).analyze(
        build_demo_candles(),
        build_demo_orderbook(),
        reference_price=DEMO_REFERENCE_PRICE,
        macro_scenario=DEMO_MACRO_SCENARIO,
    )
    assert a.decision.confidence == b.decision.confidence
    assert a.decision.entry == b.decision.entry
