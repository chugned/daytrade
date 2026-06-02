"""Tests for the Fear & Greed sentiment regime tag.

We never hit the real network — every test monkeypatches the underlying
``urlopen`` (via ``_fetch_raw``) so the suite is fully offline.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pytest

from daytrade.observatory import fear_greed as fg


@pytest.fixture(autouse=True)
def _clear_cache():
    fg.reset_cache()
    yield
    fg.reset_cache()


def _payload(value: float, classification: str = "Greed") -> Dict[str, Any]:
    return {
        "name": "Fear and Greed Index",
        "data": [
            {
                "value": str(value),
                "value_classification": classification,
                "timestamp": "1700000000",
                "time_until_update": "12345",
            }
        ],
        "metadata": {"error": None},
    }


def _patch_fetch(monkeypatch, payload: Optional[Dict[str, Any]]):
    """Replace the raw fetch with a stub returning ``payload`` (or None)."""

    def _stub(_timeout):
        return payload

    monkeypatch.setattr(fg, "_fetch_raw", _stub)


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------


def test_parse_extracts_value_and_classification(monkeypatch):
    _patch_fetch(monkeypatch, _payload(72.5, "Greed"))
    reading = fg.fetch_fear_greed()
    assert reading is not None
    assert reading.value == pytest.approx(72.5)
    assert reading.classification == "Greed"


def test_parse_rejects_out_of_range(monkeypatch):
    _patch_fetch(monkeypatch, _payload(150.0))
    assert fg.fetch_fear_greed() is None


def test_parse_handles_empty_data(monkeypatch):
    _patch_fetch(monkeypatch, {"data": []})
    assert fg.fetch_fear_greed() is None


def test_parse_handles_missing_data_key(monkeypatch):
    _patch_fetch(monkeypatch, {"name": "Fear and Greed Index"})
    assert fg.fetch_fear_greed() is None


def test_parse_handles_garbage_value(monkeypatch):
    _patch_fetch(monkeypatch, {"data": [{"value": "n/a"}]})
    assert fg.fetch_fear_greed() is None


def test_network_failure_returns_none(monkeypatch):
    _patch_fetch(monkeypatch, None)
    assert fg.fetch_fear_greed() is None


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_cache_returns_same_object_within_ttl(monkeypatch):
    calls = {"n": 0}

    def _stub(_timeout):
        calls["n"] += 1
        return _payload(60.0)

    monkeypatch.setattr(fg, "_fetch_raw", _stub)
    a = fg.fetch_fear_greed(cache_ttl_s=3600, now=1_000_000.0)
    b = fg.fetch_fear_greed(cache_ttl_s=3600, now=1_000_000.0 + 30.0)
    assert a is b
    assert calls["n"] == 1


def test_cache_refreshes_after_ttl(monkeypatch):
    seq = iter([_payload(40.0), _payload(85.0)])

    def _stub(_timeout):
        return next(seq)

    monkeypatch.setattr(fg, "_fetch_raw", _stub)
    a = fg.fetch_fear_greed(cache_ttl_s=10.0, now=1_000_000.0)
    b = fg.fetch_fear_greed(cache_ttl_s=10.0, now=1_000_000.0 + 100.0)
    assert a is not None and b is not None
    assert a.value == 40.0
    assert b.value == 85.0


def test_cache_survives_failed_refresh(monkeypatch):
    """A failed second fetch must not destroy the last good reading."""
    seq = iter([_payload(55.0), None])

    def _stub(_timeout):
        return next(seq)

    monkeypatch.setattr(fg, "_fetch_raw", _stub)
    first = fg.fetch_fear_greed(cache_ttl_s=10.0, now=1_000_000.0)
    second = fg.fetch_fear_greed(cache_ttl_s=10.0, now=1_000_000.0 + 100.0)
    assert first is not None and second is not None
    assert first is second  # same cached object


# ---------------------------------------------------------------------------
# Gate logic
# ---------------------------------------------------------------------------


def test_extreme_greed_blocks_buy_at_or_above_threshold():
    r = fg.FearGreedReading(value=85.0, classification="Extreme Greed", fetched_at=0.0)
    assert fg.extreme_greed_blocks_buy(r, threshold=80.0) is True
    assert fg.extreme_greed_blocks_buy(r, threshold=90.0) is False


def test_extreme_fear_blocks_sell_at_or_below_threshold():
    r = fg.FearGreedReading(value=15.0, classification="Extreme Fear", fetched_at=0.0)
    assert fg.extreme_fear_blocks_sell(r, threshold=20.0) is True
    assert fg.extreme_fear_blocks_sell(r, threshold=10.0) is False


def test_gates_default_to_allow_when_reading_missing():
    assert fg.extreme_greed_blocks_buy(None, threshold=80.0) is False
    assert fg.extreme_fear_blocks_sell(None, threshold=20.0) is False


# ---------------------------------------------------------------------------
# Regime labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (10.0, "EXTREME_FEAR"),
        (25.0, "FEAR"),
        (50.0, "NEUTRAL"),
        (70.0, "GREED"),
        (95.0, "EXTREME_GREED"),
    ],
)
def test_regime_label_buckets(value, expected):
    r = fg.FearGreedReading(value=value, classification="x", fetched_at=0.0)
    assert fg.regime_label(r) == expected


def test_regime_label_unknown_when_missing():
    assert fg.regime_label(None) == "UNKNOWN"


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------


def test_gating_config_has_fear_greed_fields():
    from daytrade.config.schema import GatingConfig

    g = GatingConfig()
    assert g.use_fear_greed_gate is False  # opt-in only
    assert 0.0 <= g.fear_greed_extreme_fear <= 100.0
    assert 0.0 <= g.fear_greed_extreme_greed <= 100.0
    assert g.fear_greed_extreme_fear < g.fear_greed_extreme_greed


def test_parsing_round_trips_through_json(monkeypatch):
    """End-to-end sanity: the documented JSON shape parses cleanly."""
    sample = json.dumps(_payload(33.0, "Fear"))

    def _stub(_timeout):
        return json.loads(sample)

    monkeypatch.setattr(fg, "_fetch_raw", _stub)
    reading = fg.fetch_fear_greed()
    assert reading is not None
    assert reading.value == 33.0
    assert reading.classification == "Fear"
    assert fg.regime_label(reading) == "FEAR"
