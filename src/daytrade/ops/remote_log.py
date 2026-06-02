"""Remote log forwarding — survive host loss without losing forensics.

If the VPS / Mac running the bot dies (disk corruption, accidental wipe,
provider outage), the local ``logs/daytrade.log`` may go with it. This
module adds two affordances:

* :class:`RemoteLogHandler`           — buffers log records and POSTs them
                                        as JSON batches to a webhook /
                                        generic HTTP collector.
* :func:`attach_rotating_file_handler` — drop-in replacement for plain
                                          FileHandler that rotates daily
                                          and keeps 14 days locally.

Both are env-driven and entirely optional. Failures never break the
calling code; logging must not become a vector for crashes.

Configure:
    DAYTRADE_REMOTE_LOG_URL    — HTTP endpoint that accepts JSON POSTs
    DAYTRADE_REMOTE_LOG_LEVEL  — minimum level to ship (default WARNING)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import List, Optional

import httpx

_log = logging.getLogger("daytrade.remote_log")


class RemoteLogHandler(logging.Handler):
    """Batched HTTP POST handler for shipping logs off-host.

    Records are buffered in memory and flushed when the batch is full OR
    ``flush_interval`` seconds pass since the oldest pending record. A
    background flusher thread handles the time-based flush.
    """

    def __init__(
        self,
        url: str,
        *,
        batch_size: int = 20,
        flush_interval: float = 5.0,
        timeout: float = 4.0,
        level: int = logging.WARNING,
    ) -> None:
        super().__init__(level=level)
        if not url:
            raise ValueError("remote log url is required")
        self.url = url
        self.batch_size = max(1, int(batch_size))
        self.flush_interval = float(flush_interval)
        self.timeout = float(timeout)
        self._buf: List[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._flush_loop, name="daytrade-remote-log", daemon=True
        )
        self._thread.start()

    # logging.Handler API ---------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = {
                "ts": record.created,
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
            }
            with self._lock:
                self._buf.append(payload)
                full = len(self._buf) >= self.batch_size
            if full:
                self._flush()
        except Exception:  # noqa: BLE001 - logging must never crash callers
            pass

    def close(self) -> None:
        try:
            self._stop.set()
            self._flush()
        finally:
            super().close()

    # internal --------------------------------------------------------------

    def _flush(self) -> None:
        with self._lock:
            batch, self._buf = self._buf, []
        if not batch:
            return
        try:
            httpx.post(self.url, json={"records": batch}, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            _log.debug("remote log flush failed (%d records): %s", len(batch), exc)

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.flush_interval)
            self._flush()


def attach_rotating_file_handler(
    path: Path | str,
    *,
    keep_days: int = 14,
    level: int = logging.INFO,
) -> None:
    """Attach a daily-rotating file handler to the root logger.

    Idempotent: a duplicate attachment for the same path is a no-op.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    abspath = str(path.resolve())
    for handler in root.handlers:
        if (
            isinstance(handler, TimedRotatingFileHandler)
            and getattr(handler, "baseFilename", None) == abspath
        ):
            return
    handler = TimedRotatingFileHandler(
        filename=abspath,
        when="midnight",
        interval=1,
        backupCount=keep_days,
        encoding="utf-8",
        utc=True,
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


def attach_remote_handler_from_env() -> Optional[RemoteLogHandler]:
    """If ``DAYTRADE_REMOTE_LOG_URL`` is set, attach the remote handler.

    Returns the handler (or None) so callers can manage its lifetime
    if they want; otherwise it lives for the process.
    """
    url = os.environ.get("DAYTRADE_REMOTE_LOG_URL", "").strip()
    if not url:
        return None
    level_name = os.environ.get("DAYTRADE_REMOTE_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    handler = RemoteLogHandler(url, level=level)
    logging.getLogger().addHandler(handler)
    return handler
