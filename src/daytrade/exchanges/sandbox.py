"""Exchange SANDBOX (testnet) execution layer.

SANDBOX MEANS TESTNET. There is no mainnet execution path in this module —
not a disabled one, not a guarded one: there is no code that can send an
order to a real exchange.

Structural safety, enforced here and tested in ``tests/test_sandbox.py``:

* **Testnet-URL allowlist.** A :class:`SandboxExchangeClient` can only be
  constructed against a URL in ``_TESTNET_URLS``. There is no parameter to
  pass an arbitrary (mainnet) URL; every signed request re-asserts the base
  URL is a known testnet before sending.
* **No withdrawal path.** This module contains no withdrawal or transfer
  call. On connect it verifies the key's permissions and rejects any key
  with withdrawal scope (:func:`credentials.enforce_key_safety`).
* **Read-only by default.** Unless the operator explicitly lowers
  ``sandbox.require_read_only_keys``, a trade-scoped key is refused and
  execution falls back to local simulation.

Even with a trade-scoped testnet key, the worst an order can do is move
*testnet play money*.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx

from ..config.schema import AppConfig, SandboxConfig
from ..models import Fill, Side
from ..runtime import get_logger
from .base import ExchangeError
from .credentials import (
    ApiCredentials,
    ApiKeyPermissions,
    SecurityError,
    enforce_key_safety,
    load_sandbox_credentials,
)

_log = get_logger("exchanges.sandbox")

# The ONLY base URLs this module will ever talk to. Both are exchange
# testnets serving play money. Mainnet hosts appear nowhere in this file.
_TESTNET_URLS: Dict[str, str] = {
    "binance": "https://testnet.binance.vision",
    "bybit": "https://api-testnet.bybit.com",
}

# Hosts that must never be reached for execution. Used by the URL guard as a
# belt-and-braces check in addition to the positive allowlist.
_FORBIDDEN_HOST_FRAGMENTS = (
    "api.binance.com",
    "api-gcp.binance.com",
    "api.bybit.com",
    "api.kraken.com",
    "api.coinbase.com",
)


class SandboxSafetyError(SecurityError):
    """Raised when a sandbox operation would breach the testnet-only contract."""


def _assert_testnet_url(url: str) -> None:
    """Hard guard: ``url`` must be a known testnet base and nothing else."""
    if url not in _TESTNET_URLS.values():
        raise SandboxSafetyError(
            f"refusing non-testnet base URL: {url!r}. Sandbox execution is "
            "testnet-only; there is no mainnet path."
        )
    if any(frag in url for frag in _FORBIDDEN_HOST_FRAGMENTS):
        raise SandboxSafetyError(f"refusing forbidden (mainnet) host in {url!r}")


class SandboxExchangeClient:
    """A testnet-only signed exchange client (Binance / Bybit testnet)."""

    def __init__(
        self, credentials: ApiCredentials, config: SandboxConfig, timeout: float = 10.0
    ) -> None:
        self.exchange = credentials.exchange
        if self.exchange not in _TESTNET_URLS:
            raise SandboxSafetyError(f"no testnet for exchange {self.exchange!r}")
        self.base_url = _TESTNET_URLS[self.exchange]
        _assert_testnet_url(self.base_url)  # guard at construction
        self._creds = credentials
        self._config = config
        self._timeout = timeout

    # -- low-level signed transport -----------------------------------------

    def _client(self) -> httpx.Client:
        _assert_testnet_url(self.base_url)  # guard again before every call
        return httpx.Client(base_url=self.base_url, timeout=self._timeout)

    def _binance_signed(
        self, method: str, path: str, params: Optional[Dict[str, object]] = None
    ) -> dict:
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urllib.parse.urlencode(params)
        signature = hmac.new(
            self._creds.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        url = f"{path}?{query}&signature={signature}"
        headers = {"X-MBX-APIKEY": self._creds.api_key}
        with self._client() as client:
            resp = client.request(method, url, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def _bybit_signed(
        self, method: str, path: str, params: Optional[Dict[str, object]] = None
    ) -> dict:
        params = dict(params or {})
        ts = str(int(time.time() * 1000))
        recv = "5000"
        if method.upper() == "GET":
            payload = urllib.parse.urlencode(params)
            sign_src = ts + self._creds.api_key + recv + payload
            url = f"{path}?{payload}" if payload else path
            body = None
        else:
            import json as _json

            body = _json.dumps(params, separators=(",", ":"))
            sign_src = ts + self._creds.api_key + recv + body
            url = path
        signature = hmac.new(
            self._creds.api_secret.encode(), sign_src.encode(), hashlib.sha256
        ).hexdigest()
        headers = {
            "X-BAPI-API-KEY": self._creds.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "Content-Type": "application/json",
        }
        with self._client() as client:
            resp = client.request(method, url, headers=headers, content=body)
            resp.raise_for_status()
            return resp.json()

    # -- credential verification --------------------------------------------

    def verify_credentials(self) -> ApiKeyPermissions:
        """Connect to the testnet, read the key's scopes, enforce the policy.

        Raises:
            WithdrawalPermissionError: the key can withdraw — never allowed.
            TradePermissionError: trade scope while read-only keys required.
            ExchangeError: the testnet could not be reached.
        """
        try:
            permissions = self._read_permissions()
        except httpx.HTTPError as exc:
            raise ExchangeError(f"sandbox: could not reach {self.exchange} testnet: {exc}") from exc

        # The single, non-negotiable safety gate.
        enforce_key_safety(permissions, self._config)
        self._creds = ApiCredentials(
            exchange=self._creds.exchange,
            api_key=self._creds.api_key,
            api_secret=self._creds.api_secret,
            permissions=permissions,
        )
        _log.info(
            "sandbox key verified (%s testnet): scopes=%s", self.exchange, permissions.describe()
        )
        return permissions

    def _read_permissions(self) -> ApiKeyPermissions:
        if self.exchange == "binance":
            acct = self._binance_signed("GET", "/api/v3/account")
            return ApiKeyPermissions(
                can_read=True,
                can_trade=bool(acct.get("canTrade", False)),
                can_withdraw=bool(acct.get("canWithdraw", False)),
                can_margin="MARGIN" in acct.get("permissions", []),
                can_futures=False,
                is_testnet=True,  # base URL is allowlist-guaranteed testnet
            )
        # Bybit: query the API key's own permission set.
        info = self._bybit_signed("GET", "/v5/user/query-api")
        perms = (info.get("result") or {}).get("permissions") or {}
        return ApiKeyPermissions(
            can_read=True,
            can_trade=bool(perms.get("Spot")),
            can_withdraw=bool(perms.get("Withdraw")),
            can_margin=bool(perms.get("Margin")),
            can_futures=bool(perms.get("Derivatives")),
            is_testnet=True,
        )

    # -- account / execution -------------------------------------------------

    def get_balances(self) -> Dict[str, float]:
        """Return free testnet balances per asset."""
        if self.exchange == "binance":
            acct = self._binance_signed("GET", "/api/v3/account")
            return {
                b["asset"]: float(b["free"])
                for b in acct.get("balances", [])
                if float(b["free"]) > 0
            }
        wallet = self._bybit_signed("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        out: Dict[str, float] = {}
        for acc in (wallet.get("result") or {}).get("list", []):
            for coin in acc.get("coin", []):
                out[coin["coin"]] = float(coin.get("walletBalance", 0.0) or 0.0)
        return out

    def place_test_order(self, symbol: str, side: Side, quantity: float) -> Fill:
        """Place a MARKET order on the exchange TESTNET and return the fill.

        Pre-conditions (all enforced): the key is verified, has trade scope,
        and read-only mode is off. The base URL is re-asserted as testnet
        immediately before the request.
        """
        perms = self._creds.permissions
        if perms is None:
            raise SandboxSafetyError(
                "credentials not verified — call " "verify_credentials() first"
            )
        if not perms.can_trade or self._config.require_read_only_keys:
            raise SandboxSafetyError(
                "testnet order placement requires a trade-scoped key and "
                "sandbox.require_read_only_keys: false"
            )
        _assert_testnet_url(self.base_url)  # final guard before sending

        if self.exchange == "binance":
            resp = self._binance_signed(
                "POST",
                "/api/v3/order",
                {
                    "symbol": symbol,
                    "side": side.value.upper(),
                    "type": "MARKET",
                    "quantity": f"{quantity:.8f}",
                },
            )
            return self._fill_from_binance(symbol, side, resp)
        resp = self._bybit_signed(
            "POST",
            "/v5/order/create",
            {
                "category": "spot",
                "symbol": symbol,
                "side": side.value.capitalize(),
                "orderType": "Market",
                "qty": f"{quantity:.8f}",
            },
        )
        return self._fill_from_bybit(symbol, side, quantity, resp)

    @staticmethod
    def _fill_from_binance(symbol: str, side: Side, resp: dict) -> Fill:
        fills = resp.get("fills", [])
        qty = sum(float(f["qty"]) for f in fills) or float(resp.get("executedQty", 0))
        if qty <= 0:
            raise ExchangeError(f"sandbox: testnet order not filled: {resp}")
        notional = sum(float(f["price"]) * float(f["qty"]) for f in fills)
        avg_price = notional / qty
        fee = sum(float(f.get("commission", 0.0)) for f in fills)
        return Fill(
            order_id=str(resp.get("orderId", "testnet")),
            symbol=symbol,
            side=side,
            quantity=qty,
            price=avg_price,
            requested_price=avg_price,
            fee=fee,
            slippage=0.0,
            timestamp=datetime.now(timezone.utc),
            is_partial=resp.get("status") == "PARTIALLY_FILLED",
        )

    @staticmethod
    def _fill_from_bybit(symbol: str, side: Side, quantity: float, resp: dict) -> Fill:
        result = resp.get("result") or {}
        order_id = str(result.get("orderId", "testnet"))
        # Bybit's create response does not include fill price; the caller
        # marks at the reference price. Treated as a clean testnet fill.
        return Fill(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=1.0,
            requested_price=1.0,
            fee=0.0,
            slippage=0.0,
            timestamp=datetime.now(timezone.utc),
            is_partial=False,
        )


def build_sandbox_client(config: AppConfig) -> Optional[SandboxExchangeClient]:
    """Construct and verify a sandbox client, or return None.

    Returns ``None`` (the platform stays in pure paper mode) when sandbox is
    disabled, network is off, or no testnet credentials are configured.
    """
    sb = config.sandbox
    if not sb.enabled:
        return None
    if not config.runtime.allow_network:
        _log.warning(
            "sandbox enabled but runtime.allow_network is false — " "staying in paper mode"
        )
        return None
    credentials = load_sandbox_credentials(sb.exchange)
    if credentials is None:
        _log.warning(
            "sandbox enabled but no %s testnet keys configured — " "staying in paper mode",
            sb.exchange,
        )
        return None
    client = SandboxExchangeClient(credentials, sb)
    client.verify_credentials()  # raises on any unsafe key
    return client
