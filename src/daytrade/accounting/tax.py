"""Tax-reporting CSV export.

Exports each closed paper trade as a disposal event in a flat CSV — the shape
most tax tools expect. This is a *bookkeeping convenience for simulated data*,
not tax advice and not a filing. Every row is from paper trading; no real
disposal occurred.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from ..paper.broker import TradeRecord

_CSV_COLUMNS = [
    "symbol",
    "quantity",
    "opened_at",
    "closed_at",
    "cost_basis",
    "proceeds",
    "fees",
    "gain_loss",
    "holding_seconds",
]

_DISCLAIMER = (
    "# daytrade tax-reporting export — SIMULATED PAPER TRADES ONLY. "
    "Not tax advice, not a filing, not real disposals. Educational use."
)


def trade_to_tax_row(trade: TradeRecord) -> dict:
    """Convert one closed trade into a tax-CSV row."""
    cost_basis = trade.entry_price * trade.quantity
    proceeds = trade.exit_price * trade.quantity
    holding = (trade.closed_at - trade.opened_at).total_seconds()
    return {
        "symbol": trade.symbol,
        "quantity": f"{trade.quantity:.10f}",
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat(),
        "cost_basis": f"{cost_basis:.2f}",
        "proceeds": f"{proceeds:.2f}",
        "fees": f"{trade.fees:.2f}",
        "gain_loss": f"{trade.pnl:.2f}",
        "holding_seconds": f"{holding:.0f}",
    }


def export_tax_csv(trades: List[TradeRecord], path: Path | str) -> Path:
    """Write closed paper trades to a tax-reporting CSV at ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(_DISCLAIMER + "\n")
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for trade in trades:
            writer.writerow(trade_to_tax_row(trade))
    return path
