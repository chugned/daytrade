"""Notifier tests."""

from __future__ import annotations

import logging
from unittest.mock import patch

from daytrade.ops.notify import (
    Level,
    LogNotifier,
    NtfyNotifier,
    TelegramNotifier,
    build_notifier,
)


def test_log_notifier_writes_at_the_right_level(caplog):
    caplog.set_level(logging.INFO, logger="daytrade.notify")
    LogNotifier().notify("Opened BTC", "qty 0.05", Level.INFO)
    LogNotifier().notify("Critical!", "kill switch", Level.CRITICAL)
    text = caplog.text
    assert "Opened BTC" in text
    assert "Critical!" in text


def test_build_notifier_default_is_log(monkeypatch):
    monkeypatch.delenv("DAYTRADE_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DAYTRADE_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DAYTRADE_NTFY_TOPIC", raising=False)
    assert isinstance(build_notifier(), LogNotifier)


def test_build_notifier_picks_telegram_when_configured(monkeypatch):
    monkeypatch.setenv("DAYTRADE_TELEGRAM_BOT_TOKEN", "token123")
    monkeypatch.setenv("DAYTRADE_TELEGRAM_CHAT_ID", "456")
    notifier = build_notifier()
    assert isinstance(notifier, TelegramNotifier)
    assert notifier.bot_token == "token123" and notifier.chat_id == "456"


def test_build_notifier_picks_ntfy_when_topic_set(monkeypatch):
    monkeypatch.delenv("DAYTRADE_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DAYTRADE_NTFY_TOPIC", "my-topic")
    notifier = build_notifier()
    assert isinstance(notifier, NtfyNotifier)
    assert notifier.topic == "my-topic"


def test_telegram_send_does_an_http_call_to_the_bot_api():
    notifier = TelegramNotifier(bot_token="abc", chat_id="789")
    with patch("daytrade.ops.notify.httpx.post") as post:
        notifier.notify("Trade opened", "BTC qty 0.05", Level.INFO)
    assert post.called
    (url,) = post.call_args.args
    assert "api.telegram.org/botabc/sendMessage" in url
    payload = post.call_args.kwargs["json"]
    assert payload["chat_id"] == "789"
    assert "Trade opened" in payload["text"]


def test_ntfy_send_posts_to_topic_url():
    notifier = NtfyNotifier(topic="t")
    with patch("daytrade.ops.notify.httpx.post") as post:
        notifier.notify("Hi", "world", Level.WARN)
    assert post.called
    (url,) = post.call_args.args
    assert url.endswith("/t")
    assert post.call_args.kwargs["headers"]["Title"] == "Hi"
    assert post.call_args.kwargs["headers"]["Priority"] == "high"


def test_telegram_swallows_http_errors_and_falls_back_to_log(caplog):
    """A notifier failure must never break the calling code path."""
    caplog.set_level(logging.WARNING, logger="daytrade.notify")
    notifier = TelegramNotifier(bot_token="abc", chat_id="789")
    with patch("daytrade.ops.notify.httpx.post", side_effect=RuntimeError("net")):
        notifier.notify("Trade opened", "BTC", Level.INFO)
    assert "telegram notify failed" in caplog.text
