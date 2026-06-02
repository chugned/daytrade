"""Multi-asset watchlist — quality screening before an asset is tradeable."""

from __future__ import annotations

from .loader import DEFAULT_WATCHLIST_PATH, load_watchlist_config
from .screener import (
    AssetMetrics,
    AssetScreening,
    WatchlistScreener,
    extract_metrics,
    screen_asset,
)
from .universe import (
    build_mock_asset_data,
    build_mock_universe,
    demo_universe_symbols,
)

__all__ = [
    "AssetMetrics",
    "AssetScreening",
    "WatchlistScreener",
    "extract_metrics",
    "screen_asset",
    "build_mock_asset_data",
    "build_mock_universe",
    "demo_universe_symbols",
    "load_watchlist_config",
    "DEFAULT_WATCHLIST_PATH",
]
