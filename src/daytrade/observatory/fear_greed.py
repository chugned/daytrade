"""Crypto Fear & Greed Index — contrarian sentiment regime tag.

The index (alternative.me, daily) compresses several market-wide signals
(volatility, momentum, social, dominance, surveys) into a 0–100 score where
0 is "extreme fear" and 100 is "extreme greed". Empirically, the extremes
tend to mark *contrarian* turning points: tops at extreme greed, bottoms at
extreme fear. This module is the small, well-isolated piece that fetches
the score, caches it, and turns it into a gate decision.

Design properties (intentional):

- **Read-only**: pulls the public API; no auth, no keys, no orders.
- **Fail-safe**: any network/parse failure returns ``None`` / "ALLOW" so the
  bot keeps trading on its primary signals rather than locking up.
- **Cached**: the index updates once per day, so a single in-process cache
  with a configurable TTL (default 1h) is plenty.
- **Pure helpers**: the threshold logic is a function, not a class — easy
  to test, easy to sweep.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_API_URL = "https://api.alternative.me/fng/"
_DEFAULT_TIMEOUT_S = 5.0
_DEFAULT_CACHE_TTL_S = 3600.0  # daily index — refresh hourly is generous


@dataclass(frozen=True)
class FearGreedReading:
    """A single Fear & Greed observation."""

    value: float  # 0–100
    classification: str  # e.g. "Extreme Fear", "Greed", ...
    fetched_at: float  # unix epoch when this reading was retrieved


# Module-level cache. A single reading at a time is sufficient — the index
# is a global, daily score; there is nothing per-symbol to track.
_cache: Optional[FearGreedReading] = None


def _fetch_raw(timeout_s: float) -> Optional[dict]:
    try:
        req = urllib.request.Request(
            _API_URL,
            headers={"User-Agent": "daytrade-paper/1.0 (+research only)"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        # Any network or parse failure is non-fatal: we run on the primary
        # signals and just don't apply the sentiment gate this cycle.
        logger.info("fear_greed: fetch failed (%s); using last cache if any", exc)
        return None
    return payload


def _parse(payload: dict, fetched_at: float) -> Optional[FearGreedReading]:
    """Pull the first entry out of the documented response shape."""
    try:
        entries = payload.get("data") or []
        if not entries:
            return None
        first = entries[0]
        value = float(first["value"])
        classification = str(first.get("value_classification") or "Unknown")
    except (KeyError, TypeError, ValueError) as exc:
        logger.info("fear_greed: unexpected payload shape (%s)", exc)
        return None
    if not (0.0 <= value <= 100.0):
        logger.info("fear_greed: out-of-range value %r", value)
        return None
    return FearGreedReading(
        value=value,
        classification=classification,
        fetched_at=fetched_at,
    )


def fetch_fear_greed(
    *,
    cache_ttl_s: float = _DEFAULT_CACHE_TTL_S,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    now: Optional[float] = None,
) -> Optional[FearGreedReading]:
    """Return the current Crypto Fear & Greed reading, or ``None``.

    Uses a module-level TTL cache so repeated calls within ``cache_ttl_s``
    return without hitting the network. ``now`` is injectable for testing.
    """
    global _cache
    now_ts = time.time() if now is None else now
    if _cache is not None and (now_ts - _cache.fetched_at) < cache_ttl_s:
        return _cache
    payload = _fetch_raw(timeout_s)
    if payload is None:
        return _cache  # last good reading or None
    reading = _parse(payload, fetched_at=now_ts)
    if reading is None:
        return _cache
    _cache = reading
    return reading


def reset_cache() -> None:
    """Test helper — purge the cached reading."""
    global _cache
    _cache = None


def extreme_greed_blocks_buy(
    reading: Optional[FearGreedReading],
    *,
    threshold: float,
) -> bool:
    """Should a fresh BUY be blocked because the index is in extreme greed?"""
    if reading is None:
        return False
    return reading.value >= threshold


def extreme_fear_blocks_sell(
    reading: Optional[FearGreedReading],
    *,
    threshold: float,
) -> bool:
    """Should a fresh SELL be blocked because the index is in extreme fear?"""
    if reading is None:
        return False
    return reading.value <= threshold


def regime_label(reading: Optional[FearGreedReading]) -> str:
    """Coarse 5-bucket regime label, useful for dashboards / metrics."""
    if reading is None:
        return "UNKNOWN"
    v = reading.value
    if v <= 20:
        return "EXTREME_FEAR"
    if v <= 40:
        return "FEAR"
    if v < 60:
        return "NEUTRAL"
    if v < 80:
        return "GREED"
    return "EXTREME_GREED"
