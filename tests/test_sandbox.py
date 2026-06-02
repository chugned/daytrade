"""Sandbox/testnet execution-layer and credential tests.

These cover the *structural guards* — the testnet-URL allowlist, credential
verification, the disabled-by-default posture. The actual testnet HTTP calls
require live testnet keys and are not exercised offline.
"""

from __future__ import annotations

import pytest

from daytrade.config import SandboxConfig, load_config
from daytrade.exchanges.credentials import (
    ApiCredentials,
    ApiKeyPermissions,
    MainnetKeyError,
    TradePermissionError,
    WithdrawalPermissionError,
    enforce_key_safety,
    load_sandbox_credentials,
)
from daytrade.exchanges.sandbox import (
    _TESTNET_URLS,
    SandboxExchangeClient,
    SandboxSafetyError,
    _assert_testnet_url,
    build_sandbox_client,
)
from daytrade.paper import PaperBroker, SandboxBroker

# --- credentials -----------------------------------------------------------


def test_no_credentials_returns_none(monkeypatch):
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)
    assert load_sandbox_credentials("binance") is None


def test_partial_credentials_raise(monkeypatch):
    from daytrade.exchanges.credentials import MissingCredentialsError

    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "abc")
    monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)
    with pytest.raises(MissingCredentialsError):
        load_sandbox_credentials("binance")


def test_masked_key_hides_secret():
    creds = ApiCredentials("binance", "ABCDEFGH1234", "secret")
    assert creds.masked_key().endswith("1234")
    assert "ABCDEFGH" not in creds.masked_key()


def test_read_only_key_accepted():
    enforce_key_safety(ApiKeyPermissions(can_read=True), SandboxConfig())


def test_withdrawal_key_rejected():
    with pytest.raises(WithdrawalPermissionError):
        enforce_key_safety(ApiKeyPermissions(can_withdraw=True), SandboxConfig())


def test_trade_key_rejected_when_read_only_required():
    with pytest.raises(TradePermissionError):
        enforce_key_safety(ApiKeyPermissions(can_trade=True), SandboxConfig())


def test_trade_key_allowed_when_read_only_disabled():
    cfg = SandboxConfig(require_read_only_keys=False)
    # Trade scope is fine; withdrawal is still banned.
    enforce_key_safety(ApiKeyPermissions(can_trade=True), cfg)


def test_mainnet_key_rejected():
    with pytest.raises(MainnetKeyError):
        enforce_key_safety(ApiKeyPermissions(is_testnet=False), SandboxConfig())


# --- testnet URL guard -----------------------------------------------------


def test_all_sandbox_urls_are_testnet():
    for url in _TESTNET_URLS.values():
        assert "testnet" in url
        _assert_testnet_url(url)


def test_mainnet_url_rejected():
    for url in ("https://api.binance.com", "https://api.bybit.com"):
        with pytest.raises(SandboxSafetyError):
            _assert_testnet_url(url)


def test_sandbox_client_uses_testnet_base_url():
    client = SandboxExchangeClient(ApiCredentials("binance", "k", "s"), SandboxConfig())
    assert client.base_url == _TESTNET_URLS["binance"]
    assert "testnet" in client.base_url


def test_place_order_requires_verified_trade_key():
    """An unverified client cannot place a testnet order."""
    from daytrade.models import Side

    client = SandboxExchangeClient(ApiCredentials("binance", "k", "s"), SandboxConfig())
    with pytest.raises(SandboxSafetyError):
        client.place_test_order("BTCUSDT", Side.BUY, 1.0)


# --- build / broker --------------------------------------------------------


def test_build_sandbox_client_none_when_disabled():
    cfg = load_config(load_dotenv_file=False)
    assert cfg.sandbox.enabled is False
    assert build_sandbox_client(cfg) is None


def test_sandbox_broker_defaults_to_simulated():
    broker = SandboxBroker(PaperBroker(10_000.0))
    assert broker.execution_mode == "simulated"
    assert not broker.is_testnet_execution
