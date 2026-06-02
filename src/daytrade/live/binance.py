"""BinanceExchange — ccxt-backed implementation of the Exchange protocol.

Layered carefully because this is the file that, if misused, can move
real money. Three guard rails:

  1. **No writes by default.** Construction requires
     ``writes_enabled=False`` unless the caller explicitly passes
     ``writes_enabled=True`` *and* the SafetyConfig live-trading rails
     are open (the LiveBroker checks those). Until then,
     ``place_market_order`` raises ``ShadowModeError``.

  2. **Trade-only API key required.** On construction we call
     ``ops.api_keys.assert_trade_only`` which queries the key's
     permissions and raises if it can withdraw. A withdraw-enabled
     key is never acceptable; this fails closed.

  3. **No silent retries on writes.** A failed write raises and the
     caller (LiveBroker) treats it as not-taken. We retry only the
     read paths, and only on network-transient errors.

ccxt is an *optional* dependency. The import happens at construction;
the rest of the package works without it.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, List, Optional

from ..models import Fill, Position, Side
from ..ops.api_keys import (
    KeyPermissions,
    assert_trade_only,
    inspect_key,
)
from .exchange import (
    ExchangeOrder,
    ExchangeUnreachable,
    OrderRejected,
    OrderState,
)

_log = logging.getLogger("live.binance")


class ShadowModeError(RuntimeError):
    """A write was attempted while the adapter is in read-only/shadow mode."""


class BinanceExchange:
    """Binance spot adapter for :class:`daytrade.live.exchange.Exchange`.

    Defaults to **shadow mode** (writes disabled). Reads of balance /
    positions / open orders work; ``place_market_order`` raises until
    writes are explicitly enabled by the caller.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        writes_enabled: bool = False,
        client: Any = None,
        rate_limit: bool = True,
        permissions: Optional[KeyPermissions] = None,
    ) -> None:
        """Construct a Binance adapter.

        Parameters
        ----------
        api_key, api_secret : str
            Binance trade-only API credentials. Required.
        writes_enabled : bool
            False by default — read paths work, writes raise
            :class:`ShadowModeError`. Flip with :meth:`enable_writes`
            after the SafetyConfig opt-in is confirmed.
        client : ccxt.binance, optional
            Inject a pre-built / mocked client. If None, builds a real
            ccxt client (which requires the ``ccxt`` package).
        permissions : KeyPermissions, optional
            Pre-validated key permissions. If None, we call Binance's
            ``/sapi/v1/account/apiRestrictions`` endpoint via
            :func:`daytrade.ops.api_keys.inspect_key`. The permissions
            are then asserted via :func:`assert_trade_only`. The check
            cannot be bypassed.
        """
        if not api_key or not api_secret:
            raise ValueError("api_key and api_secret must be non-empty")
        # Validate the key BEFORE building/storing the ccxt client. Failing
        # the trade-only check raises and the adapter is never usable.
        if permissions is None:
            permissions = inspect_key(api_key, api_secret)
        assert_trade_only(permissions)  # raises on any non-safe permission
        self._permissions = permissions

        if client is None:
            try:
                import ccxt  # noqa: PLC0415 — optional dep, imported lazily
            except ImportError as exc:
                raise RuntimeError(
                    "BinanceExchange requires the optional 'ccxt' package. "
                    "Install it: pip install ccxt"
                ) from exc
            client = ccxt.binance(
                {
                    "apiKey": api_key,
                    "secret": api_secret,
                    "enableRateLimit": rate_limit,
                    "options": {"defaultType": "spot"},
                }
            )
        self._client = client
        self._writes_enabled = bool(writes_enabled)

    @property
    def writes_enabled(self) -> bool:
        return self._writes_enabled

    def enable_writes(self) -> None:
        """Flip writes on after the caller has confirmed the SafetyConfig
        opt-in. Idempotent."""
        self._writes_enabled = True
        _log.warning(
            "BinanceExchange writes ENABLED — orders will hit the " "real exchange on next submit."
        )

    # ----- read paths -----

    def get_balance(self, asset: str) -> float:
        """Return the FREE balance for ``asset`` (e.g. 'USDT')."""
        try:
            bal = self._client.fetch_balance()
        except Exception as exc:  # noqa: BLE001 — map all to our error type
            raise ExchangeUnreachable(f"fetch_balance failed: {exc}") from exc
        free = (bal.get(asset, {}) or {}).get("free", 0.0)
        try:
            return float(free or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def get_position(self, symbol: str) -> Position:
        """Return the spot position for ``symbol`` (quantity of base asset)."""
        base = symbol.replace("USDT", "")
        try:
            bal = self._client.fetch_balance()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeUnreachable(f"fetch_balance failed: {exc}") from exc
        total = (bal.get(base, {}) or {}).get("total", 0.0)
        try:
            qty = float(total or 0.0)
        except (TypeError, ValueError):
            qty = 0.0
        # Avg entry price is not exposed by Binance for spot in fetch_balance;
        # the broker tracks it from its own fills. We return qty + 0 avg here;
        # the LiveBroker uses this for reconciliation, where only qty matters.
        return Position(symbol=symbol, quantity=qty, avg_entry_price=0.0)

    def list_open_orders(self, symbol: Optional[str] = None) -> List[OrderState]:
        try:
            if symbol:
                ccxt_sym = self._to_ccxt_symbol(symbol)
                raw = self._client.fetch_open_orders(ccxt_sym)
            else:
                raw = self._client.fetch_open_orders()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeUnreachable(f"fetch_open_orders failed: {exc}") from exc
        return [self._to_order_state(o) for o in raw]

    # ----- write paths -----

    def place_market_order(self, order: ExchangeOrder) -> Fill:
        if not self._writes_enabled:
            raise ShadowModeError(
                "BinanceExchange is in shadow (read-only) mode. To enable "
                "writes the caller must (1) confirm LiveConfig.dry_run=False, "
                "(2) confirm the SafetyConfig opt-in fields, and (3) call "
                "enable_writes() explicitly. No order placed."
            )
        ccxt_sym = self._to_ccxt_symbol(order.symbol)
        side_str = "buy" if order.side is Side.BUY else "sell"
        try:
            resp = self._client.create_order(
                ccxt_sym,
                "market",
                side_str,
                order.quantity,
                None,
                {"newClientOrderId": order.client_order_id},
            )
        except Exception as exc:  # noqa: BLE001 — paranoid wrap
            # Distinguish reject vs unreachable so the broker can react.
            msg = str(exc)
            if any(
                s in msg.lower()
                for s in (
                    "insufficient",
                    "lot size",
                    "min notional",
                    "filter failure",
                    "duplicate clientorderid",
                )
            ):
                raise OrderRejected(f"binance rejected: {msg}") from exc
            raise ExchangeUnreachable(f"create_order failed: {msg}") from exc
        return self._to_fill(order, resp)

    def cancel_order(self, symbol: str, client_order_id: str) -> None:
        if not self._writes_enabled:
            raise ShadowModeError("cancel_order requires writes_enabled=True")
        ccxt_sym = self._to_ccxt_symbol(symbol)
        try:
            self._client.cancel_order(
                client_order_id, ccxt_sym, {"origClientOrderId": client_order_id}
            )
        except Exception as exc:  # noqa: BLE001
            raise ExchangeUnreachable(f"cancel_order failed: {exc}") from exc

    # ----- helpers -----

    @staticmethod
    def _to_ccxt_symbol(symbol: str) -> str:
        """Convert 'BTCUSDT' -> 'BTC/USDT' (ccxt format)."""
        if "/" in symbol:
            return symbol
        if symbol.endswith("USDT"):
            return symbol[:-4] + "/USDT"
        if symbol.endswith("BUSD"):
            return symbol[:-4] + "/BUSD"
        # Last resort — let ccxt validate
        return symbol

    @staticmethod
    def _to_fill(order: ExchangeOrder, resp: dict) -> Fill:
        """Map a ccxt order response into our internal Fill."""
        try:
            filled = float(resp.get("filled") or 0.0)
            avg = resp.get("average") or resp.get("price") or order.reference_price
            avg = float(avg or order.reference_price)
            fee_info = resp.get("fee") or {}
            fee = float(fee_info.get("cost", 0.0) or 0.0)
        except (TypeError, ValueError) as exc:
            raise ExchangeUnreachable(f"unparseable order response: {resp!r} ({exc})") from exc
        if filled <= 0:
            raise OrderRejected(f"zero-filled order response: {resp!r}")
        ts_ms = resp.get("timestamp")
        ts = (
            datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            if isinstance(ts_ms, (int, float))
            else datetime.now(timezone.utc)
        )
        return Fill(
            order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=filled,
            price=avg,
            requested_price=order.reference_price,
            fee=fee,
            slippage=abs(avg - order.reference_price),
            timestamp=ts,
            is_partial=(filled < order.quantity - 1e-9),
        )

    @staticmethod
    def _to_order_state(o: dict) -> OrderState:
        side_s = (o.get("side") or "buy").lower()
        side = Side.BUY if side_s == "buy" else Side.SELL
        status_raw = (o.get("status") or "open").lower()
        # ccxt statuses: open, closed, canceled, expired, rejected
        status = {
            "open": "open",
            "closed": "filled",
            "canceled": "cancelled",
            "expired": "cancelled",
            "rejected": "rejected",
        }.get(status_raw, status_raw)
        return OrderState(
            client_order_id=str(o.get("clientOrderId") or o.get("id") or ""),
            exchange_order_id=str(o.get("id") or ""),
            symbol=str(o.get("symbol") or "").replace("/", ""),
            side=side,
            quantity_requested=float(o.get("amount") or 0.0),
            quantity_filled=float(o.get("filled") or 0.0),
            avg_fill_price=float(o.get("average") or o.get("price") or 0.0),
            status=status,
        )


def from_env(
    *,
    api_key_env: str,
    api_secret_env: str,
    writes_enabled: bool = False,
    permissions: Optional[KeyPermissions] = None,
) -> BinanceExchange:
    """Construct a BinanceExchange from environment variables. Never
    persists the secret; reads it once and hands it to ccxt."""
    key = os.environ.get(api_key_env, "").strip()
    sec = os.environ.get(api_secret_env, "").strip()
    if not key or not sec:
        raise RuntimeError(
            f"API credentials not found: env vars {api_key_env!r} "
            f"and {api_secret_env!r} must be set."
        )
    return BinanceExchange(
        api_key=key, api_secret=sec, writes_enabled=writes_enabled, permissions=permissions
    )
