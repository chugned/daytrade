"""Operations layer — production-readiness building blocks.

The ``ops`` package collects the engineering safety mechanisms a system needs
before it can responsibly handle real money: a single-instance lock,
staleness guards, idempotent-order helpers, startup reconciliation, push
notifications, and trade-only-key validation.

Each piece is paper/simulation-friendly today and *also* the same primitive
the eventual live-execution path will use. Importing from ``daytrade.ops``
does not enable live trading — that structural guarantee is unchanged.
"""

from __future__ import annotations

from .instance_lock import SingleInstanceLock, SingleInstanceLockError
from .order_ids import OrderIDRegistry, generate_client_order_id
from .reconciliation import ReconciliationReport, reconcile_paper_state

__all__ = [
    "SingleInstanceLock",
    "SingleInstanceLockError",
    "OrderIDRegistry",
    "generate_client_order_id",
    "ReconciliationReport",
    "reconcile_paper_state",
]
