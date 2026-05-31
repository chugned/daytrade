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

from .api_keys import (
    KeyPermissions,
    WithdrawalPermissionForbidden,
    assert_trade_only,
    inspect_key,
)
from .instance_lock import SingleInstanceLock, SingleInstanceLockError
from .notify import (
    Level,
    LogNotifier,
    NtfyNotifier,
    Notifier,
    TelegramNotifier,
    build_notifier,
)
from .order_ids import OrderIDRegistry, generate_client_order_id
from .reconciliation import ReconciliationReport, reconcile_paper_state
from .remote_log import (
    RemoteLogHandler,
    attach_remote_handler_from_env,
    attach_rotating_file_handler,
)

__all__ = [
    "KeyPermissions",
    "WithdrawalPermissionForbidden",
    "assert_trade_only",
    "inspect_key",
    "SingleInstanceLock",
    "SingleInstanceLockError",
    "OrderIDRegistry",
    "generate_client_order_id",
    "ReconciliationReport",
    "reconcile_paper_state",
    "RemoteLogHandler",
    "attach_remote_handler_from_env",
    "attach_rotating_file_handler",
    "Notifier",
    "Level",
    "LogNotifier",
    "TelegramNotifier",
    "NtfyNotifier",
    "build_notifier",
]
