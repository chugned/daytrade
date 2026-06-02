"""Risk engine — sizing, loss limits, exposure caps and realistic execution.

The risk engine sits between a *decision* and an *execution*. It answers, in
order:

1. Are we allowed to open a new position right now?
   - daily loss limit not breached
   - weekly loss limit not breached
   - open-position count below the cap
   - not inside a post-loss cooldown
2. If allowed, how big? (risk-based sizing, per-coin notional cap)
3. What fill do we actually get? (adverse fees / slippage / partial fill)

Spread / liquidity / chop hazards are handled upstream by the kill switch;
the confidence gate is handled by the fusion engine. Together these are the
"no trade if ..." controls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from ..config.schema import RiskConfig
from ..models import Fill, Side
from .execution import simulate_fill
from .limits import DailyLossTracker, WeeklyLossTracker
from .sizing import SizingResult, position_size


@dataclass(frozen=True)
class TradePermission:
    """Whether a new entry is currently permitted, and why / why not."""

    allowed: bool
    reason: str
    blocks: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.blocks is None:
            object.__setattr__(self, "blocks", [])


class RiskEngine:
    """Stateful risk controller for a single trading session."""

    def __init__(self, config: RiskConfig, starting_equity: float) -> None:
        self.config = config
        self._daily = DailyLossTracker(starting_equity, config.max_daily_loss_pct)
        self._weekly = WeeklyLossTracker(starting_equity, config.max_weekly_loss_pct)
        # Cooldown: no new entry until the bar index reaches this value.
        self._cooldown_until_bar: int = -1

    # -- state updates -------------------------------------------------------

    def observe_equity(self, timestamp: datetime, equity: float) -> None:
        """Feed the latest equity to the loss trackers (call once per bar)."""
        self._daily.observe(timestamp, equity)
        self._weekly.observe(timestamp, equity)

    def register_trade_close(self, pnl: float, bar_index: int) -> None:
        """Record a closed trade. A loss starts the post-loss cooldown."""
        if pnl < 0 and self.config.loss_cooldown_bars > 0:
            self._cooldown_until_bar = bar_index + self.config.loss_cooldown_bars

    # -- permission checks ---------------------------------------------------

    def permission(self, equity: float) -> TradePermission:
        """Loss-limit-only check (daily + weekly). Kept for simple callers."""
        blocks = self._loss_blocks(equity)
        if blocks:
            return TradePermission(False, blocks[0], blocks)
        return TradePermission(True, "within loss limits")

    def evaluate_entry(
        self,
        equity: float,
        open_positions: int,
        bar_index: int,
    ) -> TradePermission:
        """Full pre-entry risk check: loss limits, exposure cap, cooldown."""
        blocks = self._loss_blocks(equity)

        if open_positions >= self.config.max_open_positions:
            blocks.append(
                f"max open positions reached: {open_positions} "
                f">= {self.config.max_open_positions}"
            )
        if bar_index < self._cooldown_until_bar:
            remaining = self._cooldown_until_bar - bar_index
            blocks.append(f"post-loss cooldown active: {remaining} bar(s) remaining")

        if blocks:
            return TradePermission(False, blocks[0], blocks)
        return TradePermission(True, "within risk limits")

    def _loss_blocks(self, equity: float) -> List[str]:
        blocks: List[str] = []
        if self._daily.is_breached(equity):
            blocks.append(
                f"daily loss limit hit: down {self._daily.loss_pct(equity) * 100:.2f}%"
                f" >= {self.config.max_daily_loss_pct * 100:.2f}%"
            )
        if self._weekly.is_breached(equity):
            blocks.append(
                f"weekly loss limit hit: down "
                f"{self._weekly.loss_pct(equity) * 100:.2f}%"
                f" >= {self.config.max_weekly_loss_pct * 100:.2f}%"
            )
        return blocks

    # -- sizing & execution --------------------------------------------------

    def size(self, equity: float, entry: float, stop: float) -> SizingResult:
        """Risk-based position size for a planned entry/stop (per-coin capped)."""
        return position_size(equity, entry, stop, self.config)

    def execute(
        self,
        order_id: str,
        symbol: str,
        side: Side,
        quantity: float,
        reference_price: float,
        available_liquidity: float,
        timestamp: Optional[datetime] = None,
    ) -> Fill:
        """Simulate an adverse, realistic fill for an order."""
        return simulate_fill(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            reference_price=reference_price,
            available_liquidity=available_liquidity,
            config=self.config,
            timestamp=timestamp,
        )

    # -- introspection -------------------------------------------------------

    @property
    def daily_loss_tracker(self) -> DailyLossTracker:
        return self._daily

    @property
    def weekly_loss_tracker(self) -> WeeklyLossTracker:
        return self._weekly

    def in_cooldown(self, bar_index: int) -> bool:
        return bar_index < self._cooldown_until_bar
