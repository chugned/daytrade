"""Live-trading scaffolding.

THIS PACKAGE DOES NOT PLACE ORDERS BY DEFAULT.

It contains the infrastructure required to connect the daytrade engine
to a real exchange, but every piece ships behind:

  1. A ``dry_run: True`` default on :class:`LiveConfig`.
  2. The :class:`daytrade.config.schema.SafetyConfig` validator which
     refuses to load a config with ``live_trading_enabled = true``.
  3. A trade-only API-key assertion at startup
     (:func:`daytrade.ops.api_keys.assert_trade_only`).

Going live is a deliberate, multi-step opt-in — not a flag flip.
"""

from .broker import LiveBroker, LiveBrokerError
from .exchange import (
    Exchange,
    ExchangeOrder,
    MockExchange,
    OrderRejected,
    OrderState,
)
from .config import LiveConfig

__all__ = [
    "Exchange",
    "ExchangeOrder",
    "MockExchange",
    "OrderState",
    "OrderRejected",
    "LiveBroker",
    "LiveBrokerError",
    "LiveConfig",
]
