"""Regression tests for mission control's bot-process matcher.

Bug history: ``find_bot_processes`` originally matched any process whose
command-line contained the bot's ``process_match`` substring. Each bot
runs under ``caffeinate -s python -m <bot> ...``, so BOTH the caffeinate
parent AND the python child matched — giving misleading ``procs=2`` and,
worse, masking a dead bot when the python child crashed but caffeinate
hadn't been reaped yet (mission control still saw procs=1, reported
healthy).

These tests pin the corrected behaviour: the matcher discriminates on
the *executable* (first token), keeping python interpreters and
filtering caffeinate.
"""

from __future__ import annotations

from pathlib import Path

from daytrade.mission_control.app import Bot, find_bot_processes


def _bot(needles):
    return Bot(name="x", project_root=Path("/tmp"), process_match=list(needles))


# -- the actual live commands seen on the host -----------------------------

NIGHTTRADE_PYTHON = (
    "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/"
    "Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/"
    "Python -m nighttrade observe --live --interval 300"
)
NIGHTTRADE_CAFFEINATE = (
    "/usr/bin/caffeinate -s /usr/bin/python3 -m nighttrade observe "
    "--live --interval 300"
)
DAYTRADE_PYTHON = (
    "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/"
    "Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/"
    "Python -m daytrade learn --days 30 --interval 60 --real-data"
)
DAYTRADE_CAFFEINATE = (
    "/usr/bin/caffeinate -s python -m daytrade learn --days 30"
)


def _snap(*cmds):
    return [{"command": c} for c in cmds]


# -- contract --------------------------------------------------------------

def test_returns_python_interpreter_not_caffeinate_wrapper():
    snap = _snap(NIGHTTRADE_PYTHON, NIGHTTRADE_CAFFEINATE)
    got = find_bot_processes(_bot(["nighttrade observe"]), snap)
    assert len(got) == 1
    assert got[0]["command"] == NIGHTTRADE_PYTHON


def test_filters_caffeinate_even_when_its_args_contain_python():
    # caffeinate /usr/bin/python3 — the word 'python3' is in the cmdline,
    # but the executable is caffeinate, so it must NOT match.
    snap = _snap(NIGHTTRADE_CAFFEINATE)
    assert find_bot_processes(_bot(["nighttrade observe"]), snap) == []


def test_keeps_developer_launched_python_too():
    # A dev launched the bot directly from a shell with /usr/local/bin/python3
    dev = "/usr/local/bin/python3 -m nighttrade observe --live"
    snap = _snap(NIGHTTRADE_PYTHON, dev)
    got = find_bot_processes(_bot(["nighttrade observe"]), snap)
    assert len(got) == 2


def test_filters_unrelated_processes_mentioning_the_bot_name():
    snap = _snap(
        "/usr/bin/vim src/nighttrade/observatory/observer.py",
        "grep -r 'nighttrade observe' src/",
        "/bin/zsh -c 'tail -f ~/nighttrade/logs/observer.log'",
    )
    assert find_bot_processes(_bot(["nighttrade observe"]), snap) == []


def test_multi_bot_isolation():
    """daytrade and nighttrade matchers don't cross-pollinate."""
    snap = _snap(NIGHTTRADE_PYTHON, DAYTRADE_PYTHON)
    nt = find_bot_processes(_bot(["nighttrade observe"]), snap)
    dt = find_bot_processes(_bot(["daytrade learn", "daytrade observe"]), snap)
    assert len(nt) == 1 and "nighttrade" in nt[0]["command"]
    assert len(dt) == 1 and "daytrade" in dt[0]["command"]


def test_empty_snapshot_returns_empty_list():
    assert find_bot_processes(_bot(["nighttrade observe"]), []) == []


def test_python_capitalised_in_apple_framework_path():
    """Apple's framework path uses capital ``Python``, not ``python``.
    Case-insensitive matching is required."""
    apple_only = NIGHTTRADE_PYTHON  # already capital
    got = find_bot_processes(_bot(["nighttrade observe"]), _snap(apple_only))
    assert len(got) == 1
