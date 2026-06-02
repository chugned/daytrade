"""Trade-only API key validation — refuse any key with withdrawal permission.

This is the **single most important** safety control on the eventual live
path. Even if everything else fails — server compromised, malware on the
laptop, a rogue dependency — a key that *cannot* withdraw can at worst be
used to *trade* the account, never *drain* it. With withdrawal disabled,
the absolute floor on damage is the equity itself.

This module ships:

* :class:`KeyPermissions` — the permission flags as the exchange reports them.
* :func:`inspect_key`     — queries Binance's API-restrictions endpoint with
                             an HMAC-signed request and returns the flags.
* :func:`assert_trade_only` — raises :class:`WithdrawalPermissionForbidden`
                              if withdrawal is enabled (the cardinal sin).

No live-execution path uses these yet — they are a primitive ready for the
future. Importing this module does not enable live trading.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

import httpx

_BINANCE_BASE = "https://api.binance.com"


class WithdrawalPermissionForbidden(RuntimeError):
    """Raised when an API key reports withdrawal permission enabled."""

    def __init__(
        self,
        message: str = "API key has withdrawal permission "
        "enabled — refuse to use it. Disable withdrawals on the key "
        "before retrying.",
    ) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class KeyPermissions:
    """Trading-relevant permissions reported by the exchange for a key."""

    ip_restricted: bool
    can_trade: bool
    can_withdraw: bool
    can_internal_transfer: bool
    enable_spot_and_margin_trading: bool
    enable_futures: bool
    enable_universal_transfer: bool

    @property
    def is_trade_only(self) -> bool:
        """True iff trading is allowed AND withdrawal-style flags are all off."""
        return (
            self.can_trade
            and not self.can_withdraw
            and not self.can_internal_transfer
            and not self.enable_universal_transfer
        )


def _sign(secret: str, query_string: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def inspect_key(
    api_key: str,
    api_secret: str,
    base_url: str = _BINANCE_BASE,
    client: Optional[httpx.Client] = None,
) -> KeyPermissions:
    """Query Binance's API-restrictions endpoint and return the flags.

    Uses the signed ``GET /sapi/v1/account/apiRestrictions`` endpoint.
    Raises ``httpx.HTTPError`` on transport failures; the caller's job to
    react to those.
    """
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret are required")

    timestamp = int(time.time() * 1000)
    query = urllib.parse.urlencode({"timestamp": timestamp, "recvWindow": 5000})
    signed = f"{query}&signature={_sign(api_secret, query)}"
    url = f"{base_url}/sapi/v1/account/apiRestrictions?{signed}"
    headers = {"X-MBX-APIKEY": api_key}

    own_client = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if own_client:
            client.close()

    return KeyPermissions(
        ip_restricted=bool(data.get("ipRestrict", False)),
        can_trade=bool(data.get("enableSpotAndMarginTrading", False)),
        can_withdraw=bool(data.get("enableWithdrawals", False)),
        can_internal_transfer=bool(data.get("enableInternalTransfer", False)),
        enable_spot_and_margin_trading=bool(data.get("enableSpotAndMarginTrading", False)),
        enable_futures=bool(data.get("enableFutures", False)),
        enable_universal_transfer=bool(data.get("permitsUniversalTransfer", False)),
    )


def assert_trade_only(perms: KeyPermissions) -> None:
    """Raise :class:`WithdrawalPermissionForbidden` if the key can withdraw.

    Use this immediately after :func:`inspect_key` and before *any* live
    trading code path. There is no override.
    """
    if perms.can_withdraw:
        raise WithdrawalPermissionForbidden(
            "API key has enableWithdrawals=true — refuse. Disable withdrawals "
            "in the Binance API settings, IP-allowlist the key to your "
            "trading server, then retry."
        )
    if perms.can_internal_transfer or perms.enable_universal_transfer:
        raise WithdrawalPermissionForbidden(
            "API key allows internal/universal transfers — same risk as "
            "withdrawal. Disable those flags and retry."
        )
    if not perms.can_trade:
        raise WithdrawalPermissionForbidden(
            "API key does not have spot trading enabled — cannot use it."
        )
