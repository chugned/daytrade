#!/usr/bin/env python3
"""Pre-flight checklist — verify the engineering primitives are in place.

Runs through the items the Secure branch installs and prints a coloured
PASS/FAIL report. Intended to be run before any live-deployment step. It
makes NO real-money calls and changes NO state.

Usage::

    PYTHONPATH=src python3 scripts/preflight.py

Exit code 0 if every check passes, 1 otherwise.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Callable, List, Tuple

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))


CheckResult = Tuple[bool, str]


def check_ops_imports() -> CheckResult:
    """All Secure-branch primitives import cleanly."""
    try:
        mod = importlib.import_module("daytrade.ops")
        expected = {
            "SingleInstanceLock", "OrderIDRegistry", "generate_client_order_id",
            "reconcile_paper_state", "build_notifier", "inspect_key",
            "assert_trade_only", "attach_remote_handler_from_env",
        }
        missing = expected - set(dir(mod))
        if missing:
            return False, f"missing exports: {sorted(missing)}"
        return True, f"all {len(expected)} primitives importable"
    except Exception as exc:  # noqa: BLE001
        return False, f"import error: {exc!r}"


def check_lock_works() -> CheckResult:
    """A SingleInstanceLock can acquire and release a temp file."""
    try:
        from daytrade.ops import SingleInstanceLock
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with SingleInstanceLock("preflight", lock_dir=Path(td)) as lock:
                assert lock.held
            assert not lock.held
        return True, "acquire/release cycle works"
    except Exception as exc:  # noqa: BLE001
        return False, f"lock failed: {exc!r}"


def check_trade_only_validator_refuses_withdrawal_keys() -> CheckResult:
    """assert_trade_only raises on a key with withdrawal permission."""
    try:
        from daytrade.ops import KeyPermissions, assert_trade_only, \
            WithdrawalPermissionForbidden
        bad = KeyPermissions(
            ip_restricted=True, can_trade=True, can_withdraw=True,
            can_internal_transfer=False, enable_spot_and_margin_trading=True,
            enable_futures=False, enable_universal_transfer=False)
        try:
            assert_trade_only(bad)
            return False, "validator FAILED to refuse a withdrawal-enabled key"
        except WithdrawalPermissionForbidden:
            return True, "validator correctly refused a withdrawal-enabled key"
    except Exception as exc:  # noqa: BLE001
        return False, f"check raised unexpectedly: {exc!r}"


def check_idempotent_order_ids() -> CheckResult:
    """Same inputs → same id; duplicates are rejected."""
    try:
        from datetime import datetime, timezone
        from daytrade.ops import OrderIDRegistry, generate_client_order_id
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        a = generate_client_order_id("BTCUSDT", "buy", ts)
        b = generate_client_order_id("BTCUSDT", "buy", ts)
        assert a == b, "same inputs should produce the same id"
        reg = OrderIDRegistry()
        assert reg.register(a) is True
        assert reg.register(a) is False
        return True, f"id stable + duplicate rejected ({a})"
    except Exception as exc:  # noqa: BLE001
        return False, f"check failed: {exc!r}"


def check_paper_only_invariant() -> CheckResult:
    """daytrade.paper still has no live broker class wired in."""
    try:
        from daytrade.paper import broker
        # PaperBroker should exist, LiveBroker should NOT.
        assert hasattr(broker, "PaperBroker"), "PaperBroker missing"
        forbidden = [n for n in dir(broker)
                     if "live" in n.lower() and "broker" in n.lower()]
        if forbidden:
            return False, f"live broker found in paper module: {forbidden}"
        return True, "paper module is paper-only"
    except Exception as exc:  # noqa: BLE001
        return False, f"check failed: {exc!r}"


def check_kill_switch_tests_present() -> CheckResult:
    """The kill-switch test file from Secure #3 must exist."""
    p = _REPO / "tests" / "test_kill_switches.py"
    if not p.exists():
        return False, "tests/test_kill_switches.py missing"
    return True, "kill-switch tests present"


def check_watchdog_units_present() -> CheckResult:
    """LaunchAgent and systemd unit files must ship in deploy/."""
    needed = [
        _REPO / "deploy" / "launchd" / "com.daytrade.learn.plist",
        _REPO / "deploy" / "launchd" / "com.daytrade.dashboard.plist",
        _REPO / "deploy" / "systemd" / "daytrade-learn.service",
        _REPO / "deploy" / "systemd" / "daytrade-dashboard.service",
    ]
    missing = [p for p in needed if not p.exists()]
    if missing:
        return False, f"missing: {[p.name for p in missing]}"
    return True, "all watchdog units shipped"


def check_freqtrade_port_present() -> CheckResult:
    """The freqtrade port artifacts must be in the repo."""
    needed = [
        _REPO / "freqtrade-port" / "strategies" / "DaytradeStrategy.py",
        _REPO / "freqtrade-port" / "config.json",
        _REPO / "freqtrade-port" / "README.md",
    ]
    missing = [p for p in needed if not p.exists()]
    if missing:
        return False, f"missing: {[p.name for p in missing]}"
    return True, "freqtrade port artifacts shipped"


def check_real_money_risk_docs_present() -> CheckResult:
    """The risk inventory must be readable."""
    p = _REPO / "docs" / "REAL-MONEY-RISKS.md"
    if not p.exists():
        return False, "docs/REAL-MONEY-RISKS.md missing"
    return True, "risk doc present"


def check_secure_branch_doc_present() -> CheckResult:
    p = _REPO / "docs" / "SECURE-BRANCH.md"
    if not p.exists():
        return False, "docs/SECURE-BRANCH.md missing"
    return True, "Secure-branch doc present"


# Order matters — earliest failures cascade.
CHECKS: List[Tuple[str, Callable[[], CheckResult]]] = [
    ("daytrade.ops package importable",        check_ops_imports),
    ("Single-instance lock works",             check_lock_works),
    ("Trade-only validator refuses withdraw",  check_trade_only_validator_refuses_withdrawal_keys),
    ("Order-id determinism + dedup",           check_idempotent_order_ids),
    ("Paper-only invariant in daytrade.paper", check_paper_only_invariant),
    ("Kill-switch tests present",              check_kill_switch_tests_present),
    ("Watchdog supervisor units present",      check_watchdog_units_present),
    ("Freqtrade port artifacts present",       check_freqtrade_port_present),
    ("REAL-MONEY-RISKS.md present",            check_real_money_risk_docs_present),
    ("SECURE-BRANCH.md present",               check_secure_branch_doc_present),
]


def main() -> int:
    print("\n=== daytrade pre-flight checklist ===\n")
    passes, fails = 0, 0
    for label, check in CHECKS:
        ok, detail = check()
        if ok:
            passes += 1
            print(f"  ✅  {label:48} — {detail}")
        else:
            fails += 1
            print(f"  ❌  {label:48} — {detail}")
    print(f"\n  {passes} passed / {fails} failed\n")
    if fails:
        print("Pre-flight FAILED. Do not proceed to live deployment.\n")
        return 1
    print("Pre-flight PASSED. The engineering primitives are in place.")
    print("Note: passing pre-flight does NOT imply strategy edge is proven.")
    print("Strategy evidence is a SEPARATE prerequisite — see docs/REAL-MONEY-RISKS.md.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
