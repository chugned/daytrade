"""Binance perpetual-futures funding-rate signal.

Background: every 8 hours Binance settles funding between perpetual-futures
longs and shorts. The *funding rate* is the price of holding that position
for one settlement: positive means longs pay shorts (perp trades above
spot — crowded long); negative means shorts pay longs (crowded short).

Extreme funding rates are a well-documented sentiment signal:

* **Extreme positive funding** = leveraged longs are crowded. The cohort
  is about to be charged for holding; a long squeeze / mean-reversion
  pullback often follows. *For our long-only trend bot, this is a
  pause signal.*
* **Extreme negative funding** = shorts are crowded. A short-squeeze
  rally often follows. *Mildly favourable for our long bias.*

This module ships:

* :func:`fetch_current_funding_rate` — read the live premium index for a
  perpetual symbol via Binance's public futures API (read-only, no key).
* :func:`fetch_funding_history`     — historical 8-hour funding payments
  for sweep / validation.
* :func:`extreme_funding_blocks_buy`— interpretation: should this rate
  block a new BUY?

All values are paper-safe; no live order code uses these. Configurable
thresholds live in :class:`GatingConfig`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx

_FUTURES_BASE = "https://fapi.binance.com"

#: Spot symbol BTCUSDT maps to the perp symbol BTCUSDT (same string on Binance).
#: Symbols that don't have a perp listing simply return None.
_PREMIUM_PATH = "/fapi/v1/premiumIndex"
_HISTORY_PATH = "/fapi/v1/fundingRate"

# Small per-process cache. Funding rates update continuously but a 5-min
# stale reading is more than fine for a 1-min-cycle bot.
_CACHE: Dict[str, Tuple[float, float]] = {}
_CACHE_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class FundingSnapshot:
    """A point-in-time funding-rate reading."""

    symbol: str
    rate: float  # current premium index, as a fraction (0.0001 = 0.01%)
    mark_price: float
    timestamp_ms: int


def _spot_to_perp(symbol: str) -> str:
    """Map a spot symbol (BTCUSDT) to its perp counterpart on Binance.

    For the watchlist we care about, the perp string equals the spot string.
    """
    return symbol.upper()


def fetch_current_funding_rate(
    symbol: str,
    *,
    client: Optional[httpx.Client] = None,
    base_url: str = _FUTURES_BASE,
    use_cache: bool = True,
) -> Optional[FundingSnapshot]:
    """Return the live funding rate for ``symbol``, or None if the perp doesn't exist.

    Caches per-symbol for ``_CACHE_TTL_SECONDS`` so repeated calls inside one
    cycle are free. Network failures return None rather than raising — a
    missing optional signal must never kill a trading cycle.
    """
    perp = _spot_to_perp(symbol)
    now = time.time()
    if use_cache and perp in _CACHE:
        ts, rate = _CACHE[perp]
        if now - ts < _CACHE_TTL_SECONDS:
            return FundingSnapshot(perp, rate, mark_price=0.0, timestamp_ms=int(ts * 1000))

    own = client is None
    client = client or httpx.Client(timeout=8.0)
    try:
        resp = client.get(f"{base_url}{_PREMIUM_PATH}", params={"symbol": perp})
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:  # noqa: BLE001 - optional feed; never crash callers
        return None
    finally:
        if own:
            client.close()

    try:
        rate = float(data["lastFundingRate"])
        mark = float(data["markPrice"])
        ts_ms = int(data.get("time") or now * 1000)
    except (KeyError, TypeError, ValueError):
        return None
    _CACHE[perp] = (now, rate)
    return FundingSnapshot(symbol=perp, rate=rate, mark_price=mark, timestamp_ms=ts_ms)


def fetch_funding_history(
    symbol: str,
    *,
    limit: int = 100,
    client: Optional[httpx.Client] = None,
    base_url: str = _FUTURES_BASE,
) -> List[FundingSnapshot]:
    """Return up to ``limit`` historical 8-hour funding records for ``symbol``.

    Used by the sweep script to validate the signal — measure forward
    returns conditional on the funding regime.
    """
    perp = _spot_to_perp(symbol)
    own = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        resp = client.get(
            f"{base_url}{_HISTORY_PATH}",
            params={"symbol": perp, "limit": max(1, min(1000, int(limit)))},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:  # noqa: BLE001
        return []
    finally:
        if own:
            client.close()

    out: List[FundingSnapshot] = []
    for row in data:
        try:
            out.append(
                FundingSnapshot(
                    symbol=perp,
                    rate=float(row["fundingRate"]),
                    mark_price=float(row.get("markPrice", 0.0) or 0.0),
                    timestamp_ms=int(row["fundingTime"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def extreme_funding_blocks_buy(
    rate: float,
    *,
    extreme_positive: float = 0.0003,
    extreme_negative: float = -0.0010,
) -> Tuple[bool, str]:
    """Should this funding rate veto a new BUY?

    Defaults are wider than typical "normal" funding (~0.0001 = 0.01%):

    * ``extreme_positive`` (default 0.0003 = 0.03%): above this, longs are
      crowded → block BUYs.
    * ``extreme_negative`` (default −0.0010 = −0.10%): below this is a
      genuine short squeeze setup that may be the *best* time to buy →
      explicitly do NOT block; in fact this is informational.

    Returns ``(block, reason)``. Thresholds should be sweep-validated on
    the watchlist before being relied on in production
    (see ``scripts/sweep_funding_gate.py``).
    """
    if rate >= extreme_positive:
        return True, (
            f"extreme positive funding {rate*100:.3f}% — " f"longs crowded, pullback risk"
        )
    if rate <= extreme_negative:
        return False, (
            f"extreme negative funding {rate*100:.3f}% — " "short squeeze setup, BUY allowed"
        )
    return False, f"funding {rate*100:.3f}% within normal range"
