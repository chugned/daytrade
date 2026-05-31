"""Push-notification layer — Telegram / ntfy / log.

A live trading bot needs ears. When a position opens, a kill-switch fires,
or an error knocks the cycle over, you want to know on your phone — not
discover it next time you happen to look at the dashboard.

This module ships three backends, all behind one tiny interface:

* :class:`LogNotifier`        — default. Just logs. Always available.
* :class:`TelegramNotifier`   — sends via the Telegram Bot API.
* :class:`NtfyNotifier`       — sends via https://ntfy.sh (no account needed).

The right backend is picked from environment variables by :func:`build_notifier`
so changing it never requires a code edit. Failures *never* break the calling
path — a notifier that fails just logs and returns.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol

import httpx

_log = logging.getLogger("daytrade.notify")


class Level(str, Enum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class Notifier(Protocol):
    """The single notification interface used throughout the codebase."""

    def notify(self, title: str, message: str,
               level: Level = Level.INFO) -> None: ...


@dataclass
class LogNotifier:
    """Default backend — logs only. Always works; never sends anything."""

    name: str = "log"

    def notify(self, title: str, message: str,
               level: Level = Level.INFO) -> None:
        log_fn = {
            Level.INFO: _log.info,
            Level.WARN: _log.warning,
            Level.CRITICAL: _log.critical,
        }.get(level, _log.info)
        log_fn("[%s] %s — %s", level.value.upper(), title, message)


@dataclass
class TelegramNotifier:
    """Sends via the Telegram Bot API.

    Configure with env vars:
      DAYTRADE_TELEGRAM_BOT_TOKEN — bot token from @BotFather
      DAYTRADE_TELEGRAM_CHAT_ID   — your chat id (numeric)
    """

    bot_token: str
    chat_id: str
    name: str = "telegram"

    def notify(self, title: str, message: str,
               level: Level = Level.INFO) -> None:
        icon = {"info": "🟢", "warn": "🟡", "critical": "🔴"}[level.value]
        text = f"{icon} *{title}*\n{message}"
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            httpx.post(url, json={"chat_id": self.chat_id, "text": text,
                                  "parse_mode": "Markdown"},
                       timeout=5.0)
        except Exception as exc:  # noqa: BLE001 - notify must never crash callers
            _log.warning("telegram notify failed: %s", exc)
            LogNotifier().notify(title, message, level)


@dataclass
class NtfyNotifier:
    """Sends via https://ntfy.sh — no account, just a topic name.

    Configure with env var:
      DAYTRADE_NTFY_TOPIC   — e.g. "daytrade-alerts-yourname-pick-a-secret"
      DAYTRADE_NTFY_SERVER  — optional; defaults to https://ntfy.sh
    """

    topic: str
    server: str = "https://ntfy.sh"
    name: str = "ntfy"

    def notify(self, title: str, message: str,
               level: Level = Level.INFO) -> None:
        priority = {"info": "default", "warn": "high",
                    "critical": "urgent"}[level.value]
        try:
            httpx.post(
                f"{self.server.rstrip('/')}/{self.topic}",
                data=message.encode("utf-8"),
                headers={"Title": title, "Priority": priority,
                         "Tags": level.value},
                timeout=5.0,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("ntfy notify failed: %s", exc)
            LogNotifier().notify(title, message, level)


def build_notifier() -> Notifier:
    """Pick the right notifier from environment variables.

    Order of preference: Telegram (if fully configured) -> ntfy (if topic set)
    -> log (always works). Env-driven so swapping does not need a code edit.
    """
    tg_token = os.environ.get("DAYTRADE_TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("DAYTRADE_TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        return TelegramNotifier(bot_token=tg_token, chat_id=tg_chat)

    ntfy_topic = os.environ.get("DAYTRADE_NTFY_TOPIC", "").strip()
    if ntfy_topic:
        ntfy_server = os.environ.get("DAYTRADE_NTFY_SERVER", "https://ntfy.sh")
        return NtfyNotifier(topic=ntfy_topic, server=ntfy_server)

    return LogNotifier()
