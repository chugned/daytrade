"""Configuration for the live-trading layer.

Kept separate from the main config schema so it can be loaded only when
explicitly enabled. The defaults are paranoid: dry_run=true, tiny stake,
hard daily loss cap.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LiveConfig(BaseModel):
    """Live-trading parameters. Frozen, extra fields forbidden."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dry_run: bool = Field(
        default=True,
        description="If True, the LiveBroker mirrors orders into a Mock "
                    "exchange and does NOT call the real exchange. Default "
                    "TRUE; flip to False only after deliberate config "
                    "override AND SafetyConfig opt-in.",
    )

    api_key_env: str = Field(
        default="DAYTRADE_BINANCE_KEY",
        description="Name of the env var holding the API key. The key "
                    "itself is never persisted to disk by daytrade. Trade-"
                    "only permission is asserted at startup.",
    )
    api_secret_env: str = Field(
        default="DAYTRADE_BINANCE_SECRET",
        description="Name of the env var holding the API secret.",
    )

    exchange: str = Field(
        default="binance",
        description="Exchange identifier. Currently only 'binance' and "
                    "'mock' are supported.",
    )

    base_currency: str = Field(default="USDT")

    max_stake_per_trade: float = Field(
        default=25.0, gt=0.0,
        description="Hard cap on USDT amount per market order. Designed "
                    "to limit blast radius when first deploying live.",
    )
    max_daily_loss: float = Field(
        default=30.0, gt=0.0,
        description="Hard daily loss limit in USDT. Broker raises and "
                    "stops accepting new orders for the rest of the UTC "
                    "day if realised loss exceeds this.",
    )
    max_open_positions: int = Field(default=3, ge=1, le=10)

    reconcile_every_n_orders: int = Field(
        default=5, ge=1,
        description="Run exchange-vs-local reconciliation after this many "
                    "orders, alerting if drift detected.",
    )

    @model_validator(mode="after")
    def _sane_limits(self) -> "LiveConfig":
        if self.max_stake_per_trade > 500:
            raise ValueError(
                "max_stake_per_trade > 500 USDT is unusually large for "
                "an initial deployment. If intentional, raise the limit "
                "in code; the default config keeps you under €500 per "
                "trade for first 30 days."
            )
        return self
