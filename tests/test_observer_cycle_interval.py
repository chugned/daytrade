"""Test that ``Observer.run_forever`` interprets ``interval`` as the
total cycle period, NOT as extra sleep on top of the work.

Before the fix, ``--interval 60`` meant "sleep 60s AFTER the work" so
a 130s analysis pass produced a 190s actual cycle. The bot silently
overshot its target cadence by exactly the work duration. After the
fix, sleep is computed as ``max(0, interval - work_elapsed)`` so:

- work < interval: sleep makes up the rest (bot hits configured cadence)
- work >= interval: no sleep, bot runs back-to-back + a warning is logged
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from daytrade.config import load_config
from daytrade.observatory import LiveMockFeed, ObservatoryDB, Observer
from daytrade.config.schema import WatchlistConfig


def _make_observer(tmp_path):
    return Observer(
        load_config(load_dotenv_file=False),
        WatchlistConfig(symbols=["AAPL"]),
        db=ObservatoryDB(tmp_path / "obs.db"),
        feed=LiveMockFeed(),
    )


def test_short_work_sleeps_remainder_to_hit_interval(tmp_path):
    """work=2s, interval=10s → sleep 8s (totals 10s cycle).

    The inner sleep loop uses 1-second slices. ``_stop`` is set after
    the first slice fires so we measure exactly one cycle's worth."""
    obs = _make_observer(tmp_path)
    monot_calls = iter([100.0, 102.0])
    sleeps = []

    def fake_run_once():
        pass  # don't stop yet — we want to observe the sleep

    def fake_sleep(s):
        sleeps.append(s)
        # After first sleep slice, stop the loop so we exit cleanly
        obs._stop = True

    with patch("daytrade.observatory.observer.time.monotonic",
               side_effect=monot_calls), \
         patch("daytrade.observatory.observer.time.sleep",
               side_effect=fake_sleep), \
         patch.object(obs, "run_once", side_effect=fake_run_once):
        obs.run_forever(interval=10)

    # One slice was attempted. With interval=10 minus elapsed=2 → total=8.
    # The first slice is min(1.0, 8.0) = 1.0 second.
    assert sleeps == [pytest.approx(1.0)], (
        f"expected first sleep slice of 1.0s, got {sleeps}"
    )


def test_long_work_does_not_sleep(tmp_path):
    """work=15s, interval=10s → no sleep, bot runs back-to-back."""
    obs = _make_observer(tmp_path)
    monot_calls = iter([100.0, 115.0])  # work took 15s
    sleeps = []

    def fake_run_once():
        obs._stop = True

    with patch("daytrade.observatory.observer.time.monotonic",
               side_effect=monot_calls), \
         patch("daytrade.observatory.observer.time.sleep",
               side_effect=lambda s: sleeps.append(s)), \
         patch.object(obs, "run_once", side_effect=fake_run_once):
        obs.run_forever(interval=10)

    # work exceeded interval — zero sleep
    assert sum(sleeps) == 0.0, f"expected 0s sleep, got {sum(sleeps)}"


def test_work_at_exactly_interval_does_not_oversleep(tmp_path):
    """work=10s, interval=10s → sleep 0s (not 10s)."""
    obs = _make_observer(tmp_path)
    monot_calls = iter([100.0, 110.0])
    sleeps = []

    def fake_run_once():
        obs._stop = True

    with patch("daytrade.observatory.observer.time.monotonic",
               side_effect=monot_calls), \
         patch("daytrade.observatory.observer.time.sleep",
               side_effect=lambda s: sleeps.append(s)), \
         patch.object(obs, "run_once", side_effect=fake_run_once):
        obs.run_forever(interval=10)

    assert sum(sleeps) == 0.0
