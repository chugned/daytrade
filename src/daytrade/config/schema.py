"""Validated configuration schema.

The config is a tree of pydantic models. Validation happens at load time, so a
malformed YAML file fails immediately with a precise error rather than blowing
up deep inside the pipeline.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models.enums import ModelKind


class _Section(BaseModel):
    """Base for config sections: reject unknown keys, allow nothing extra."""

    model_config = ConfigDict(extra="forbid", frozen=True)


#: The exact phrase the operator must type into ``live_acknowledgement`` to
#: open the gate. Long, deliberate, and contains the absolute floor on the
#: failure mode (capital loss) so it cannot be flipped by autocompletion or
#: by a copy-paste from a Discord screenshot.
LIVE_ACKNOWLEDGEMENT_PHRASE = (
    "I understand this places real orders with real money on Binance and "
    "the maximum loss is the funded capital."
)


class SafetyConfig(_Section):
    """Hard safety rails.

    The default config is paper-only and the validator refuses to load
    any combination of flags that implies live trading **unless** the
    operator has also entered the exact :data:`LIVE_ACKNOWLEDGEMENT_PHRASE`
    into ``live_acknowledgement``. That phrase is intentionally long and
    specific — it cannot be set by accident, by a 1-line diff, or by any
    tool's auto-completion. Going live is a deliberate two-field change
    in this file.

    Even with all three flags aligned, the eventual broker still gates
    writes behind :class:`daytrade.live.LiveConfig.dry_run` and
    :meth:`daytrade.live.BinanceExchange.enable_writes`. So three
    deliberate actions across three locations are required, not one.
    """

    live_trading_enabled: bool = False
    allow_real_orders: bool = False
    paper_trading: bool = True
    live_acknowledgement: str = Field(
        default="",
        description="The operator's explicit acknowledgement phrase. Must "
                    "match LIVE_ACKNOWLEDGEMENT_PHRASE exactly for live "
                    "trading to be allowed. Empty by default; do not "
                    "store this in version control.",
    )

    @model_validator(mode="after")
    def _enforce_paper_only(self) -> "SafetyConfig":
        # Aligned-but-no-acknowledgement is the most likely accidental
        # path; check it first and refuse with a clear message.
        any_live_flag = (self.live_trading_enabled
                         or self.allow_real_orders
                         or not self.paper_trading)
        ack_correct = self.live_acknowledgement == LIVE_ACKNOWLEDGEMENT_PHRASE
        if any_live_flag and not ack_correct:
            raise ValueError(
                "Live-trading flags are set but live_acknowledgement does "
                "NOT match the required phrase. To enable live trading "
                "you must set live_acknowledgement to:\n\n"
                f"  {LIVE_ACKNOWLEDGEMENT_PHRASE!r}\n\n"
                "Leaving paper-only mode without this acknowledgement is "
                "refused by design."
            )
        if not any_live_flag and self.live_acknowledgement:
            # An acknowledgement without the flags is harmless but
            # suspicious — likely a half-finished edit.
            raise ValueError(
                "live_acknowledgement is set but no live-trading flags "
                "are enabled. Either set live_trading_enabled+allow_real_orders "
                "+paper_trading=false to match, or clear live_acknowledgement."
            )
        if any_live_flag and ack_correct:
            # All three flags aligned AND acknowledgement matches.
            # Validate each flag is in the correct combination.
            if not (self.live_trading_enabled and self.allow_real_orders
                    and not self.paper_trading):
                raise ValueError(
                    "Live-trading flags must be set CONSISTENTLY: "
                    "live_trading_enabled=true, allow_real_orders=true, "
                    "paper_trading=false. Partial flips are refused."
                )
        return self

    @property
    def is_live(self) -> bool:
        """True iff every safety gate at this layer is open for live
        trading. Code that places real orders MUST check this."""
        return (self.live_trading_enabled
                and self.allow_real_orders
                and not self.paper_trading
                and self.live_acknowledgement == LIVE_ACKNOWLEDGEMENT_PHRASE)


class RuntimeConfig(_Section):
    log_level: str = "INFO"
    deterministic: bool = True
    random_seed: int = 42
    allow_network: bool = False
    max_data_age_seconds: int = Field(
        default=300, ge=60,
        description="Skip a symbol's decision this cycle if its most recent "
                    "candle is older than this many seconds — a staleness "
                    "guard so the bot never acts on a stale feed.",
    )

    @field_validator("log_level")
    @classmethod
    def _level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError(f"invalid log_level: {v}")
        return v


class ExchangeConfig(_Section):
    sources: List[str] = Field(default_factory=lambda: ["binance", "bybit", "coingecko"])
    timeout_seconds: float = Field(default=5.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)


class ConsensusConfig(_Section):
    min_sources: int = Field(default=1, ge=1)
    outlier_z_threshold: float = Field(
        default=3.0, gt=0,
        description="Source prices beyond this many MADs from the median are dropped.",
    )
    max_dispersion: float = Field(
        default=0.05, gt=0,
        description="If accepted-source dispersion exceeds this, mark degraded.",
    )


class IndicatorConfig(_Section):
    rsi_period: int = Field(default=14, ge=2)
    ema_fast: int = Field(default=12, ge=2)
    ema_slow: int = Field(default=26, ge=3)
    macd_signal: int = Field(default=9, ge=2)
    volatility_window: int = Field(default=20, ge=2)
    momentum_window: int = Field(default=10, ge=2)
    trend_window: int = Field(default=20, ge=2)

    @model_validator(mode="after")
    def _fast_slow(self) -> "IndicatorConfig":
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be < ema_slow")
        return self


class MicrostructureConfig(_Section):
    depth_levels: int = Field(default=10, ge=1)
    imbalance_strong: float = Field(default=0.30, gt=0, le=1)
    wide_spread_bps: float = Field(default=5.0, gt=0)
    thin_liquidity_notional: float = Field(default=50_000.0, gt=0)
    wall_multiple: float = Field(
        default=3.0, gt=1,
        description="A level this many times the average size is a 'wall'.",
    )
    # Chop-zone detection. A market is a "chop zone" (kill switch -> HOLD) when
    # its trailing trend slope is below chop_max_trend_slope. The slope is a
    # fractional price move per bar; calibrated to real 1-minute Binance data,
    # where genuine 1m slopes sit around 0.0001-0.0004. The old 0.0006 default
    # was above the largest real slope, so every symbol was always "chop" and
    # the bot never traded.
    chop_max_trend_slope: float = Field(
        default=0.00015, gt=0,
        description="Abs trailing slope (fractional move per bar) below which "
                    "the market is a directionless chop zone.",
    )
    chop_high_volatility: float = Field(
        default=0.012, gt=0,
        description="Per-bar return std above which volatility is 'high'.",
    )


class FeatureConfig(_Section):
    return_windows: List[int] = Field(default_factory=lambda: [1, 3, 5, 10])
    rolling_std_window: int = Field(default=20, ge=2)
    skew_kurtosis_window: int = Field(default=30, ge=4)


class LabelConfig(_Section):
    horizon: int = Field(default=5, ge=1, description="Bars ahead for the label.")
    breakout_threshold: float = Field(
        default=0.004, gt=0,
        description="Forward return magnitude that counts as a directional move.",
    )


class MLConfig(_Section):
    model_kind: ModelKind = ModelKind.GRADIENT_BOOSTING
    test_size: float = Field(default=0.25, gt=0, lt=1)
    min_train_samples: int = Field(default=100, ge=20)

    model_config = _Section.model_config | {"protected_namespaces": ()}


class WalkForwardConfig(_Section):
    n_folds: int = Field(default=5, ge=2)
    train_window: int = Field(default=400, ge=50)
    test_window: int = Field(default=100, ge=20)
    overfit_gap_warn: float = Field(
        default=0.15, gt=0,
        description="train-test accuracy gap above this flags overfitting.",
    )
    suspicious_accuracy: float = Field(
        default=0.85, gt=0.5, le=1.0,
        description="Test accuracy above this flags likely leakage.",
    )


class MacroConfig(_Section):
    source: str = Field(default="mock", description="'mock' or 'gemini'.")
    default_risk_level: str = "medium"

    @field_validator("source")
    @classmethod
    def _src(cls, v: str) -> str:
        v = v.lower()
        if v not in {"mock", "gemini"}:
            raise ValueError("macro.source must be 'mock' or 'gemini'")
        return v


class FusionWeights(_Section):
    technical: float = Field(default=0.35, ge=0)
    microstructure: float = Field(default=0.25, ge=0)
    macro: float = Field(default=0.20, ge=0)
    ml: float = Field(default=0.20, ge=0)

    @model_validator(mode="after")
    def _positive_sum(self) -> "FusionWeights":
        if self.technical + self.microstructure + self.macro + self.ml <= 0:
            raise ValueError("fusion weights must sum to a positive number")
        return self


class FusionConfig(_Section):
    weights: FusionWeights = Field(default_factory=FusionWeights)
    action_threshold: float = Field(
        default=0.15, gt=0, lt=1,
        description="Abs fused score required to act (else HOLD).",
    )
    min_confidence: float = Field(
        default=0.35, ge=0, le=1,
        description="Confidence below this downgrades the action to HOLD.",
    )
    # Entry/stop/target are placed in units of a volatility unit U, where
    # U = reference_price * clip(ATR/price, min_vol_fraction, max_vol_fraction).
    entry_offset_vol_mult: float = Field(
        default=1.0, ge=0,
        description="Entry is offset this many volatility units toward a better fill.",
    )
    # Stop/target widths calibrated by a backtest sweep over real history
    # (see docs/STRATEGY-IMPROVEMENT-PLAN.md, Phase 1). A 2.0x stop sits
    # outside routine noise; a 3.0x target gives a 1.5:1 reward:risk — the
    # sweep's best combination on out-of-sample data (best return, 58% win
    # rate vs the old 1.0x/5.0x stop's fragile 33%).
    stop_vol_mult: float = Field(default=2.0, gt=0,
                                 description="Stop distance from entry, in vol units.")
    target_vol_mult: float = Field(default=3.0, gt=0,
                                   description="Target distance from entry, in vol units.")
    min_volatility_fraction: float = Field(
        default=0.004, gt=0,
        description="Volatility-unit floor as a fraction of price — keeps stops "
                    "from being placed unrealistically tight in calm markets. "
                    "Raised to 0.004 so the stop clears 1-minute market noise.",
    )
    max_volatility_fraction: float = Field(
        default=0.05, gt=0,
        description="Volatility-unit cap as a fraction of price.",
    )

    @model_validator(mode="after")
    def _vol_bounds(self) -> "FusionConfig":
        if self.min_volatility_fraction >= self.max_volatility_fraction:
            raise ValueError("min_volatility_fraction must be < max_volatility_fraction")
        return self


class KillSwitchConfig(_Section):
    macro_risk_block: str = Field(
        default="extreme",
        description="Macro risk at/above this level blocks all entries.",
    )
    micro_max_spread_bps: float = Field(default=12.0, gt=0)
    block_on_chop: bool = True
    block_on_thin_liquidity: bool = True


class RiskConfig(_Section):
    fee_bps: float = Field(default=10.0, ge=0, description="Per-side fee, bps.")
    base_slippage_bps: float = Field(default=2.0, ge=0)
    impact_slippage_bps: float = Field(
        default=8.0, ge=0,
        description="Extra slippage scaled by order size vs available liquidity.",
    )
    latency_ms: float = Field(default=250.0, ge=0)
    risk_per_trade: float = Field(
        default=0.01, gt=0, le=0.25,
        description="Fraction of equity risked between entry and stop.",
    )
    max_position_pct: float = Field(
        default=0.25, gt=0, le=1.0,
        description="Max notional of a single per-coin position, as a fraction "
                    "of equity.",
    )
    max_daily_loss_pct: float = Field(default=0.05, gt=0, le=1.0)
    max_weekly_loss_pct: float = Field(
        default=0.12, gt=0, le=1.0,
        description="Rolling 7-day loss limit; blocks new entries once hit.",
    )
    max_open_positions: int = Field(
        default=3, ge=1,
        description="Maximum number of simultaneously open positions.",
    )
    loss_cooldown_bars: int = Field(
        default=20, ge=0,
        description="Bars to wait after a losing trade before a new entry.",
    )
    max_hold_bars: int = Field(
        default=48, ge=0,
        description="Triple-barrier vertical: force-close a position after this "
                    "many bars even if neither stop nor target was hit. 0 disables.",
    )
    partial_fill_liquidity_frac: float = Field(
        default=0.25, gt=0, le=1.0,
        description="Max fraction of top-of-book liquidity one order may consume.",
    )

    @model_validator(mode="after")
    def _loss_limits_ordered(self) -> "RiskConfig":
        if self.max_weekly_loss_pct < self.max_daily_loss_pct:
            raise ValueError("max_weekly_loss_pct must be >= max_daily_loss_pct")
        return self


class PaperConfig(_Section):
    starting_cash: float = Field(default=1_000.0, gt=0)
    base_currency: str = "USDT"


class BacktestConfig(_Section):
    warmup_bars: int = Field(default=50, ge=0)
    sharpe_warn_threshold: float = Field(
        default=4.0, gt=0,
        description="A backtest Sharpe-like ratio above this is flagged unrealistic.",
    )


class WatchlistConfig(_Section):
    """Multi-asset watchlist with liquidity / quality screening."""

    symbols: List[str] = Field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        description="Tradeable universe — extend with configurable altcoins.",
    )
    min_24h_volume_usd: float = Field(
        default=50_000_000.0, gt=0,
        description="Reject assets thinner than this in 24h quote volume.",
    )
    max_spread_bps: float = Field(
        default=8.0, gt=0,
        description="Reject assets whose top-of-book spread exceeds this.",
    )
    min_orderbook_notional_usd: float = Field(
        default=200_000.0, gt=0,
        description="Reject assets with less than this notional in the book.",
    )
    pump_dump_max_1h_move_pct: float = Field(
        default=0.25, gt=0,
        description="Reject assets that moved more than this in the last hour "
                    "(suspected pump-and-dump).",
    )

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: List[str]) -> List[str]:
        cleaned = [s.strip().upper() for s in v if s.strip()]
        if not cleaned:
            raise ValueError("watchlist.symbols must not be empty")
        return cleaned


class ApprovalConfig(_Section):
    """Manual-approval gate for paper / sandbox execution."""

    require_manual_approval: bool = Field(
        default=True,
        description="If true, every trade must be confirmed at the CLI.",
    )
    confirmation_phrase: str = Field(
        default="YES",
        description="Phrase the operator must type to authorize a paper trade.",
    )


class SandboxConfig(_Section):
    """Exchange-sandbox (testnet) settings.

    SANDBOX = exchange TESTNET only. There is no mainnet execution path. These
    flags exist so the safe values are explicit and validated — the validator
    refuses any combination that would imply real-money execution.
    """

    enabled: bool = Field(
        default=False,
        description="Opt-in: enable testnet execution (still no real money).",
    )
    exchange: str = Field(
        default="binance",
        description="Which testnet to use: 'binance' or 'bybit'.",
    )
    require_read_only_keys: bool = Field(
        default=True,
        description="Require API keys without trade/withdraw scope by default.",
    )
    reject_withdrawal_keys: bool = Field(
        default=True,
        description="Refuse any API key that has withdrawal permission.",
    )

    @field_validator("exchange")
    @classmethod
    def _exchange(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"binance", "bybit"}:
            raise ValueError("sandbox.exchange must be 'binance' or 'bybit'")
        return v

    @model_validator(mode="after")
    def _enforce_sandbox_safety(self) -> "SandboxConfig":
        # These guards cannot be turned off — withdrawal access is never allowed.
        if not self.reject_withdrawal_keys:
            raise ValueError(
                "reject_withdrawal_keys must be true — withdrawal access is "
                "never permitted."
            )
        return self


class GatingConfig(_Section):
    """Decision gates layered on top of the fusion engine's signal.

    Each gate can only *suppress* a trade, never create one — analysis
    proposes, the gates dispose. Paper / simulation only.
    """

    min_regime_accuracy: float = Field(
        default=0.50, ge=0.0, le=1.0,
        description="Block new trades in a market regime whose own measured "
                    "out-of-sample accuracy is below this floor.",
    )
    regime_min_samples: int = Field(
        default=30, ge=1,
        description="Evaluated predictions a regime needs before the regime "
                    "gate will judge it; below this the regime is allowed "
                    "through so it can accumulate evidence.",
    )
    min_calibrated_confidence: float = Field(
        default=0.50, ge=0.0, le=1.0,
        description="Block trades whose calibrated win probability is below "
                    "this floor (see daytrade.observatory.calibration).",
    )
    meta_label_edge_multiple: float = Field(
        default=2.0, gt=0.0,
        description="Take a trade only if the meta-model scores it at least "
                    "this multiple of its own base win rate. Relative to the "
                    "base rate (not a fixed probability) so it stays "
                    "reachable on any timeframe. 2.0 is the sweep-best value "
                    "on real 1-minute data: ~7x precision lift while still "
                    "keeping enough trades to learn from.",
    )
    use_fear_greed_gate: bool = Field(
        default=False,
        description="Block contrarian-unfavourable trades when the Crypto "
                    "Fear & Greed Index is at an extreme. Disabled by default "
                    "until validated; enable via config. Paper-only.",
    )
    fear_greed_extreme_greed: float = Field(
        default=80.0, ge=0.0, le=100.0,
        description="Score >= this (0-100 scale) is 'extreme greed' — "
                    "the index has historically marked local tops, so when "
                    "the gate is on we block new longs at this level.",
    )
    fear_greed_extreme_fear: float = Field(
        default=20.0, ge=0.0, le=100.0,
        description="Score <= this is 'extreme fear' — historically near "
                    "local bottoms, so when the gate is on we block new "
                    "shorts at this level.",
    )
    use_liquidation_cascade_gate: bool = Field(
        default=False,
        description="Block new long entries when the proxy liquidation-"
                    "cascade detector is in CASCADE_ACTIVE state. Off by "
                    "default until sweep-validated on the deployed symbol "
                    "set. Paper-only.",
    )
    cascade_body_atr_threshold: float = Field(
        default=2.0, gt=0.0,
        description="Bar body magnitude (ATR units) required to call a "
                    "cascade active.",
    )
    cascade_volume_spike_ratio: float = Field(
        default=3.0, gt=0.0,
        description="Bar volume / trailing mean volume required to call "
                    "a cascade active.",
    )
    # Multi-timeframe trend filter — Tier-1 of the 10x research roadmap.
    # Documented ~80% false-signal reduction. Resamples 1m candles to 15m
    # and 1h, then requires both higher-timeframe slopes to align with the
    # primary signal direction before a trade is allowed. Off by default
    # until proven on this watchlist (run scripts/sweep_mtf_filter.py).
    require_higher_tf_alignment: bool = Field(
        default=False,
        description="If True, block BUYs whose 15m AND 1h trend are not "
                    "aligned with the 1m signal. Filter ~80% of false "
                    "1m signals in the literature.",
    )
    higher_tf_min_slope: float = Field(
        default=0.0, ge=0.0,
        description="Minimum absolute slope on the higher timeframes to count "
                    "as an aligned trend. 0.0 = sign-only check.",
    )
    # Funding-rate sentiment gate — opt-in alternative-data signal.
    # Extreme positive funding = crowded longs = often precedes a pullback;
    # blocking BUYs during such windows is a documented sentiment-based
    # alpha in crypto. Off by default until proven on this watchlist
    # (see scripts/sweep_funding_gate.py).
    use_funding_rate_gate: bool = Field(
        default=False,
        description="If True, query Binance perpetual-futures funding rates "
                    "and block BUYs when funding is extremely positive.",
    )
    funding_extreme_positive: float = Field(
        default=0.0003, gt=0.0,
        description="Block BUYs when funding rate exceeds this (as a "
                    "fraction; 0.0003 = 0.03%).",
    )
    funding_extreme_negative: float = Field(
        default=-0.0010, lt=0.0,
        description="At or below this funding rate, explicitly allow the "
                    "BUY (short-squeeze setup).",
    )


class AppConfig(_Section):
    """The complete validated application configuration."""

    profile: str = "default"
    symbol: str = "BTCUSDT"

    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    exchanges: ExchangeConfig = Field(default_factory=ExchangeConfig)
    consensus: ConsensusConfig = Field(default_factory=ConsensusConfig)
    indicators: IndicatorConfig = Field(default_factory=IndicatorConfig)
    microstructure: MicrostructureConfig = Field(default_factory=MicrostructureConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    labels: LabelConfig = Field(default_factory=LabelConfig)
    ml: MLConfig = Field(default_factory=MLConfig)
    walkforward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)
    macro: MacroConfig = Field(default_factory=MacroConfig)
    fusion: FusionConfig = Field(default_factory=FusionConfig)
    gating: GatingConfig = Field(default_factory=GatingConfig)
    killswitch: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    watchlist: WatchlistConfig = Field(default_factory=WatchlistConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        return v.strip().upper()
