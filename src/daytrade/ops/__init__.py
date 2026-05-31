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

__all__ = [
    "SingleInstanceLock",
    "SingleInstanceLockError",
]
