"""Multi-asset watchlist screening tests."""

from __future__ import annotations

from daytrade.config import WatchlistConfig
from daytrade.watchlist import (
    AssetMetrics,
    WatchlistScreener,
    build_mock_universe,
    demo_universe_symbols,
    screen_asset,
)

CFG = WatchlistConfig()


def _metrics(**overrides) -> AssetMetrics:
    base = dict(
        symbol="XUSDT",
        price=100.0,
        volume_24h_usd=1e9,
        spread_bps=3.0,
        book_notional_usd=1e6,
        populated_levels=40,
        move_1h_pct=0.01,
    )
    base.update(overrides)
    return AssetMetrics(**base)


def test_healthy_asset_approved():
    assert screen_asset(_metrics(), CFG).approved


def test_low_volume_rejected():
    result = screen_asset(_metrics(volume_24h_usd=1_000.0), CFG)
    assert not result.approved
    assert any("volume" in r for r in result.rejections)


def test_wide_spread_rejected():
    result = screen_asset(_metrics(spread_bps=50.0), CFG)
    assert not result.approved
    assert any("spread" in r for r in result.rejections)


def test_thin_orderbook_notional_rejected():
    result = screen_asset(_metrics(book_notional_usd=100.0), CFG)
    assert not result.approved
    assert any("liquidity" in r for r in result.rejections)


def test_thin_orderbook_levels_rejected():
    result = screen_asset(_metrics(populated_levels=2), CFG)
    assert not result.approved
    assert any("orderbook" in r for r in result.rejections)


def test_pump_and_dump_rejected():
    result = screen_asset(_metrics(move_1h_pct=0.40), CFG)
    assert not result.approved
    assert any("pump" in r or "dump" in r for r in result.rejections)


def test_dump_move_rejected():
    result = screen_asset(_metrics(move_1h_pct=-0.40), CFG)
    assert not result.approved


def test_screener_separates_good_and_bad():
    data = build_mock_universe(demo_universe_symbols())
    screener = WatchlistScreener(CFG)
    approved = screener.approved_symbols(data)
    assert "BTCUSDT" in approved and "ETHUSDT" in approved
    assert "THINUSDT" not in approved  # thin / low volume
    assert "PUMPUSDT" not in approved  # pump-and-dump


def test_screener_screens_all_symbols():
    data = build_mock_universe(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    results = WatchlistScreener(CFG).screen(data)
    assert len(results) == 3
