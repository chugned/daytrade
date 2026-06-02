"""Historical research lab — fast strategy evaluation over real history.

Downloads real Binance history (read-only public data) and runs the backtest
+ walk-forward engines over years of it, collapsing the research feedback
loop from a 30-day live window to minutes. Research only — no orders.
"""

from __future__ import annotations

from .history import HISTORY_DB_PATH, INTERVAL_MS, HistoryStore, download_history
from .lab import ResearchResult, render_research, run_research

__all__ = [
    "download_history",
    "HistoryStore",
    "HISTORY_DB_PATH",
    "INTERVAL_MS",
    "ResearchResult",
    "run_research",
    "render_research",
]
