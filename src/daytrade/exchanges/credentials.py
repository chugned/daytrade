"""API credential loading and the key-permission safety policy.

This module is the gatekeeper for exchange API keys. Two rules are absolute
and cannot be configured away:

1. **No withdrawal access, ever.** A key whose verified permissions include
   withdrawal is rejected outright — the platform must never be able to move
   money off an exchange.
2. **Testnet only.** Keys are loaded exclusively from ``*_TESTNET_*`` env
   variables and are only ever used against testnet endpoints.

By default keys must also be *read-only* (no trade scope) — pure monitoring.
Placing sandbox orders requires the operator to explicitly lower
``sandbox.require_read_only_keys``; even then, withdrawal access stays banned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from ..config.schema import SandboxConfig


class SecurityError(RuntimeError):
    """Base class for credential / permission security failures."""


class MissingCredentialsError(SecurityError):
    """Raised when expected testnet credentials are not configured."""


class WithdrawalPermissionError(SecurityError):
    """Raised when an API key carries withdrawal permission — never allowed."""


class TradePermissionError(SecurityError):
    """Raised when a key has trade scope but read-only keys are required."""


class MainnetKeyError(SecurityError):
    """Raised when a key appears to be a live/mainnet key, not a testnet key."""


# Environment-variable names per exchange. Only TESTNET keys are ever read.
_ENV_KEYS = {
    "binance": ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"),
    "bybit": ("BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET"),
}


@dataclass(frozen=True)
class ApiKeyPermissions:
    """The verified permission scopes of an API key.

    Populated by querying the exchange after connecting; until then a key's
    permissions are unknown and it must not be trusted for execution.
    """

    can_read: bool = True
    can_trade: bool = False
    can_withdraw: bool = False
    can_margin: bool = False
    can_futures: bool = False
    is_testnet: bool = True

    @property
    def is_read_only(self) -> bool:
        return not (self.can_trade or self.can_withdraw or self.can_margin or self.can_futures)

    def describe(self) -> str:
        scopes = [
            name
            for name, on in (
                ("read", self.can_read),
                ("trade", self.can_trade),
                ("withdraw", self.can_withdraw),
                ("margin", self.can_margin),
                ("futures", self.can_futures),
            )
            if on
        ]
        return ", ".join(scopes) or "none"


@dataclass
class ApiCredentials:
    """A loaded API key/secret pair plus (once verified) its permissions."""

    exchange: str
    api_key: str
    api_secret: str
    permissions: Optional[ApiKeyPermissions] = field(default=None)

    @property
    def is_verified(self) -> bool:
        return self.permissions is not None

    def masked_key(self) -> str:
        """The key with all but the last 4 chars masked — safe to log."""
        if len(self.api_key) <= 4:
            return "****"
        return "*" * (len(self.api_key) - 4) + self.api_key[-4:]


def load_sandbox_credentials(exchange: str) -> Optional[ApiCredentials]:
    """Load testnet credentials for ``exchange`` from the environment.

    Returns ``None`` when no keys are configured (the platform then stays in
    pure paper-simulation mode). Raises only on a partially-configured pair.
    """
    exchange = exchange.lower().strip()
    if exchange not in _ENV_KEYS:
        raise SecurityError(f"unknown sandbox exchange: {exchange}")

    key_var, secret_var = _ENV_KEYS[exchange]
    api_key = os.environ.get(key_var, "").strip()
    api_secret = os.environ.get(secret_var, "").strip()

    if not api_key and not api_secret:
        return None
    if not api_key or not api_secret:
        raise MissingCredentialsError(f"{exchange}: both {key_var} and {secret_var} must be set")
    return ApiCredentials(exchange=exchange, api_key=api_key, api_secret=api_secret)


def enforce_key_safety(
    permissions: ApiKeyPermissions,
    config: SandboxConfig,
) -> None:
    """Apply the credential safety policy. Raises on any violation.

    * withdrawal permission -> always rejected
    * non-testnet key       -> always rejected
    * trade permission when read-only keys are required -> rejected
    """
    if permissions.can_withdraw:
        raise WithdrawalPermissionError(
            "API key has WITHDRAWAL permission — rejected. This platform must "
            "never be able to move funds. Use a key with withdrawals disabled."
        )
    if not permissions.is_testnet:
        raise MainnetKeyError(
            "API key is not a testnet key — rejected. Only exchange testnet "
            "keys are accepted; there is no mainnet execution path."
        )
    if config.reject_withdrawal_keys is False:  # defense-in-depth
        raise SecurityError("reject_withdrawal_keys must be true")
    if config.require_read_only_keys and permissions.can_trade:
        raise TradePermissionError(
            "API key has TRADE scope but sandbox.require_read_only_keys is "
            "true. Either supply a read-only key, or explicitly set "
            "require_read_only_keys: false to allow TESTNET order placement."
        )
