"""JSON and Markdown serialization of reports.

JSON is for machines (and reproducibility diffs); Markdown is for humans
reading a saved report later. Both always carry the safety/realism notices.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

from ..models import BacktestMetrics

if TYPE_CHECKING:
    from ..backtest.engine import BacktestResult
    from ..pipeline import PipelineResult

_SAFETY_NOTE = (
    "EDUCATIONAL ONLY — this platform cannot place real trades. "
    "All execution is simulated. Backtests are NOT reality."
)


def decision_report_dict(result: PipelineResult) -> Dict[str, Any]:
    """Serialize a pipeline result to a plain JSON-ready dict."""
    d = result.decision
    return {
        "safety_note": _SAFETY_NOTE,
        "symbol": d.symbol,
        "timestamp": d.timestamp.isoformat(),
        "reference_price": result.reference_price,
        "atr": result.atr,
        "decision": {
            "action": d.action.value,
            "confidence": d.confidence,
            "fused_score": d.fused_score,
            "entry": d.entry,
            "stop": d.stop,
            "target": d.target,
            "risk_reward": d.risk_reward,
            "reasoning": d.reasoning,
        },
        "kill_switch": {
            "active": result.kill_switch.active,
            "reasons": result.kill_switch.reasons,
        },
        "signals": {
            "technical": json.loads(result.technical.to_json()),
            "microstructure": json.loads(result.microstructure.to_json()),
            "macro": json.loads(result.macro.to_json()),
            "ml": json.loads(result.ml.to_json()),
        },
    }


def decision_report_markdown(result: PipelineResult) -> str:
    """Render a pipeline result as a Markdown report."""
    d = result.decision
    lines = [
        f"# Trading decision — {d.symbol}",
        "",
        f"> {_SAFETY_NOTE}",
        "",
        f"- **Timestamp:** {d.timestamp.isoformat()}",
        f"- **Reference price:** {result.reference_price:,.2f}",
        f"- **Action:** **{d.action.value.upper()}**",
        f"- **Confidence:** {d.confidence:.2f}",
        f"- **Fused score:** {d.fused_score:+.3f}",
    ]
    if d.is_actionable:
        lines += [
            f"- **Entry:** {d.entry:,.2f}",
            f"- **Stop:** {d.stop:,.2f}",
            f"- **Target:** {d.target:,.2f}",
            f"- **Risk/Reward:** {d.risk_reward:.2f}",
        ]
    lines += [
        "",
        "## Analysis layers",
        "",
        "| Layer | Bias | Score | Confidence |",
        "|-------|------|-------|------------|",
    ]
    for name, sig in (
        ("Technical", result.technical),
        ("Microstructure", result.microstructure),
        ("Macro", result.macro),
        ("ML", result.ml),
    ):
        lines.append(f"| {name} | {sig.bias.value} | {sig.score:+.3f} | {sig.confidence:.2f} |")
    if result.kill_switch.active:
        lines += ["", "## ⚠ Kill switch ACTIVE", ""]
        lines += [f"- {r}" for r in result.kill_switch.reasons]
    lines += ["", "## Reasoning", ""]
    lines += [f"- {r}" for r in d.reasoning]
    lines += [
        "",
        "## Execution assumptions",
        "",
        "- Simulated fills only — adverse slippage, two-sided fees, "
        "liquidity-capped partial fills, modeled latency.",
        "- This platform CANNOT place real trades.",
        "- Backtests are NOT reality.",
    ]
    return "\n".join(lines) + "\n"


def backtest_report_dict(result: BacktestResult) -> Dict[str, Any]:
    """Serialize backtest metrics to a JSON-ready dict."""
    m: BacktestMetrics = result.metrics
    data = json.loads(m.to_json())
    data["safety_note"] = _SAFETY_NOTE
    data["decisions"] = result.decisions
    data["holds"] = result.holds
    return data


def backtest_report_markdown(result: BacktestResult) -> str:
    """Render backtest metrics as a Markdown report."""
    m: BacktestMetrics = result.metrics
    lines = [
        f"# Backtest report — {m.symbol}",
        "",
        f"> {_SAFETY_NOTE}",
        "",
        f"- **Period:** {m.start.isoformat()} → {m.end.isoformat()}",
        f"- **Bars:** {m.bars}",
        f"- **Trades:** {m.total_trades} ({m.winning_trades}W / {m.losing_trades}L)",
        f"- **Win rate:** {m.win_rate:.1%}",
        f"- **Total return:** {m.total_return_pct:+.2f}%",
        f"- **Equity:** {m.starting_equity:,.2f} → {m.ending_equity:,.2f}",
        f"- **Profit factor:** {m.profit_factor:.2f}",
        f"- **Max drawdown:** {m.max_drawdown_pct:.2f}%",
        f"- **Sharpe-like:** {m.sharpe_like:.2f}",
        f"- **Exposure:** {m.exposure_pct:.1%}",
        f"- **Fees / slippage:** {m.total_fees:,.2f} / {m.total_slippage:,.2f}",
        "",
        "## ⚠ Realism warnings",
        "",
    ]
    lines += [f"- {w}" for w in m.warnings]
    return "\n".join(lines) + "\n"


def save_json(data: Dict[str, Any], path: Path | str) -> Path:
    """Write ``data`` as pretty JSON to ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    return path


def save_text(text: str, path: Path | str) -> Path:
    """Write ``text`` to ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(text)
    return path
