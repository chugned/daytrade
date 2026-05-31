"""Lightweight checks on the freqtrade-port artifacts.

We cannot fully test the strategy file inside this repo because importing
it requires the freqtrade package, which is a heavy external dependency
that lives in its own venv on the eventual VPS. What we *can* do, in this
repo, is verify:

  - the strategy file parses as valid Python (no syntax bitrot)
  - the config.json is well-formed JSON
  - the README references the documented dependencies

These guarantees catch the most common "edited and didn't run it"
regressions without forcing freqtrade as a test-time dep.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

_PORT = Path(__file__).resolve().parents[1] / "freqtrade-port"


def test_strategy_file_is_valid_python():
    src = (_PORT / "strategies" / "DaytradeStrategy.py").read_text("utf-8")
    tree = ast.parse(src)
    classes = [n.name for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef)]
    assert "DaytradeStrategy" in classes


def test_strategy_file_declares_the_four_gates():
    src = (_PORT / "strategies" / "DaytradeStrategy.py").read_text("utf-8")
    for hook in ("populate_indicators", "populate_entry_trend",
                 "populate_exit_trend", "custom_stoploss", "custom_exit",
                 "confirm_trade_entry"):
        assert hook in src, f"strategy missing freqtrade hook: {hook}"
    for gate in ("_regime_gate_passes", "_calibration_gate_passes",
                 "_meta_gate_passes"):
        assert gate in src, f"strategy missing gate: {gate}"


def test_config_is_valid_json_and_dry_run_by_default():
    payload = json.loads((_PORT / "config.json").read_text("utf-8"))
    assert payload["dry_run"] is True, "live execution must be opt-in"
    # Exchange credentials must be blank in the committed config.
    assert payload["exchange"]["key"] == "", "no key may be committed"
    assert payload["exchange"]["secret"] == "", "no secret may be committed"
    assert payload["strategy"] == "DaytradeStrategy"
    assert "MATIC/USDT" in payload["exchange"]["pair_blacklist"]
    assert "RNDR/USDT" in payload["exchange"]["pair_blacklist"]


def test_readme_links_to_safety_docs():
    text = (_PORT / "README.md").read_text("utf-8")
    assert "REAL-MONEY-RISKS.md" in text
    assert "SECURE-BRANCH.md" in text
