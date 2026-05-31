"""Funding-rate gate tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from daytrade.observatory.funding import (
    FundingSnapshot,
    _CACHE,
    extreme_funding_blocks_buy,
    fetch_current_funding_rate,
    fetch_funding_history,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty cache."""
    _CACHE.clear()
    yield
    _CACHE.clear()


# ---------------------------------------------------------------------------
# extreme_funding_blocks_buy
# ---------------------------------------------------------------------------

def test_normal_funding_does_not_block():
    block, reason = extreme_funding_blocks_buy(0.00005)
    assert block is False
    assert "normal" in reason.lower()


def test_extreme_positive_funding_blocks_buy():
    block, reason = extreme_funding_blocks_buy(0.001)   # 0.10%
    assert block is True
    assert "crowded" in reason.lower() or "pullback" in reason.lower()


def test_extreme_negative_funding_allows_buy_with_squeeze_note():
    block, reason = extreme_funding_blocks_buy(-0.002)  # -0.20%
    assert block is False
    assert "squeeze" in reason.lower()


def test_thresholds_are_configurable():
    block, _ = extreme_funding_blocks_buy(
        0.00015, extreme_positive=0.0001, extreme_negative=-0.001)
    assert block is True


# ---------------------------------------------------------------------------
# fetch_current_funding_rate (HTTP mocked)
# ---------------------------------------------------------------------------

def test_fetch_current_returns_snapshot():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "lastFundingRate": "0.00012",
        "markPrice": "65000.5",
        "time": 1779028800000,
    }
    client = MagicMock(); client.get.return_value = fake
    snap = fetch_current_funding_rate("BTCUSDT", client=client)
    assert snap is not None
    assert snap.symbol == "BTCUSDT"
    assert abs(snap.rate - 0.00012) < 1e-9
    assert abs(snap.mark_price - 65000.5) < 1e-6


def test_fetch_returns_none_on_http_error():
    fake = MagicMock(); fake.status_code = 503
    client = MagicMock(); client.get.return_value = fake
    assert fetch_current_funding_rate("BTCUSDT", client=client) is None


def test_fetch_returns_none_on_exception():
    client = MagicMock()
    client.get.side_effect = RuntimeError("network blip")
    # Must not raise — optional signal.
    assert fetch_current_funding_rate("BTCUSDT", client=client) is None


def test_fetch_uses_cache_on_repeated_calls():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "lastFundingRate": "0.00012", "markPrice": "100", "time": 1}
    client = MagicMock(); client.get.return_value = fake
    fetch_current_funding_rate("BTCUSDT", client=client)
    fetch_current_funding_rate("BTCUSDT", client=client)
    fetch_current_funding_rate("BTCUSDT", client=client)
    assert client.get.call_count == 1  # second + third served from cache


# ---------------------------------------------------------------------------
# fetch_funding_history
# ---------------------------------------------------------------------------

def test_history_parses_rows():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = [
        {"fundingRate": "0.00010", "markPrice": "65000", "fundingTime": 1},
        {"fundingRate": "-0.00050", "markPrice": "64500", "fundingTime": 2},
    ]
    client = MagicMock(); client.get.return_value = fake
    hist = fetch_funding_history("BTCUSDT", limit=2, client=client)
    assert len(hist) == 2
    assert hist[0].rate == 0.0001
    assert hist[1].rate == -0.0005


def test_history_empty_on_error():
    fake = MagicMock(); fake.status_code = 500
    client = MagicMock(); client.get.return_value = fake
    assert fetch_funding_history("BTCUSDT", client=client) == []
