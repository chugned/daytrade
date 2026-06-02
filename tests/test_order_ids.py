"""Idempotent client-order-id tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daytrade.ops import OrderIDRegistry, generate_client_order_id


def test_same_inputs_produce_the_same_id():
    """Idempotency: a retry within the same bucket re-uses the id."""
    ts = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    a = generate_client_order_id("BTCUSDT", "buy", ts)
    b = generate_client_order_id("BTCUSDT", "buy", ts)
    assert a == b


def test_different_buckets_produce_different_ids():
    """A new minute should generate a fresh id (a new genuine decision)."""
    t1 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    t2 = t1 + timedelta(seconds=61)
    assert generate_client_order_id(
        "BTCUSDT", "buy", t1, bucket_seconds=60
    ) != generate_client_order_id("BTCUSDT", "buy", t2, bucket_seconds=60)


def test_different_symbols_produce_different_ids():
    ts = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    assert generate_client_order_id("BTCUSDT", "buy", ts) != generate_client_order_id(
        "ETHUSDT", "buy", ts
    )


def test_different_sides_produce_different_ids():
    ts = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    assert generate_client_order_id("BTCUSDT", "buy", ts) != generate_client_order_id(
        "BTCUSDT", "sell", ts
    )


def test_id_fits_exchange_length_limit():
    ts = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    cid = generate_client_order_id("BTCUSDT", "buy", ts, intent="entry")
    assert 0 < len(cid) <= 32


def test_id_is_human_readable():
    ts = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    cid = generate_client_order_id("BTCUSDT", "buy", ts, intent="entry")
    assert cid.startswith("dt-BTC-B-ent-")


def test_registry_rejects_duplicates():
    reg = OrderIDRegistry()
    assert reg.register("dt-abc-123") is True
    assert reg.register("dt-abc-123") is False  # second time = blocked
    assert reg.has("dt-abc-123")
    assert len(reg) == 1


def test_registry_distinguishes_different_ids():
    reg = OrderIDRegistry()
    reg.register("a")
    reg.register("b")
    assert reg.register("a") is False
    assert reg.register("c") is True
    assert len(reg) == 3


def test_registry_refuses_empty_id():
    reg = OrderIDRegistry()
    with pytest.raises(ValueError):
        reg.register("")


def test_generate_validates_inputs():
    with pytest.raises(ValueError):
        generate_client_order_id("", "buy")
    with pytest.raises(ValueError):
        generate_client_order_id("BTC", "", "entry")
    with pytest.raises(ValueError):
        generate_client_order_id("BTC", "buy", bucket_seconds=0)
