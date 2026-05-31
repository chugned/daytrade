"""Idempotent order client-IDs — the same order can never accidentally fire twice.

Most live-trading incidents start the same way: a transient error makes the bot
retry an order that *already filled* on the exchange, doubling the position.
The fix is a deterministic ``client_order_id`` per (symbol, side, time bucket,
intent) and a local registry that rejects the same id a second time.

The exchange-side de-duplication that real brokers offer is a complementary
layer; the local one runs first and is the only one we control.

Paper / simulation only here — this is a primitive ready for the future live
execution path, not a switch that enables it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

#: Maximum length Binance accepts for ``clientOrderId``; we stay safely below.
_MAX_CLIENT_ID_LEN = 32


def generate_client_order_id(
    symbol: str,
    side: str,
    timestamp: Optional[datetime] = None,
    bucket_seconds: int = 60,
    intent: str = "entry",
) -> str:
    """Return a deterministic client-order-id for one trading intent.

    Two calls with the *same* inputs (symbol/side/intent in the same time
    bucket) return the *same* id — so a retry never creates a second order.
    Crossing into the next bucket produces a fresh id, which is correct: a
    new "moment" justifies a new order.

    Args:
        symbol: trading pair (e.g. ``"BTCUSDT"``).
        side: ``"buy"``/``"sell"``.
        timestamp: defaults to ``datetime.now(timezone.utc)``.
        bucket_seconds: width of a time bucket. 60s matches a typical
            decision cadence — two decisions in the same minute won't fire
            two orders.
        intent: ``"entry"`` / ``"exit"`` / ``"stop"`` / ``"target"`` etc.
    """
    if not symbol or not side or not intent:
        raise ValueError("symbol, side and intent are required")
    if bucket_seconds < 1:
        raise ValueError("bucket_seconds must be >= 1")

    ts = timestamp or datetime.now(timezone.utc)
    epoch = int(ts.timestamp())
    bucket = epoch // bucket_seconds

    raw = f"{symbol.upper()}|{side.lower()}|{intent.lower()}|{bucket}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    short_symbol = symbol.upper().replace("USDT", "")[:6]
    short_side = "B" if side.lower().startswith("b") else "S"
    short_intent = intent.lower()[:3]
    cid = f"dt-{short_symbol}-{short_side}-{short_intent}-{bucket}-{digest}"
    return cid[:_MAX_CLIENT_ID_LEN]


@dataclass
class OrderIDRegistry:
    """In-process registry that rejects duplicate client-order-ids.

    ``register(id)`` returns True the first time an id is seen, False every
    time after. Callers refuse to send orders whose ids the registry rejects.
    Lives for the process lifetime; for cross-restart safety, pair this with
    exchange-side de-duplication (Binance honours clientOrderId for that).
    """

    _seen: Dict[str, datetime] = field(default_factory=dict)

    def register(self, client_order_id: str) -> bool:
        """Try to register an id. True = first time; False = duplicate."""
        if not client_order_id:
            raise ValueError("empty client_order_id")
        if client_order_id in self._seen:
            return False
        self._seen[client_order_id] = datetime.now(timezone.utc)
        return True

    def has(self, client_order_id: str) -> bool:
        return client_order_id in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def clear(self) -> None:
        self._seen.clear()
