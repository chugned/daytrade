"""QA-CRIT-3 regression — log files are bounded.

Before the fix:
  - daytrade.log had no rotation (374 MB after 11 days).
  - db-writes.log had no rotation (184 MB after 11 days).

After the fix:
  - daytrade.log uses RotatingFileHandler (50MB × 5 = 250 MB cap).
  - db-writes.log uses ObservatoryDB._rotate_writelog_if_needed
    (50MB × 3 backups = 200 MB cap).
"""

from __future__ import annotations

import logging

import pytest

from daytrade.observatory.database import ObservatoryDB
from daytrade.runtime import add_file_logging


def test_add_file_logging_attaches_rotating_handler(tmp_path):
    from logging.handlers import RotatingFileHandler

    # Clean any handlers a previous test may have left behind
    root = logging.getLogger()
    pre_handlers = list(root.handlers)
    for h in pre_handlers:
        if isinstance(h, logging.FileHandler):
            root.removeHandler(h)

    log_path = tmp_path / "daytrade.log"
    add_file_logging(str(log_path), max_bytes=1024, backup_count=3)
    matched = [h for h in root.handlers
               if isinstance(h, RotatingFileHandler)
               and h.baseFilename == str(log_path.resolve())]
    assert matched, "expected a RotatingFileHandler attached"
    rh = matched[0]
    assert rh.maxBytes == 1024
    assert rh.backupCount == 3
    # Cleanup
    root.removeHandler(rh)
    rh.close()


def test_writelog_rotates_when_over_cap(tmp_path):
    """Force the db-writes log past the cap and verify rotation kicks in."""
    db_path = tmp_path / "obs.db"
    db = ObservatoryDB(path=db_path)
    try:
        # Make the cap tiny for the test
        db._WRITELOG_MAX_BYTES = 256
        db._WRITELOG_BACKUP_COUNT = 2

        # Hammer the writelog until it definitely rotated at least once
        for i in range(200):
            db._writelog("INSERT", "test_table", i,
                         {"symbol": "BTCUSDT", "price": i})

        live = db._writelog_path
        bak1 = live.with_suffix(live.suffix + ".1")
        assert live.exists()
        assert bak1.exists(), "expected at least one rotation"
        # Live file is at-or-below cap (next write will rotate it again)
        assert live.stat().st_size <= db._WRITELOG_MAX_BYTES + 200
    finally:
        db.close()


def test_writelog_rotation_caps_total_disk(tmp_path):
    """After many writes, total disk used by all rotations stays bounded."""
    db_path = tmp_path / "obs.db"
    db = ObservatoryDB(path=db_path)
    try:
        db._WRITELOG_MAX_BYTES = 1024
        db._WRITELOG_BACKUP_COUNT = 3

        for i in range(5000):
            db._writelog("INSERT", "test_table", i,
                         {"symbol": "BTCUSDT", "price": i,
                          "detail": "x" * 50})

        live = db._writelog_path
        total = live.stat().st_size if live.exists() else 0
        for n in range(1, db._WRITELOG_BACKUP_COUNT + 1):
            bak = live.with_suffix(live.suffix + f".{n}")
            if bak.exists():
                total += bak.stat().st_size

        cap = db._WRITELOG_MAX_BYTES * (db._WRITELOG_BACKUP_COUNT + 1)
        # Allow up to 2x cap to absorb the final pre-rotation overflow
        assert total <= 2 * cap, f"total {total} exceeds bounded ceiling"
    finally:
        db.close()
