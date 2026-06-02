"""Technical-indicator engine.

Computes indicators over a candle series and condenses them into a single
:class:`TechnicalSignal` describing the latest bar — a directional ``score``
in [-1, 1], a ``confidence``, and human-readable reasoning.
"""

from __future__ import annotations

import math
from typing import List

import numpy as np
import pandas as pd

from ..config.schema import IndicatorConfig
from ..models import OHLCV, Bias, TechnicalSignal
from . import core
from .frame import ohlcv_to_frame


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _safe_last(series: pd.Series) -> float | None:
    """Last non-NaN value of a series, or None."""
    clean = series.dropna()
    if clean.empty:
        return None
    val = float(clean.iloc[-1])
    return val if math.isfinite(val) else None


class TechnicalEngine:
    """Turns OHLCV candles into a technical signal.

    The engine blends a trend read (EMA / MACD / slope / momentum) with a
    mean-reversion read (RSI). ``RSI_EXTREMITY_GAIN`` controls how much an
    *extreme* RSI overrides the trend: at a neutral RSI the weight is 1.0; at
    a fully extreme RSI it is ``1 + RSI_EXTREMITY_GAIN``. A high gain makes the
    engine behave like a mean-reversion model when the market is stretched.
    """

    #: Extra RSI weight applied at a fully extreme (0 or 100) reading.
    RSI_EXTREMITY_GAIN: float = 5.5

    #: Sub-score weights for the trend indicators.
    EMA_WEIGHT: float = 0.8
    MACD_WEIGHT: float = 0.6
    SLOPE_WEIGHT: float = 0.8
    MOMENTUM_WEIGHT: float = 0.6

    def __init__(self, config: IndicatorConfig | None = None) -> None:
        self.config = config or IndicatorConfig()

    def compute(self, candles: List[OHLCV]) -> TechnicalSignal:
        """Compute the technical signal for the most recent candle."""
        if not candles:
            raise ValueError("TechnicalEngine.compute needs at least one candle")
        frame = ohlcv_to_frame(candles)
        close = frame["close"]
        symbol = candles[-1].symbol
        ts = candles[-1].timestamp
        cfg = self.config

        rsi_series = core.rsi(close, cfg.rsi_period)
        ema_fast = core.ema(close, cfg.ema_fast)
        ema_slow = core.ema(close, cfg.ema_slow)
        macd_df = core.macd(close, cfg.ema_fast, cfg.ema_slow, cfg.macd_signal)
        vol_series = core.volatility(close, cfg.volatility_window)
        mom_series = core.momentum(close, cfg.momentum_window)
        slope_series = core.trend_slope(close, cfg.trend_window)

        rsi_v = _safe_last(rsi_series)
        ema_f = _safe_last(ema_fast)
        ema_s = _safe_last(ema_slow)
        macd_v = _safe_last(macd_df["macd"])
        macd_sig = _safe_last(macd_df["signal"])
        macd_hist = _safe_last(macd_df["histogram"])
        vol_v = _safe_last(vol_series)
        mom_v = _safe_last(mom_series)
        slope_v = _safe_last(slope_series)

        # Each indicator contributes a (score, weight) pair. Scores are
        # combined as a weighted mean — RSI gets extra weight at extremes
        # because an oversold/overbought reading is a high-conviction,
        # high-information mean-reversion signal.
        sub: List[float] = []  # raw sub-scores (for agreement/std)
        weighted: List[float] = []  # weight * score
        weights: List[float] = []
        reasoning: List[str] = []

        def _add(score_val: float, weight: float) -> None:
            sub.append(score_val)
            weighted.append(weight * score_val)
            weights.append(weight)

        # --- RSI: mean-reversion read ---
        if rsi_v is not None:
            # Map RSI 0..100 to +1..-1 around the neutral 50 line.
            rsi_score = _clip((50.0 - rsi_v) / 30.0)
            # Extremity boost: weight ramps from 1.0 (neutral) upward as the
            # reading approaches an extreme — see RSI_EXTREMITY_GAIN.
            extremity = _clip(abs(rsi_v - 50.0) / 25.0, 0.0, 1.0)
            rsi_weight = 1.0 + self.RSI_EXTREMITY_GAIN * extremity
            _add(rsi_score, rsi_weight)
            if rsi_v <= 30:
                reasoning.append(f"RSI {rsi_v:.1f} — oversold (bullish mean-reversion)")
            elif rsi_v >= 70:
                reasoning.append(f"RSI {rsi_v:.1f} — overbought (bearish mean-reversion)")
            else:
                reasoning.append(f"RSI {rsi_v:.1f} — neutral")

        # --- EMA cross: trend read ---
        if ema_f is not None and ema_s is not None and ema_s != 0:
            ema_gap = (ema_f - ema_s) / ema_s
            ema_score = _clip(ema_gap / 0.01)  # 1% gap saturates
            _add(ema_score, self.EMA_WEIGHT)
            direction = "above" if ema_f >= ema_s else "below"
            reasoning.append(
                f"EMA{cfg.ema_fast} {direction} EMA{cfg.ema_slow} " f"({ema_gap * 100:+.2f}%)"
            )

        # --- MACD histogram: momentum read ---
        if macd_hist is not None and ema_s:
            macd_score = _clip(macd_hist / (0.004 * ema_s))
            _add(macd_score, self.MACD_WEIGHT)
            reasoning.append(f"MACD histogram {macd_hist:+.4f}")

        # --- Trend slope ---
        if slope_v is not None:
            slope_score = _clip(slope_v / 0.002)
            _add(slope_score, self.SLOPE_WEIGHT)
            reasoning.append(f"Trend slope {slope_v * 100:+.3f}%/bar")

        # --- Momentum ---
        if mom_v is not None:
            mom_score = _clip(mom_v / 0.02)
            _add(mom_score, self.MOMENTUM_WEIGHT)
            reasoning.append(f"Momentum {mom_v * 100:+.2f}% over {cfg.momentum_window} bars")

        total_weight = sum(weights)
        score = _clip(sum(weighted) / total_weight) if total_weight > 0 else 0.0

        # Confidence: how many indicators were available and how much they
        # agree (low dispersion among sub-scores => high confidence).
        if sub:
            agreement = 1.0 - _clip(float(np.std(sub)), 0.0, 1.0)
            coverage = len(sub) / 5.0
            confidence = _clip(0.35 + 0.4 * agreement + 0.25 * coverage, 0.0, 1.0)
        else:
            confidence = 0.0

        if score > 0.15:
            bias = Bias.BULLISH
        elif score < -0.15:
            bias = Bias.BEARISH
        else:
            bias = Bias.NEUTRAL

        return TechnicalSignal(
            symbol=symbol,
            timestamp=ts,
            bias=bias,
            score=score,
            confidence=confidence,
            reasoning=reasoning,
            rsi=rsi_v,
            ema_fast=ema_f,
            ema_slow=ema_s,
            macd=macd_v,
            macd_signal=macd_sig,
            macd_histogram=macd_hist,
            volatility=vol_v,
            momentum=mom_v,
            trend_slope=slope_v,
            indicators={
                k: v
                for k, v in {
                    "rsi": rsi_v,
                    "ema_fast": ema_f,
                    "ema_slow": ema_s,
                    "macd": macd_v,
                    "volatility": vol_v,
                    "momentum": mom_v,
                    "trend_slope": slope_v,
                }.items()
                if v is not None
            },
        )
