"""Tests for the Binance exchange adapter.

All tests inject a mock ccxt client and a pre-validated permissions
object — no real network, no real credentials. The point is to verify
the adapter's mapping logic, safety gates, and exception translation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from daytrade.live.binance import (
    BinanceExchange,
    ShadowModeError,
    from_env,
)
from daytrade.live.exchange import (
    ExchangeOrder,
    ExchangeUnreachable,
    OrderRejected,
)
from daytrade.models import Side
from daytrade.ops.api_keys import (
    KeyPermissions,
    WithdrawalPermissionForbidden,
)


def _trade_only_perms() -> KeyPermissions:
    return KeyPermissions(
        ip_restricted=True,
        can_trade=True,
        can_withdraw=False,
        can_internal_transfer=False,
        enable_spot_and_margin_trading=True,
        enable_futures=False,
        enable_universal_transfer=False,
    )


def _make(client: MagicMock | None = None, writes_enabled: bool = False
          ) -> tuple[BinanceExchange, MagicMock]:
    client = client or MagicMock()
    ex = BinanceExchange(
        api_key="key", api_secret="secret",
        client=client, writes_enabled=writes_enabled,
        permissions=_trade_only_perms(),
    )
    return ex, client


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def test_construction_requires_credentials():
    with pytest.raises(ValueError, match="api_key"):
        BinanceExchange(api_key="", api_secret="x",
                        client=MagicMock(),
                        permissions=_trade_only_perms())


def test_construction_refuses_withdraw_enabled_key():
    perms = KeyPermissions(
        ip_restricted=True, can_trade=True,
        can_withdraw=True,  # the cardinal sin
        can_internal_transfer=False,
        enable_spot_and_margin_trading=True,
        enable_futures=False, enable_universal_transfer=False,
    )
    with pytest.raises(WithdrawalPermissionForbidden):
        BinanceExchange(api_key="k", api_secret="s",
                        client=MagicMock(), permissions=perms)


def test_construction_refuses_internal_transfer_key():
    perms = KeyPermissions(
        ip_restricted=True, can_trade=True, can_withdraw=False,
        can_internal_transfer=True,  # also rejected
        enable_spot_and_margin_trading=True, enable_futures=False,
        enable_universal_transfer=False,
    )
    with pytest.raises(WithdrawalPermissionForbidden):
        BinanceExchange(api_key="k", api_secret="s",
                        client=MagicMock(), permissions=perms)


def test_construction_refuses_universal_transfer_key():
    perms = KeyPermissions(
        ip_restricted=True, can_trade=True, can_withdraw=False,
        can_internal_transfer=False,
        enable_spot_and_margin_trading=True, enable_futures=False,
        enable_universal_transfer=True,
    )
    with pytest.raises(WithdrawalPermissionForbidden):
        BinanceExchange(api_key="k", api_secret="s",
                        client=MagicMock(), permissions=perms)


def test_construction_refuses_key_without_trade_permission():
    perms = KeyPermissions(
        ip_restricted=True, can_trade=False, can_withdraw=False,
        can_internal_transfer=False,
        enable_spot_and_margin_trading=False, enable_futures=False,
        enable_universal_transfer=False,
    )
    with pytest.raises(WithdrawalPermissionForbidden, match="spot trading"):
        BinanceExchange(api_key="k", api_secret="s",
                        client=MagicMock(), permissions=perms)


def test_writes_disabled_by_default():
    ex, _ = _make()
    assert ex.writes_enabled is False


def test_place_market_order_raises_in_shadow_mode():
    ex, _ = _make(writes_enabled=False)
    order = ExchangeOrder(
        client_order_id="x", symbol="BTCUSDT", side=Side.BUY,
        quantity=0.001, reference_price=100_000.0,
    )
    with pytest.raises(ShadowModeError):
        ex.place_market_order(order)


def test_enable_writes_flips_state():
    ex, _ = _make()
    assert ex.writes_enabled is False
    ex.enable_writes()
    assert ex.writes_enabled is True


# ---------------------------------------------------------------------------
# Read paths
# ---------------------------------------------------------------------------


def test_get_balance_extracts_free_asset():
    ex, client = _make()
    client.fetch_balance.return_value = {"USDT": {"free": 250.5, "total": 250.5}}
    assert ex.get_balance("USDT") == 250.5


def test_get_balance_returns_zero_when_missing():
    ex, client = _make()
    client.fetch_balance.return_value = {}
    assert ex.get_balance("USDT") == 0.0


def test_get_balance_maps_failure_to_unreachable():
    ex, client = _make()
    client.fetch_balance.side_effect = RuntimeError("connection refused")
    with pytest.raises(ExchangeUnreachable):
        ex.get_balance("USDT")


def test_get_position_uses_base_asset_total():
    ex, client = _make()
    client.fetch_balance.return_value = {"BTC": {"free": 0.001, "total": 0.0015}}
    pos = ex.get_position("BTCUSDT")
    assert pos.symbol == "BTCUSDT"
    assert pos.quantity == 0.0015


def test_get_position_zero_when_flat():
    ex, client = _make()
    client.fetch_balance.return_value = {}
    pos = ex.get_position("BTCUSDT")
    assert pos.quantity == 0.0


def test_list_open_orders_translates_ccxt_format():
    ex, client = _make()
    client.fetch_open_orders.return_value = [
        {"clientOrderId": "abc", "id": "12345", "symbol": "BTC/USDT",
         "side": "buy", "amount": 0.001, "filled": 0.0,
         "average": 0.0, "price": 100_000.0, "status": "open"}
    ]
    orders = ex.list_open_orders("BTCUSDT")
    assert len(orders) == 1
    o = orders[0]
    assert o.client_order_id == "abc"
    assert o.symbol == "BTCUSDT"
    assert o.side is Side.BUY
    assert o.status == "open"


# ---------------------------------------------------------------------------
# Write path (writes_enabled=True)
# ---------------------------------------------------------------------------


def test_place_market_order_succeeds_when_writes_enabled():
    ex, client = _make(writes_enabled=True)
    client.create_order.return_value = {
        "filled": 0.001, "average": 100_050.0,
        "fee": {"cost": 0.10}, "timestamp": 1750000000000,
    }
    order = ExchangeOrder(
        client_order_id="abc123", symbol="BTCUSDT", side=Side.BUY,
        quantity=0.001, reference_price=100_000.0,
    )
    fill = ex.place_market_order(order)
    assert fill.quantity == 0.001
    assert fill.price == 100_050.0
    assert fill.fee == 0.10
    # ccxt must have been called with the right symbol format + clientOrderId
    args, kwargs = client.create_order.call_args
    assert args[0] == "BTC/USDT"
    assert args[1] == "market"
    assert args[2] == "buy"
    assert args[3] == 0.001


def test_place_market_order_maps_insufficient_to_rejected():
    ex, client = _make(writes_enabled=True)
    client.create_order.side_effect = RuntimeError("Insufficient balance")
    order = ExchangeOrder(
        client_order_id="x", symbol="BTCUSDT", side=Side.BUY,
        quantity=0.001, reference_price=100_000.0,
    )
    with pytest.raises(OrderRejected):
        ex.place_market_order(order)


def test_place_market_order_maps_min_lot_to_rejected():
    ex, client = _make(writes_enabled=True)
    client.create_order.side_effect = RuntimeError("LOT_SIZE filter failure")
    order = ExchangeOrder(
        client_order_id="x", symbol="BTCUSDT", side=Side.BUY,
        quantity=0.000001, reference_price=100_000.0,
    )
    with pytest.raises(OrderRejected):
        ex.place_market_order(order)


def test_place_market_order_maps_network_to_unreachable():
    ex, client = _make(writes_enabled=True)
    client.create_order.side_effect = RuntimeError("connection timed out")
    order = ExchangeOrder(
        client_order_id="x", symbol="BTCUSDT", side=Side.BUY,
        quantity=0.001, reference_price=100_000.0,
    )
    with pytest.raises(ExchangeUnreachable):
        ex.place_market_order(order)


def test_place_market_order_rejects_zero_filled_response():
    ex, client = _make(writes_enabled=True)
    client.create_order.return_value = {
        "filled": 0.0, "average": 0.0, "timestamp": 1750000000000,
    }
    order = ExchangeOrder(
        client_order_id="x", symbol="BTCUSDT", side=Side.BUY,
        quantity=0.001, reference_price=100_000.0,
    )
    with pytest.raises(OrderRejected):
        ex.place_market_order(order)


def test_cancel_order_requires_writes_enabled():
    ex, _ = _make()
    with pytest.raises(ShadowModeError):
        ex.cancel_order("BTCUSDT", "abc")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_symbol_translation():
    assert BinanceExchange._to_ccxt_symbol("BTCUSDT") == "BTC/USDT"
    assert BinanceExchange._to_ccxt_symbol("ETHBUSD") == "ETH/BUSD"
    assert BinanceExchange._to_ccxt_symbol("BTC/USDT") == "BTC/USDT"


def test_from_env_requires_both_vars(monkeypatch):
    monkeypatch.delenv("DAYTRADE_K", raising=False)
    monkeypatch.delenv("DAYTRADE_S", raising=False)
    with pytest.raises(RuntimeError, match="API credentials"):
        from_env(api_key_env="DAYTRADE_K", api_secret_env="DAYTRADE_S",
                 permissions=_trade_only_perms())


def test_from_env_reads_credentials(monkeypatch):
    monkeypatch.setenv("DAYTRADE_K", "thekey")
    monkeypatch.setenv("DAYTRADE_S", "thesecret")
    # ccxt won't be present; from_env should still attempt construction
    # which will fail at the ccxt import. We verify that path by catching
    # the specific error.
    with pytest.raises(RuntimeError, match="ccxt"):
        from_env(api_key_env="DAYTRADE_K", api_secret_env="DAYTRADE_S",
                 permissions=_trade_only_perms())
