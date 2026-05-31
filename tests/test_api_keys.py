"""Trade-only API-key validator tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from daytrade.ops.api_keys import (
    KeyPermissions,
    WithdrawalPermissionForbidden,
    assert_trade_only,
    inspect_key,
)


def _perms(**overrides) -> KeyPermissions:
    defaults = dict(
        ip_restricted=True,
        can_trade=True,
        can_withdraw=False,
        can_internal_transfer=False,
        enable_spot_and_margin_trading=True,
        enable_futures=False,
        enable_universal_transfer=False,
    )
    defaults.update(overrides)
    return KeyPermissions(**defaults)


# ---------------------------------------------------------------------------
# assert_trade_only
# ---------------------------------------------------------------------------

def test_trade_only_key_passes():
    assert_trade_only(_perms())   # no raise


def test_withdrawal_permission_is_refused():
    with pytest.raises(WithdrawalPermissionForbidden) as exc:
        assert_trade_only(_perms(can_withdraw=True))
    assert "enableWithdrawals" in str(exc.value)


def test_internal_transfer_permission_is_refused():
    with pytest.raises(WithdrawalPermissionForbidden):
        assert_trade_only(_perms(can_internal_transfer=True))


def test_universal_transfer_permission_is_refused():
    with pytest.raises(WithdrawalPermissionForbidden):
        assert_trade_only(_perms(enable_universal_transfer=True))


def test_key_without_trading_permission_is_refused():
    with pytest.raises(WithdrawalPermissionForbidden):
        assert_trade_only(_perms(can_trade=False))


def test_is_trade_only_property():
    assert _perms().is_trade_only is True
    assert _perms(can_withdraw=True).is_trade_only is False
    assert _perms(can_internal_transfer=True).is_trade_only is False


# ---------------------------------------------------------------------------
# inspect_key (mocked HTTP)
# ---------------------------------------------------------------------------

def test_inspect_key_signs_request_and_maps_flags():
    """Mock Binance to return a 'trade-only' key payload; check parsing."""
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "ipRestrict": True,
        "enableSpotAndMarginTrading": True,
        "enableWithdrawals": False,
        "enableInternalTransfer": False,
        "enableFutures": False,
        "permitsUniversalTransfer": False,
    }
    fake_response.raise_for_status = MagicMock()

    client = MagicMock()
    client.get.return_value = fake_response

    perms = inspect_key("api-key", "api-secret", client=client)

    assert perms.can_trade is True
    assert perms.can_withdraw is False
    assert perms.is_trade_only is True

    # Check we sent the key header and a signed query.
    call = client.get.call_args
    url = call.args[0]
    assert "signature=" in url
    assert call.kwargs["headers"]["X-MBX-APIKEY"] == "api-key"


def test_inspect_key_refuses_empty_credentials():
    with pytest.raises(ValueError):
        inspect_key("", "secret")
    with pytest.raises(ValueError):
        inspect_key("key", "")
