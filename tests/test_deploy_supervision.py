"""Pin the daytrade launchd supervision scripts.

The 2026-06-02 incident: daytrade ran unsupervised and silently died when
its parent shell closed. The fix is deploy/install.sh + deploy/watchdog.sh.
These tests pin the properties that made the OLD static plists unsafe to
load, so they can't silently regress:

  * no CHANGE_ME placeholder (the old plists had an unedited absolute path)
  * an env-pinning wrapper (bare /usr/bin/python3 + relative PYTHONPATH=src
    is why launchd couldn't find site-packages)
  * the learn observer runs under caffeinate (Mac sleep == paused bot)
  * a watchdog service exists and checks the right process + port
  * both scripts are valid bash

These are static-text assertions — they don't load launchd or start
processes, so they're safe in CI.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_INSTALL = _REPO / "deploy" / "install.sh"
_WATCHDOG = _REPO / "deploy" / "watchdog.sh"
_UNINSTALL = _REPO / "deploy" / "uninstall.sh"


def _bash() -> str:
    b = shutil.which("bash")
    if not b:
        pytest.skip("bash not available")
    return b


def test_scripts_exist():
    assert _INSTALL.exists(), "deploy/install.sh missing"
    assert _WATCHDOG.exists(), "deploy/watchdog.sh missing"
    assert _UNINSTALL.exists(), "deploy/uninstall.sh missing"


@pytest.mark.parametrize("script", [_INSTALL, _WATCHDOG, _UNINSTALL])
def test_scripts_are_valid_bash(script):
    """`bash -n` parses without executing — catches syntax errors."""
    result = subprocess.run(
        [_bash(), "-n", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 0, f"{script.name} has syntax errors: {result.stderr}"


def test_install_has_no_changeme_placeholder():
    """The OLD static plists shipped with /Users/CHANGE_ME/... — loading
    them as-is would fail. The generated install must derive the repo path
    at runtime, never hardcode a placeholder."""
    text = _INSTALL.read_text()
    assert "CHANGE_ME" not in text


def test_install_pins_environment():
    """launchd does not pass HOME/PYTHONPATH reliably. The install must
    write an env-pinning wrapper that exports them with ABSOLUTE paths."""
    text = _INSTALL.read_text()
    assert "PYTHONPATH=" in text
    assert 'export HOME=' in text
    # Relative PYTHONPATH=src (the old bug) must not be the wrapper's value.
    assert 'PYTHONPATH="$REPO/src"' in text


def test_canonical_learn_command_present():
    """The canonical learn command must be what gets supervised."""
    text = _INSTALL.read_text()
    assert "daytrade learn --days 30 --interval 60 --real-data" in text


def test_caffeinate_is_decoupled_not_wrapping_the_bot():
    """System-sleep prevention must be its OWN service, NOT wrapped around
    the learn job. caffeinate-as-parent breaks the bot's KeepAlive respawn
    (verified 2026-06-03). Pin that the learn command is python directly
    and caffeinate lives in a separate com.daytrade.caffeinate service."""
    text = _INSTALL.read_text()
    assert "com.daytrade.caffeinate" in text
    # The learn make_plist line must NOT prepend caffeinate.
    for line in text.splitlines():
        if "make_plist com.daytrade.learn" in line:
            # the command continues on the next line(s); check the block
            idx = text.index(line)
            block = text[idx: idx + 300]
            assert "caffeinate" not in block.split("make_plist com.daytrade.dashboard")[0], (
                "learn job must not be wrapped in caffeinate"
            )
            break


def test_install_clears_stale_lock_and_existing_processes():
    """Before launchd starts a fresh process, install must kill any running
    daytrade and clear the single-instance lock — else the new process hits
    SingleInstanceLockError and launchd throttle-loops. Two-writer safety."""
    text = _INSTALL.read_text()
    assert "pkill -f 'daytrade learn'" in text
    assert "learn.pid" in text


def test_install_registers_all_services():
    text = _INSTALL.read_text()
    for label in (
        "com.daytrade.learn",
        "com.daytrade.dashboard",
        "com.daytrade.watchdog",
        "com.daytrade.caffeinate",
    ):
        assert label in text, f"install.sh does not register {label}"


def test_install_kickstarts_after_bootstrap():
    """RunAtLoad doesn't reliably fire in gui/ domain — install must
    kickstart the services explicitly so a fresh install actually runs."""
    text = _INSTALL.read_text()
    assert "launchctl kickstart" in text


def test_watchdog_checks_learn_process_and_dashboard_port():
    text = _WATCHDOG.read_text()
    assert "pgrep -f 'daytrade learn'" in text
    assert "8000" in text  # default dashboard port
    # Watchdog must kickstart, not bootstrap (the services already exist).
    assert "launchctl kickstart" in text


def test_old_static_plists_removed():
    """The buggy CHANGE_ME static plists must be gone — install.sh now
    generates correct ones dynamically. Leaving them invites someone to
    cp+load the broken version."""
    legacy_dir = _REPO / "deploy" / "launchd"
    if legacy_dir.exists():
        stale = list(legacy_dir.glob("com.daytrade.*.plist"))
        assert not stale, (
            f"stale static plists still present: {[p.name for p in stale]} — "
            "install.sh generates these now; remove the static copies"
        )
