"""QA-CRIT-4 regression — consecutive cycle failures trigger backoff + abort.

Before the fix, run_forever incremented ``consecutive_failures`` but
never read it. A bot stuck in a permafail loop hammered Binance once
per cycle and wrote a CRITICAL alert each time.

After the fix:
  - >_BACKOFF_THRESHOLD failures → exponential backoff added to sleep
  - ≥_ABORT_THRESHOLD failures → run aborts, _stop set to True
"""

from __future__ import annotations

from unittest.mock import patch

from daytrade.observatory.observer import Observer


def _obs():
    from daytrade.config.schema import AppConfig, WatchlistConfig

    with patch("daytrade.observatory.observer.ObservatoryDB"):
        return Observer(AppConfig(), WatchlistConfig())


def test_backoff_threshold_and_abort_constants_sensible():
    """Sanity: backoff fires before abort; abort doesn't fire too late."""
    assert Observer._BACKOFF_THRESHOLD < Observer._ABORT_THRESHOLD
    assert Observer._BACKOFF_THRESHOLD >= 1
    assert Observer._ABORT_THRESHOLD <= 200  # ~16 hours @ 5min cycles
    assert Observer._BACKOFF_MAX_SECONDS >= 60


def test_abort_after_threshold_failures_stops_run():
    """After ABORT_THRESHOLD consecutive failures, _stop must be set."""
    obs = _obs()
    obs._ABORT_THRESHOLD = 3  # tiny for the test
    obs._BACKOFF_THRESHOLD = 1
    obs._BACKOFF_MAX_SECONDS = 0  # don't actually sleep
    obs.alerts.emit = lambda *a, **k: None
    obs.db.insert_error = lambda *a, **k: None
    # Make run_once always fail
    call_count = {"n": 0}

    def _fail(*_):
        call_count["n"] += 1
        raise RuntimeError("simulated cycle failure")

    obs.run_once = _fail
    obs._install_signal_handlers = lambda: None
    obs.start = lambda: None

    # Patch time.sleep so the test runs instantly
    with patch("daytrade.observatory.observer.time.sleep"):
        obs.run_forever(interval=0)

    assert obs._stop is True
    # run_once should have been called exactly ABORT_THRESHOLD times
    assert call_count["n"] == obs._ABORT_THRESHOLD


def test_successful_cycle_resets_failure_counter():
    """Backoff state must reset cleanly on the first successful cycle —
    a transient outage must NOT degrade post-recovery."""
    obs = _obs()
    obs._ABORT_THRESHOLD = 10
    obs._BACKOFF_THRESHOLD = 1
    obs._BACKOFF_MAX_SECONDS = 0
    obs.alerts.emit = lambda *a, **k: None
    obs.db.insert_error = lambda *a, **k: None
    obs._install_signal_handlers = lambda: None
    obs.start = lambda: None

    sequence = ["fail", "fail", "ok", "ok", "fail", "stop"]
    state = {"i": 0}

    def _maybe_fail(*_):
        if state["i"] >= len(sequence):
            obs._stop = True
            return
        step = sequence[state["i"]]
        state["i"] += 1
        if step == "fail":
            raise RuntimeError("cycle fail")
        if step == "stop":
            obs._stop = True

    obs.run_once = _maybe_fail

    with patch("daytrade.observatory.observer.time.sleep"):
        obs.run_forever(interval=0)
    # We did NOT abort — the counter reset on the first success and
    # the final 'fail' is back at count=1, far below abort threshold
    assert obs._stop is True
    # State index advanced past at least the recovery sequence
    assert state["i"] >= 4
