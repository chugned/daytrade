"""Cross-cutting runtime helpers: logging and deterministic seeding."""

from __future__ import annotations

import logging
import os
import random

import numpy as np
from rich.logging import RichHandler

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging with a Rich handler (idempotent)."""
    global _CONFIGURED
    numeric = getattr(logging, level.upper(), logging.INFO)
    handler = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
    if _CONFIGURED:
        logging.getLogger().setLevel(numeric)
        return
    logging.basicConfig(
        level=numeric,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[handler],
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under the ``daytrade`` root."""
    return logging.getLogger(f"daytrade.{name}")


def seed_everything(seed: int = 42) -> None:
    """Seed Python and NumPy RNGs for deterministic, reproducible runs.

    Determinism is a first-class requirement here: a research result you
    cannot reproduce is not a research result.
    """
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)


def add_file_logging(
    path: str,
    *,
    max_bytes: int = 50 * 1024 * 1024,  # 50 MB per file
    backup_count: int = 5,  # keep 5 rotations = 250 MB ceiling
) -> None:
    """Attach a SIZE-ROTATING file handler to the root logger.

    QA-CRIT-3: the previous version used a plain ``FileHandler`` with
    no rotation. After 11 days of observer uptime ``logs/daytrade.log``
    reached 373 MB; at ~34 MB/day that would silently consume 12 GB/yr
    and eventually fail ENOSPC writes (Python's logging swallows the
    error). With these defaults the on-disk footprint is bounded at
    ``(backup_count + 1) * max_bytes`` ≈ 300 MB total.
    """
    import os as _os
    from logging.handlers import RotatingFileHandler  # noqa: PLC0415

    _os.makedirs(_os.path.dirname(path) or ".", exist_ok=True)
    root = logging.getLogger()
    abspath = _os.path.abspath(path)
    for handler in root.handlers:
        if (
            isinstance(handler, logging.FileHandler)
            and getattr(handler, "baseFilename", None) == abspath
        ):
            return  # already attached
    file_handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(file_handler)


def apply_runtime(level: str = "INFO", deterministic: bool = True, seed: int = 42) -> None:
    """One-shot runtime setup used by the CLI before any work begins."""
    setup_logging(level)
    if deterministic:
        seed_everything(seed)
