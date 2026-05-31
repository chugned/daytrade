"""Single-instance lock tests."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from daytrade.ops import SingleInstanceLock, SingleInstanceLockError


def test_lock_acquires_when_no_one_holds_it(tmp_path):
    lock = SingleInstanceLock("test", lock_dir=tmp_path)
    with lock:
        assert lock.held is True
        assert lock.path.exists()
        assert int(lock.path.read_text()) == os.getpid()
    assert lock.held is False
    assert not lock.path.exists()


def test_lock_releases_on_context_exit_even_on_error(tmp_path):
    lock = SingleInstanceLock("test", lock_dir=tmp_path)
    with pytest.raises(ValueError):
        with lock:
            raise ValueError("boom")
    assert not lock.path.exists()


def test_lock_refuses_when_another_live_process_holds_it(tmp_path):
    """A second instance with a *live* peer's PID must refuse to start."""
    # Spawn a slow child so its PID is provably alive while we test.
    child = subprocess.Popen([sys.executable, "-c",
                              "import time; time.sleep(5)"])
    try:
        # Write the live child PID into the lock file by hand.
        (tmp_path / "live.lock").write_text(str(child.pid), encoding="utf-8")
        lock = SingleInstanceLock("live", lock_dir=tmp_path)
        with pytest.raises(SingleInstanceLockError) as exc_info:
            lock.acquire()
        assert exc_info.value.other_pid == child.pid
        assert "already running" in str(exc_info.value)
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_lock_takes_over_a_stale_file(tmp_path):
    """A lock file pointing at a dead PID is silently taken over."""
    # Pick a PID that does not exist (kill -0 will fail).
    stale = (tmp_path / "stale.lock")
    # Use a clearly-impossible PID number.
    stale.write_text("9999999", encoding="utf-8")
    lock = SingleInstanceLock("stale", lock_dir=tmp_path)
    with lock:
        assert int(lock.path.read_text()) == os.getpid()


def test_lock_rejects_invalid_names():
    with pytest.raises(ValueError):
        SingleInstanceLock("")
    with pytest.raises(ValueError):
        SingleInstanceLock("with/slash")


def test_release_is_idempotent(tmp_path):
    lock = SingleInstanceLock("test", lock_dir=tmp_path)
    lock.acquire()
    lock.release()
    lock.release()  # no-op, must not raise
