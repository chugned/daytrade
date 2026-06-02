"""Mean-reversion setup detector tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from daytrade.models import OHLCV
from daytrade.observatory.mean_reversion import (
    MeanReversionConfig,
    MeanReversionSetup,
    detect_mean_reversion_setup,
)


def _candles(closes: List[float], *, vols=None) -> List[OHLCV]:
    """Build OHLCV candles that satisfy the low <= open/close <= high invariant."""
    t0 = datetime(2026, 5, 31, tzinfo=timezone.utc)
    v = vols or [1000.0] * len(closes)
    out: List[OHLCV] = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else closes[i]
        h = max(o, c) * 1.0005
        l = min(o, c) * 0.9995
        out.append(
            OHLCV(
                symbol="X",
                timestamp=t0 + timedelta(minutes=i),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=v[i],
            )
        )
    return out


def _calm_then_drop(drop_pct: float = -0.015, length: int = 50, vol_spike: float = 3.0):
    """Build a calm series followed by a sharp drop in the last 15 bars."""
    pre_n = max(1, length - 15)
    closes = [100.0] * pre_n
    drop_start = closes[-1]
    drop_bars = max(1, min(15, length - 1))
    for i in range(1, drop_bars + 1):
        closes.append(drop_start * (1 + drop_pct * (i / drop_bars)))
    vols = [1000.0] * len(closes)
    vols[-1] = 1000.0 * vol_spike
    return _candles(closes, vols=vols)


def test_oversold_reversal_setup_fires():
    candles = _calm_then_drop(drop_pct=-0.015)
    setup = detect_mean_reversion_setup(candles)
    assert setup is not None
    assert isinstance(setup, MeanReversionSetup)
    assert setup.drop_pct < -0.008
    assert setup.rsi < 30
    assert setup.volume_ratio > 1.5
    assert setup.entry > 0
    assert setup.stop < setup.entry
    assert setup.target > setup.entry


def test_no_setup_when_drop_too_small():
    candles = _calm_then_drop(drop_pct=-0.003)  # only 0.3% drop
    assert detect_mean_reversion_setup(candles) is None


def test_no_setup_without_volume_spike():
    candles = _calm_then_drop(drop_pct=-0.015, vol_spike=1.0)  # flat volume
    assert detect_mean_reversion_setup(candles) is None


def test_no_setup_with_too_little_history():
    candles = _calm_then_drop(length=10)
    assert detect_mean_reversion_setup(candles) is None


def test_no_setup_in_steady_uptrend():
    closes = [100.0 + 0.05 * i for i in range(50)]
    candles = _candles(closes)
    assert detect_mean_reversion_setup(candles) is None


def test_confidence_scales_with_drop_magnitude():
    mild = detect_mean_reversion_setup(_calm_then_drop(drop_pct=-0.010))
    severe = detect_mean_reversion_setup(_calm_then_drop(drop_pct=-0.030))
    assert mild is not None and severe is not None
    assert severe.confidence > mild.confidence


def test_custom_config_thresholds():
    # Loose thresholds — small drop now qualifies.
    cfg = MeanReversionConfig(drop_pct=0.002, rsi_max=80.0, volume_mult=1.0)
    candles = _calm_then_drop(drop_pct=-0.005)
    setup = detect_mean_reversion_setup(candles, cfg)
    assert setup is not None


def test_stop_is_below_entry_and_target_above():
    setup = detect_mean_reversion_setup(_calm_then_drop(drop_pct=-0.020))
    assert setup is not None
    assert setup.stop < setup.entry < setup.target
    # Stop should be within a reasonable fraction below entry (not absurd).
    assert (setup.entry - setup.stop) / setup.entry < 0.05
