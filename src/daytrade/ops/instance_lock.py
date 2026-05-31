"""Single-instance lock — refuse to start when another instance is alive.

Background: the daytrade bot has, twice, ended up with two processes running
concurrently after a noisy restart (a previous instance survived ``pkill``).
Two bot processes sharing one SQLite file is harmless-ish in paper but is
catastrophic for live execution (double orders, conflicting state). This
lock makes that impossible.

Mechanism: a per-name PID file under ``data/``. On enter, if a stale or
crashed instance left a lock behind, we take it over; if a live instance
holds it, we refuse to start with a clear error.

Paper / simulation only — this is an operational guard, not a trading change.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCK_DIR = _REPO_ROOT / "data" / "locks"


class SingleInstanceLockError(RuntimeError):
    """Raised when another live instance already holds the lock."""

    def __init__(self, name: str, other_pid: int, lock_path: Path) -> None:
        super().__init__(
            f"another '{name}' instance is already running (pid={other_pid}). "
            f"Stop it first, or remove the stale lock at {lock_path}.")
        self.name = name
        self.other_pid = other_pid
        self.lock_path = lock_path


def _pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` exists on this machine."""
    if pid <= 0:
        return False
    try:
        # signal 0 just probes — does not actually send anything.
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but is owned by another user — treat as alive.
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    return True


class SingleInstanceLock:
    """Context manager that holds a per-name PID lock.

    Usage::

        with SingleInstanceLock("learn"):
            run_observer()

    If another live instance holds the same lock, raises
    :class:`SingleInstanceLockError`. A stale lock (PID not alive) is
    silently taken over — a previous crashed process is not a reason to
    refuse to start.
    """

    def __init__(self, name: str, lock_dir: Optional[Path] = None) -> None:
        if not name or "/" in name or "\\" in name:
            raise ValueError(f"invalid lock name: {name!r}")
        self.name = name
        self._dir = Path(lock_dir) if lock_dir is not None else _LOCK_DIR
        self.path = self._dir / f"{name}.lock"
        self._acquired = False

    # -- context-manager --------------------------------------------------

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    # -- explicit API ------------------------------------------------------

    def acquire(self) -> None:
        """Take the lock or raise :class:`SingleInstanceLockError`."""
        self._dir.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            other_pid = self._read_pid()
            if other_pid and _pid_alive(other_pid) and other_pid != os.getpid():
                raise SingleInstanceLockError(self.name, other_pid, self.path)
            # else: stale or our own pid — take over.
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        self._acquired = True

    def release(self) -> None:
        """Release the lock if we currently hold it."""
        if not self._acquired:
            return
        try:
            if self.path.exists() and self._read_pid() == os.getpid():
                self.path.unlink()
        except OSError:
            pass
        finally:
            self._acquired = False

    # -- helpers -----------------------------------------------------------

    def _read_pid(self) -> Optional[int]:
        try:
            text = self.path.read_text(encoding="utf-8").strip()
            return int(text) if text else None
        except (OSError, ValueError):
            return None

    @property
    def held(self) -> bool:
        return self._acquired
