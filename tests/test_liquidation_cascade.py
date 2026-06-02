"""Tests for the liquidation-cascade detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from daytrade.models import OHLCV
from daytrade.observatory.liquidation_cascade import (
    CascadeReading,
    CascadeState,
    cascade_blocks_buy,
    cascade_supports_mean_reversion_buy,
    detect_cascade,
)


def _candle(
    i: int, o: float, c: float, vol: float, h: float | None = None, l: float | None = None
) -> OHLCV:
    hi = h if h is not None else max(o, c) * 1.0005
    lo = l if l is not None else min(o, c) * 0.9995
    return OHLCV(
        symbol="BTCUSDT",
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
        open=o,
        high=hi,
        low=lo,
        close=c,
        volume=vol,
    )


def _flat_history(n: int = 25, *, price: float = 100.0, volume: float = 1000.0) -> List[OHLCV]:
    out: List[OHLCV] = []
    p = price
    for i in range(n):
        p_next = p * (1.0 + (0.0002 if i % 2 == 0 else -0.0002))
        out.append(_candle(i, p, p_next, volume))
        p = p_next
    return out


# ---------------------------------------------------------------------------
# Quiet baseline
# ---------------------------------------------------------------------------


def test_quiet_baseline_is_quiet():
    reading = detect_cascade(_flat_history(40))
    assert reading.state is CascadeState.QUIET
    assert not cascade_blocks_buy(reading)
    assert not cascade_supports_mean_reversion_buy(reading)


def test_insufficient_history_is_quiet():
    reading = detect_cascade(_flat_history(5))
    assert reading.state is CascadeState.QUIET
    assert "insufficient" in reading.reason


# ---------------------------------------------------------------------------
# Active cascade
# ---------------------------------------------------------------------------


def test_large_down_body_with_volume_spike_flags_active():
    base = _flat_history(40, price=100.0, volume=1000.0)
    # Append a single bar that drops ~3% on 8x volume.
    last = base[-1].close
    drop = _candle(
        len(base),
        o=last,
        c=last * 0.97,
        vol=8000.0,
        h=last * 1.0005,
        l=last * 0.969,
    )
    reading = detect_cascade(base + [drop])
    assert reading.state is CascadeState.CASCADE_ACTIVE
    assert cascade_blocks_buy(reading)
    assert not cascade_supports_mean_reversion_buy(reading)
    assert reading.body_atr_mult < -2.0
    assert reading.volume_ratio > 3.0


def test_big_down_body_without_volume_is_not_active():
    base = _flat_history(40, price=100.0, volume=1000.0)
    last = base[-1].close
    drop = _candle(  # big body, but volume only 1.2x — not a cascade
        len(base),
        o=last,
        c=last * 0.97,
        vol=1200.0,
        h=last * 1.0005,
        l=last * 0.969,
    )
    reading = detect_cascade(base + [drop])
    assert reading.state is CascadeState.QUIET


def test_high_volume_without_big_body_is_not_active():
    base = _flat_history(40, price=100.0, volume=1000.0)
    last = base[-1].close
    drop = _candle(  # volume spike, but body within noise
        len(base),
        o=last,
        c=last * 0.9995,
        vol=20000.0,
    )
    reading = detect_cascade(base + [drop])
    assert reading.state is CascadeState.QUIET


# ---------------------------------------------------------------------------
# Exhaustion
# ---------------------------------------------------------------------------


def test_long_lower_wick_after_cascade_is_exhaustion():
    base = _flat_history(40, price=100.0, volume=1000.0)
    last = base[-1].close
    cascade = _candle(
        len(base),
        o=last,
        c=last * 0.97,
        vol=8000.0,
        h=last * 1.0005,
        l=last * 0.969,
    )
    # Exhaustion: open=close just below cascade close; long lower wick.
    px = cascade.close
    exhaustion = _candle(
        len(base) + 1,
        o=px * 1.000,
        c=px * 1.002,
        vol=6000.0,
        h=px * 1.003,
        l=px * 0.980,  # wick stretches well below body
    )
    reading = detect_cascade(base + [cascade, exhaustion])
    assert reading.state is CascadeState.CASCADE_EXHAUSTION
    assert cascade_supports_mean_reversion_buy(reading)
    assert not cascade_blocks_buy(reading)
    assert reading.lower_wick_ratio >= 0.55


def test_exhaustion_needs_prior_cascade_bar():
    base = _flat_history(40, price=100.0, volume=1000.0)
    px = base[-1].close
    # Long lower wick on a quiet day with no prior cascade — should NOT
    # flag as exhaustion.
    bar = _candle(
        len(base),
        o=px,
        c=px * 1.001,
        vol=6000.0,
        h=px * 1.002,
        l=px * 0.985,
    )
    reading = detect_cascade(base + [bar])
    assert reading.state is CascadeState.QUIET


def test_exhaustion_needs_decent_volume():
    base = _flat_history(40, price=100.0, volume=1000.0)
    last = base[-1].close
    cascade = _candle(
        len(base),
        o=last,
        c=last * 0.97,
        vol=8000.0,
        h=last * 1.0005,
        l=last * 0.969,
    )
    px = cascade.close
    # Long lower wick, but volume back to baseline — not an exhaustion.
    bar = _candle(
        len(base) + 1,
        o=px * 1.000,
        c=px * 1.001,
        vol=900.0,
        h=px * 1.002,
        l=px * 0.980,
    )
    reading = detect_cascade(base + [cascade, bar])
    # The cascade bar itself already passed; the follower must volume-confirm.
    assert reading.state is CascadeState.QUIET


# ---------------------------------------------------------------------------
# Gate helpers
# ---------------------------------------------------------------------------


def test_blocks_buy_only_on_active():
    quiet = CascadeReading(CascadeState.QUIET, 0.0, 0.0, 0.0, "")
    active = CascadeReading(CascadeState.CASCADE_ACTIVE, -3.0, 8.0, 0.1, "")
    exh = CascadeReading(CascadeState.CASCADE_EXHAUSTION, 0.5, 5.0, 0.7, "")
    assert cascade_blocks_buy(quiet) is False
    assert cascade_blocks_buy(active) is True
    assert cascade_blocks_buy(exh) is False


def test_supports_reversion_buy_only_on_exhaustion():
    quiet = CascadeReading(CascadeState.QUIET, 0.0, 0.0, 0.0, "")
    active = CascadeReading(CascadeState.CASCADE_ACTIVE, -3.0, 8.0, 0.1, "")
    exh = CascadeReading(CascadeState.CASCADE_EXHAUSTION, 0.5, 5.0, 0.7, "")
    assert cascade_supports_mean_reversion_buy(quiet) is False
    assert cascade_supports_mean_reversion_buy(active) is False
    assert cascade_supports_mean_reversion_buy(exh) is True


def test_states_are_mutually_exclusive():
    assert {*CascadeState} == {
        CascadeState.QUIET,
        CascadeState.CASCADE_ACTIVE,
        CascadeState.CASCADE_EXHAUSTION,
    }


# ---------------------------------------------------------------------------
# Parameter knobs
# ---------------------------------------------------------------------------


def test_higher_thresholds_make_detection_stricter():
    base = _flat_history(40, price=100.0, volume=1000.0)
    last = base[-1].close
    drop = _candle(  # 2.5-ATR-ish drop, 4x volume
        len(base),
        o=last,
        c=last * 0.978,
        vol=4000.0,
        h=last * 1.0005,
        l=last * 0.977,
    )
    candles = base + [drop]
    permissive = detect_cascade(candles, body_atr_threshold=1.5, volume_spike_ratio=2.0)
    strict = detect_cascade(candles, body_atr_threshold=5.0, volume_spike_ratio=20.0)
    assert permissive.state is CascadeState.CASCADE_ACTIVE
    assert strict.state is CascadeState.QUIET
