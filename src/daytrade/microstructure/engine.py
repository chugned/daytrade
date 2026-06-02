"""Orderbook & microstructure analysis.

Reads an L2 orderbook (and, optionally, recent candles for regime context) and
produces a :class:`MicrostructureSignal`: directional pressure from depth
imbalance, liquidity walls as support/resistance, plus spread / thin-liquidity
/ chop-zone hazard flags that the kill switch later consumes.
"""

from __future__ import annotations

import math
from typing import List, Optional

import numpy as np

from ..config.schema import MicrostructureConfig
from ..indicators import core
from ..indicators.frame import ohlcv_to_frame
from ..models import (
    OHLCV,
    Bias,
    MarketRegime,
    MicrostructureSignal,
    OrderBookSnapshot,
)
from ..models.market import OrderBookLevel


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def depth_imbalance(book: OrderBookSnapshot, levels: int) -> float:
    """Signed depth imbalance in [-1, 1].

    ``+`` => more bid quantity (buy pressure); ``-`` => more ask quantity
    (sell pressure). Computed on quantity, not notional, so it is not skewed
    by the (tiny) price difference across the spread.
    """
    bid_qty = book.depth("bid", levels)
    ask_qty = book.depth("ask", levels)
    total = bid_qty + ask_qty
    if total <= 0:
        return 0.0
    return _clip((bid_qty - ask_qty) / total)


def find_walls(levels: List[OrderBookLevel], wall_multiple: float) -> List[float]:
    """Return prices of levels whose size exceeds ``wall_multiple`` x mean size."""
    if not levels:
        return []
    sizes = np.array([lvl.quantity for lvl in levels], dtype=float)
    mean = float(sizes.mean())
    if mean <= 0:
        return []
    return [levels[i].price for i in range(len(levels)) if sizes[i] >= wall_multiple * mean]


class MicrostructureEngine:
    """Turns an orderbook into a microstructure signal."""

    def __init__(self, config: MicrostructureConfig | None = None) -> None:
        self.config = config or MicrostructureConfig()

    def compute(
        self,
        book: OrderBookSnapshot,
        candles: Optional[List[OHLCV]] = None,
    ) -> MicrostructureSignal:
        """Analyze ``book`` (with optional candle context) into a signal."""
        cfg = self.config
        levels = cfg.depth_levels
        reasoning: List[str] = []

        imbalance = depth_imbalance(book, levels)
        if abs(imbalance) >= cfg.imbalance_strong:
            side = "buyers" if imbalance > 0 else "sellers"
            reasoning.append(f"Strong depth imbalance: {abs(imbalance) * 100:.0f}% toward {side}")
        else:
            reasoning.append(f"Depth imbalance {imbalance * 100:+.0f}% (mild)")

        # --- Spread analysis ---
        spread_bps = book.spread_bps
        wide_spread = spread_bps is not None and spread_bps > cfg.wide_spread_bps
        if spread_bps is not None:
            tag = "wide" if wide_spread else "normal"
            reasoning.append(f"Spread {spread_bps:.1f} bps ({tag})")

        # --- Thin liquidity ---
        notional = book.notional_depth("bid", levels) + book.notional_depth("ask", levels)
        thin = notional < cfg.thin_liquidity_notional
        if thin:
            reasoning.append(f"Thin liquidity: {notional:,.0f} notional in top {levels} levels")

        # --- Liquidity walls -> support / resistance ---
        bid_walls = find_walls(book.bids[:levels], cfg.wall_multiple)
        ask_walls = find_walls(book.asks[:levels], cfg.wall_multiple)
        support = max(bid_walls) if bid_walls else None
        resistance = min(ask_walls) if ask_walls else None
        if support is not None:
            reasoning.append(f"Bid liquidity wall (support) near {support:,.2f}")
        if resistance is not None:
            reasoning.append(f"Ask liquidity wall (resistance) near {resistance:,.2f}")

        # --- Regime & chop detection (needs candle context) ---
        regime, chop = self._regime(candles)
        if chop:
            reasoning.append("Chop zone: directionless, low-conviction price action")
        reasoning.append(f"Regime: {regime.value}")

        # --- Score & bias ---
        score = _clip(imbalance / cfg.imbalance_strong)
        # Hazards sap conviction but do not flip direction.
        if score > 0.15:
            bias = Bias.BULLISH
        elif score < -0.15:
            bias = Bias.BEARISH
        else:
            bias = Bias.NEUTRAL

        confidence = 0.6
        confidence += 0.2 * min(abs(imbalance) / cfg.imbalance_strong, 1.0)
        if wide_spread:
            confidence -= 0.25
        if thin:
            confidence -= 0.2
        if chop:
            confidence -= 0.2
        confidence = _clip(confidence, 0.0, 1.0)

        interpretation = self._interpret(imbalance, thin, wide_spread, chop)

        return MicrostructureSignal(
            symbol=book.symbol,
            timestamp=book.timestamp,
            bias=bias,
            score=score,
            confidence=confidence,
            reasoning=reasoning,
            imbalance=imbalance,
            spread_bps=spread_bps,
            regime=regime,
            thin_liquidity=thin,
            chop_zone=chop,
            support=support,
            resistance=resistance,
            liquidity_walls=sorted(set(bid_walls + ask_walls)),
            liquidity_interpretation=interpretation,
        )

    def _regime(self, candles: Optional[List[OHLCV]]) -> tuple[MarketRegime, bool]:
        """Classify the market regime and whether it is a chop zone."""
        if not candles or len(candles) < 30:
            return MarketRegime.RANGE, False
        frame = ohlcv_to_frame(candles)
        close = frame["close"]
        slope = core.trend_slope(close, min(20, len(close) - 1))
        vol = core.volatility(close, min(20, len(close) - 1))
        slope_v = slope.dropna()
        vol_v = vol.dropna()
        if slope_v.empty or vol_v.empty:
            return MarketRegime.RANGE, False
        s = float(slope_v.iloc[-1])
        v = float(vol_v.iloc[-1])
        if not (math.isfinite(s) and math.isfinite(v)):
            return MarketRegime.RANGE, False

        high_vol = v > self.config.chop_high_volatility
        weak_trend = abs(s) < self.config.chop_max_trend_slope
        if high_vol and weak_trend:
            return MarketRegime.VOLATILE, True
        if weak_trend:
            return MarketRegime.CHOP, True
        if high_vol:
            return MarketRegime.VOLATILE, False
        if s > 0:
            return MarketRegime.TREND_UP, False
        return MarketRegime.TREND_DOWN, False

    @staticmethod
    def _interpret(imbalance: float, thin: bool, wide: bool, chop: bool) -> str:
        parts: List[str] = []
        if imbalance > 0.1:
            parts.append("bid-heavy book favors upside")
        elif imbalance < -0.1:
            parts.append("ask-heavy book favors downside")
        else:
            parts.append("balanced book")
        if thin:
            parts.append("thin liquidity raises slippage risk")
        if wide:
            parts.append("wide spread penalizes entries")
        if chop:
            parts.append("chop zone discourages directional trades")
        return "; ".join(parts)
