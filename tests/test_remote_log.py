"""Remote log handler tests."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from daytrade.ops.remote_log import (
    RemoteLogHandler,
    attach_remote_handler_from_env,
    attach_rotating_file_handler,
)


def test_handler_batches_and_flushes_at_batch_size():
    with patch("daytrade.ops.remote_log.httpx.post") as post:
        h = RemoteLogHandler("https://example.test/logs",
                             batch_size=3, flush_interval=999)
        try:
            for i in range(3):
                rec = logging.LogRecord("t", logging.WARNING, "x", 1,
                                        f"msg {i}", None, None)
                h.emit(rec)
        finally:
            h.close()
    assert post.called, "should POST when the batch fills up"
    # Last call posts the 3 messages.
    payload = post.call_args.kwargs["json"]
    assert "records" in payload
    assert all("message" in r for r in payload["records"])


def test_handler_swallows_http_errors_silently():
    """A logging handler that crashes is worse than one that misses messages."""
    with patch("daytrade.ops.remote_log.httpx.post",
               side_effect=RuntimeError("network down")):
        h = RemoteLogHandler("https://example.test/logs",
                             batch_size=1, flush_interval=999)
        try:
            rec = logging.LogRecord("t", logging.WARNING, "x", 1, "boom",
                                    None, None)
            # Must not raise.
            h.emit(rec)
        finally:
            h.close()


def test_handler_refuses_empty_url():
    with pytest.raises(ValueError):
        RemoteLogHandler("")


def test_attach_from_env_is_no_op_without_var(monkeypatch):
    monkeypatch.delenv("DAYTRADE_REMOTE_LOG_URL", raising=False)
    assert attach_remote_handler_from_env() is None


def test_attach_from_env_returns_handler(monkeypatch):
    monkeypatch.setenv("DAYTRADE_REMOTE_LOG_URL", "https://example.test/logs")
    handler = attach_remote_handler_from_env()
    try:
        assert isinstance(handler, RemoteLogHandler)
    finally:
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()


def test_rotating_file_handler_is_idempotent(tmp_path):
    p = tmp_path / "rot.log"
    attach_rotating_file_handler(p)
    n_after_first = len(logging.getLogger().handlers)
    attach_rotating_file_handler(p)
    assert len(logging.getLogger().handlers) == n_after_first
    # Cleanup: detach handlers we just attached for this test.
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "baseFilename", None) == str(p.resolve()):
            root.removeHandler(h)
            h.close()
