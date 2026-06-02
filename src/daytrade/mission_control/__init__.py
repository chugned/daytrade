"""Mission control — unified view of every bot on this host.

Reads-only. Pulls process state via ``ps``, reads each bot's log tail
and SQLite observatory DB, and renders one single-page UI.
"""

from .app import create_app

__all__ = ["create_app"]
