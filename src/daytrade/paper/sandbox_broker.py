"""Sandbox broker — paper bookkeeping with optional testnet execution.

``SandboxBroker`` wraps a :class:`PaperBroker` (which keeps all the portfolio,
PnL and trade-log bookkeeping) and an optional verified
:class:`SandboxExchangeClient`.

Two execution modes:

* ``simulated`` — orders are filled by the local simulator. This is the
  default and the only mode reachable with a read-only testnet key.
* ``testnet`` — orders are placed on the exchange TESTNET (play money).
  Reachable only with a verified trade-scoped testnet key *and*
  ``sandbox.require_read_only_keys: false``.

There is no third mode. Real-money execution does not exist here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from ..config.schema import RiskConfig
from ..exchanges.sandbox import SandboxExchangeClient
from ..models import Fill, PortfolioSnapshot, Position, Side
from ..runtime import get_logger
from .broker import PaperBroker

_log = get_logger("paper.sandbox_broker")


class SandboxBroker:
    """A broker that books trades on a paper portfolio, optionally mirroring
    execution onto an exchange testnet."""

    def __init__(
        self, paper_broker: PaperBroker, client: Optional[SandboxExchangeClient] = None
    ) -> None:
        self._paper = paper_broker
        self._client = client

    @property
    def execution_mode(self) -> str:
        """``'testnet'`` if real testnet orders will be placed, else ``'simulated'``."""
        if self._client is None:
            return "simulated"
        perms = self._client._creds.permissions  # verified at build time
        if perms is not None and perms.can_trade:
            return "testnet"
        return "simulated"

    @property
    def is_testnet_execution(self) -> bool:
        return self.execution_mode == "testnet"

    def submit_market_order(
        self,
        order_id: str,
        symbol: str,
        side: Side,
        quantity: float,
        reference_price: float,
        available_liquidity: float,
        risk_config: RiskConfig,
        timestamp: datetime,
    ) -> Fill:
        """Execute an order — on the testnet if enabled, else simulated.

        Either way the resulting fill is booked into the paper portfolio so
        reporting, PnL and trade logs work identically across modes.
        """
        if self.is_testnet_execution:
            _log.info("placing TESTNET %s order: %s %.6f %s", side.value, symbol, quantity, symbol)
            fill = self._client.place_test_order(symbol, side, quantity)
            self._paper.apply_external_fill(fill)
            return fill
        # Simulated execution (default / read-only key path).
        return self._paper.submit_market_order(
            order_id,
            symbol,
            side,
            quantity,
            reference_price,
            available_liquidity,
            risk_config,
            timestamp,
        )

    # -- portfolio passthrough ----------------------------------------------

    @property
    def cash(self) -> float:
        return self._paper.cash

    @property
    def realized_pnl(self) -> float:
        return self._paper.realized_pnl

    @property
    def closed_trades(self):
        return self._paper.closed_trades

    @property
    def fills(self):
        return self._paper.fills

    def position(self, symbol: str) -> Position:
        return self._paper.position(symbol)

    def has_position(self, symbol: str) -> bool:
        return self._paper.has_position(symbol)

    def equity(self, mark_prices: Dict[str, float]) -> float:
        return self._paper.equity(mark_prices)

    def snapshot(self, timestamp: datetime, mark_prices: Dict[str, float]) -> PortfolioSnapshot:
        return self._paper.snapshot(timestamp, mark_prices)

    def testnet_balances(self) -> Dict[str, float]:
        """Read live balances from the testnet account (if connected)."""
        if self._client is None:
            return {}
        return self._client.get_balances()
