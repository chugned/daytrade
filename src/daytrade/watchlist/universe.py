"""Deterministic mock market data for the asset universe.

When running offline (the default), the watchlist screener needs per-symbol
market data. This module synthesizes it deterministically from a profile
table — healthy majors plus a couple of deliberately unhealthy assets so the
rejection filters are visibly exercised in the demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from ..exchanges.mock import build_orderbook, generate_random_walk
from ..models import OHLCV, OrderBookSnapshot, PriceTick


@dataclass(frozen=True)
class AssetProfile:
    """A deterministic synthetic-market profile for one asset."""

    price: float
    volume_24h_usd: float
    spread_bps: float
    book_base_qty: float  # per-level size -> drives book notional
    book_depth: int  # number of price levels per side
    pump_1h: float = 0.0  # final-hour price spike (fraction), 0 = none


# Healthy majors + two unhealthy assets that exist to demonstrate rejection.
_PROFILES: Dict[str, AssetProfile] = {
    "BTCUSDT": AssetProfile(62_000.0, 2.4e10, 2.0, 1.2, 20),
    "ETHUSDT": AssetProfile(3_050.0, 1.1e10, 2.5, 22.0, 20),
    "SOLUSDT": AssetProfile(152.0, 3.2e9, 3.5, 430.0, 20),
    "BNBUSDT": AssetProfile(585.0, 9.0e8, 3.0, 120.0, 20),
    "ADAUSDT": AssetProfile(0.46, 3.4e8, 4.0, 1.6e5, 20),
    # --- deliberately unhealthy (for demonstrating the filters) ---
    "THINUSDT": AssetProfile(0.012, 8.0e5, 35.0, 5_000.0, 3),  # thin + wide
    "PUMPUSDT": AssetProfile(2.4, 6.0e8, 6.0, 4.0e4, 20, pump_1h=0.42),
}

_DEFAULT = AssetProfile(10.0, 2.0e8, 4.0, 8_000.0, 20)


def _profile(symbol: str) -> AssetProfile:
    return _PROFILES.get(symbol.upper(), _DEFAULT)


def build_mock_asset_data(
    symbol: str,
    seed: int = 42,
) -> Tuple[PriceTick, OrderBookSnapshot, List[OHLCV]]:
    """Build deterministic (tick, orderbook, candles) for one symbol."""
    profile = _profile(symbol)
    candles = generate_random_walk(
        symbol=symbol,
        n_bars=180,
        start_price=profile.price * 0.97,
        drift=0.0003,
        volatility=0.003,
        seed=seed + hash(symbol) % 1000,
    )
    # Rescale so the final close matches the profile price.
    scale = profile.price / candles[-1].close
    candles = [
        OHLCV(
            symbol=symbol,
            timestamp=c.timestamp,
            open=round(c.open * scale, 6),
            high=round(c.high * scale, 6),
            low=round(c.low * scale, 6),
            close=round(c.close * scale, 6),
            volume=c.volume,
        )
        for c in candles
    ]
    # Inject a pump: lift the last hour of candles by pump_1h.
    if profile.pump_1h > 0:
        spiked: List[OHLCV] = []
        n = len(candles)
        for i, c in enumerate(candles):
            if i >= n - 60:
                step = profile.pump_1h * (i - (n - 60)) / 60.0
                f = 1.0 + step
                spiked.append(
                    OHLCV(
                        symbol=symbol,
                        timestamp=c.timestamp,
                        open=round(c.open * f, 6),
                        high=round(c.high * f, 6),
                        low=round(c.low * f, 6),
                        close=round(c.close * f, 6),
                        volume=c.volume,
                    )
                )
            else:
                spiked.append(c)
        candles = spiked

    last = candles[-1]
    tick = PriceTick(
        symbol=symbol,
        exchange="mock",
        price=last.close,
        timestamp=last.timestamp,
        volume_24h=profile.volume_24h_usd,
    )
    orderbook = build_orderbook(
        symbol=symbol,
        mid_price=last.close,
        exchange="mock",
        depth=profile.book_depth,
        spread_bps=profile.spread_bps,
        base_quantity=profile.book_base_qty,
        jitter=0.0,
    )
    return tick, orderbook, candles


def build_mock_universe(
    symbols: List[str],
    seed: int = 42,
) -> Dict[str, Tuple[PriceTick, OrderBookSnapshot, List[OHLCV]]]:
    """Build deterministic mock market data for every symbol in ``symbols``."""
    return {sym: build_mock_asset_data(sym, seed) for sym in symbols}


def demo_universe_symbols() -> List[str]:
    """A demonstration universe mixing healthy and unhealthy assets."""
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT", "THINUSDT", "PUMPUSDT"]
