"""The canonical demo scenario.

PLAN.md specifies a fixed scenario the platform must reproduce: a calm market
with BTC near 103,434, a bullish macro backdrop, an oversold RSI, and a
sell-heavy orderbook — resolving to a BUY at ~0.60 confidence.

This module builds that scenario deterministically as real market data
(candles + orderbook) so the *actual pipeline* — not a hard-coded answer —
produces the decision. It is a "buy-the-dip" setup: a long, calm uptrend, a
sharp multi-bar pullback (which drives RSI down), then a small bounce.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from .exchanges.mock import build_orderbook
from .models import OHLCV, OrderBookSnapshot

# The canonical scenario constants from PLAN.md.
DEMO_SYMBOL = "BTCUSDT"
DEMO_REFERENCE_PRICE = 103_434.0
DEMO_MACRO_SCENARIO = "risk_on"
# "30% more sellers": ask depth = 1.3x bid depth  =>  imbalance = -0.13043.
DEMO_ORDERBOOK_IMBALANCE = -0.130435

_BAR = timedelta(minutes=1)
_WICK = 0.00018  # tiny wicks keep ATR low so the volatility floor binds


def _build_closes() -> List[float]:
    """Construct the deterministic close-price path for the demo.

    Segments (per-bar simple returns):
      * uptrend  — long, calm advance
      * pullback — sharp enough to push RSI(14) into oversold territory
      * bounce   — a small recovery off the low
    """
    returns: List[float] = []
    returns += [0.00100] * 150  # calm uptrend
    returns += [-0.00160] * 24  # sharp pullback -> RSI oversold (~25)
    returns += [0.00060] * 5  # small bounce off the low

    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    # Rescale the whole path so the final close is exactly the reference price.
    scale = DEMO_REFERENCE_PRICE / closes[-1]
    return [c * scale for c in closes]


def build_demo_candles() -> List[OHLCV]:
    """Return the deterministic OHLCV series for the demo scenario."""
    closes = _build_closes()
    n = len(closes)
    end_time = datetime(2026, 5, 17, tzinfo=timezone.utc)
    start_time = end_time - _BAR * (n - 1)

    candles: List[OHLCV] = []
    prev_close = closes[0]
    for i, close in enumerate(closes):
        open_ = prev_close
        hi = max(open_, close) * (1.0 + _WICK)
        lo = min(open_, close) * (1.0 - _WICK)
        candles.append(
            OHLCV(
                symbol=DEMO_SYMBOL,
                timestamp=start_time + _BAR * i,
                open=round(open_, 2),
                high=round(hi, 2),
                low=round(lo, 2),
                close=round(close, 2),
                volume=1000.0,
            )
        )
        prev_close = close
    return candles


def build_demo_orderbook() -> OrderBookSnapshot:
    """Return the deterministic, sell-heavy orderbook for the demo scenario."""
    return build_orderbook(
        symbol=DEMO_SYMBOL,
        mid_price=DEMO_REFERENCE_PRICE,
        exchange="demo",
        depth=20,
        spread_bps=4.0,
        base_quantity=1.0,
        imbalance=DEMO_ORDERBOOK_IMBALANCE,
        jitter=0.0,  # exact, smooth book for a reproducible scenario
    )
