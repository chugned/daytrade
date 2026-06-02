"""Multi-asset watchlist screening.

Before any asset is eligible for paper/sandbox trading it must clear a quality
gate. Thin, wide-spread or violently-moving markets are where simulated
results diverge most from reality and where real funds would be most exposed —
so the screener rejects them up front.

Five filters, all configured in ``WatchlistConfig``:

1. **24h volume** — reject markets below a minimum quote volume.
2. **Spread** — reject markets whose top-of-book spread is too wide.
3. **Orderbook notional** — reject markets with too little resting liquidity.
4. **Thin book** — reject markets with too few populated price levels.
5. **Pump-and-dump** — reject markets that moved violently in the last hour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..config.schema import WatchlistConfig
from ..models import OHLCV, OrderBookSnapshot, PriceTick

_MIN_POPULATED_LEVELS = 5  # a book thinner than this counts as "thin"


@dataclass(frozen=True)
class AssetMetrics:
    """The raw quality metrics extracted for one asset."""

    symbol: str
    price: float
    volume_24h_usd: float
    spread_bps: float
    book_notional_usd: float
    populated_levels: int
    move_1h_pct: float


@dataclass(frozen=True)
class AssetScreening:
    """The screening verdict for one asset."""

    symbol: str
    approved: bool
    metrics: AssetMetrics
    rejections: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "APPROVED" if self.approved else "REJECTED"


def extract_metrics(
    symbol: str,
    tick: PriceTick,
    orderbook: OrderBookSnapshot,
    candles: List[OHLCV],
) -> AssetMetrics:
    """Derive screening metrics from raw market data."""
    spread_bps = orderbook.spread_bps if orderbook.spread_bps is not None else 0.0
    book_notional = orderbook.notional_depth("bid") + orderbook.notional_depth("ask")
    populated = len([lvl for lvl in orderbook.bids if lvl.quantity > 0]) + len(
        [lvl for lvl in orderbook.asks if lvl.quantity > 0]
    )

    # 1h move from 1-minute candles (or the longest window available).
    move_1h = 0.0
    if len(candles) >= 2:
        window = min(60, len(candles) - 1)
        past = candles[-1 - window].close
        if past > 0:
            move_1h = candles[-1].close / past - 1.0

    return AssetMetrics(
        symbol=symbol,
        price=tick.price,
        volume_24h_usd=tick.volume_24h,
        spread_bps=spread_bps,
        book_notional_usd=book_notional,
        populated_levels=populated,
        move_1h_pct=move_1h,
    )


def screen_asset(metrics: AssetMetrics, config: WatchlistConfig) -> AssetScreening:
    """Apply every watchlist filter; an asset is approved iff none reject it."""
    rejections: List[str] = []

    if metrics.volume_24h_usd < config.min_24h_volume_usd:
        rejections.append(
            f"low volume: ${metrics.volume_24h_usd:,.0f} 24h "
            f"< ${config.min_24h_volume_usd:,.0f} minimum"
        )
    if metrics.spread_bps > config.max_spread_bps:
        rejections.append(
            f"wide spread: {metrics.spread_bps:.1f} bps "
            f"> {config.max_spread_bps:.1f} bps maximum"
        )
    if metrics.book_notional_usd < config.min_orderbook_notional_usd:
        rejections.append(
            f"thin liquidity: ${metrics.book_notional_usd:,.0f} in book "
            f"< ${config.min_orderbook_notional_usd:,.0f} minimum"
        )
    if metrics.populated_levels < _MIN_POPULATED_LEVELS:
        rejections.append(f"thin orderbook: only {metrics.populated_levels} populated levels")
    if abs(metrics.move_1h_pct) > config.pump_dump_max_1h_move_pct:
        direction = "pump" if metrics.move_1h_pct > 0 else "dump"
        rejections.append(
            f"suspected {direction}-and-dump: {metrics.move_1h_pct * 100:+.1f}% "
            f"in 1h exceeds {config.pump_dump_max_1h_move_pct * 100:.0f}% limit"
        )

    return AssetScreening(
        symbol=metrics.symbol,
        approved=not rejections,
        metrics=metrics,
        rejections=rejections,
    )


class WatchlistScreener:
    """Screens a universe of assets against the watchlist quality filters."""

    def __init__(self, config: WatchlistConfig) -> None:
        self.config = config

    def screen_one(
        self,
        symbol: str,
        tick: PriceTick,
        orderbook: OrderBookSnapshot,
        candles: List[OHLCV],
    ) -> AssetScreening:
        metrics = extract_metrics(symbol, tick, orderbook, candles)
        return screen_asset(metrics, self.config)

    def screen(
        self,
        market_data: Dict[str, Tuple[PriceTick, OrderBookSnapshot, List[OHLCV]]],
    ) -> List[AssetScreening]:
        """Screen every asset in ``market_data`` (symbol -> tick/book/candles)."""
        results = [
            self.screen_one(sym, tick, book, candles)
            for sym, (tick, book, candles) in market_data.items()
        ]
        return sorted(results, key=lambda r: (not r.approved, r.symbol))

    def approved_symbols(
        self,
        market_data: Dict[str, Tuple[PriceTick, OrderBookSnapshot, List[OHLCV]]],
    ) -> List[str]:
        """Symbols that cleared every filter — the tradeable universe."""
        return [r.symbol for r in self.screen(market_data) if r.approved]
