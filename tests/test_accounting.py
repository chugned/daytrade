"""Accounting, tax-export and manual-approval tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from daytrade.accounting import build_accounting_report, export_tax_csv
from daytrade.approval import TradeProposal, request_approval
from daytrade.config import ApprovalConfig
from daytrade.models import Action
from daytrade.paper.broker import TradeRecord

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _trade(symbol="BTCUSDT", pnl=100.0, fees=5.0) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        quantity=0.5,
        entry_price=30_000.0,
        exit_price=30_400.0,
        opened_at=_T0,
        closed_at=_T0 + timedelta(hours=2),
        pnl=pnl,
        fees=fees,
    )


# --- accounting ------------------------------------------------------------


def test_accounting_report_totals():
    trades = [_trade(pnl=100.0), _trade(pnl=-40.0), _trade(pnl=60.0)]
    report = build_accounting_report(trades, 10_000.0, 10_120.0)
    assert report.simulated_profit == 160.0
    assert report.simulated_loss == -40.0
    assert report.net_pnl == 120.0
    assert report.total_trades == 3


def test_accounting_per_asset_breakdown():
    trades = [_trade("BTCUSDT", 100.0), _trade("ETHUSDT", -20.0)]
    report = build_accounting_report(trades, 10_000.0, 10_080.0)
    assert set(report.per_asset) == {"BTCUSDT", "ETHUSDT"}
    assert report.per_asset["ETHUSDT"].net_pnl == -20.0


def test_accounting_empty_trades():
    report = build_accounting_report([], 10_000.0, 10_000.0)
    assert report.net_pnl == 0.0
    assert report.total_trades == 0


# --- tax CSV ---------------------------------------------------------------


def test_tax_csv_export(tmp_path):
    trades = [_trade(pnl=100.0), _trade(pnl=-40.0)]
    path = export_tax_csv(trades, tmp_path / "tax.csv")
    text = path.read_text()
    assert "SIMULATED PAPER TRADES ONLY" in text  # disclaimer present
    assert "gain_loss" in text  # header present
    # one disclaimer + one header + two data rows
    assert len(text.strip().splitlines()) == 4


def test_tax_csv_disclaimer_not_tax_advice(tmp_path):
    path = export_tax_csv([_trade()], tmp_path / "tax.csv")
    assert "Not tax advice" in path.read_text()


# --- manual approval -------------------------------------------------------


def _proposal(**overrides) -> TradeProposal:
    base = dict(
        symbol="BTCUSDT",
        action=Action.BUY,
        entry=100.0,
        stop=98.0,
        target=106.0,
        confidence=0.6,
        quantity=1.0,
        risk_amount=2.0,
        expected_slippage_cost=0.5,
        expected_fee=0.1,
        reasoning=["test"],
        liquidity_warning=None,
        kill_switch_active=False,
        kill_switch_reasons=[],
    )
    base.update(overrides)
    return TradeProposal(**base)


def test_approval_requires_exact_phrase():
    decision = request_approval(_proposal(), ApprovalConfig(), input_fn=lambda _: "YES")
    assert decision.approved


def test_approval_rejects_wrong_phrase():
    decision = request_approval(_proposal(), ApprovalConfig(), input_fn=lambda _: "yes please")
    assert not decision.approved


def test_approval_rejects_empty_input():
    decision = request_approval(_proposal(), ApprovalConfig(), input_fn=lambda _: "")
    assert not decision.approved


def test_approval_blocked_by_kill_switch():
    """A kill-switch-active proposal is rejected before any prompt."""
    called = []

    def _input(_):
        called.append(True)
        return "YES"

    decision = request_approval(
        _proposal(kill_switch_active=True, kill_switch_reasons=["panic"]),
        ApprovalConfig(),
        input_fn=_input,
    )
    assert not decision.approved
    assert not called  # never even prompted


def test_approval_disabled_auto_approves():
    cfg = ApprovalConfig(require_manual_approval=False)
    decision = request_approval(_proposal(), cfg, input_fn=lambda _: "")
    assert decision.approved


def test_approval_hold_action_rejected():
    decision = request_approval(
        _proposal(action=Action.HOLD), ApprovalConfig(), input_fn=lambda _: "YES"
    )
    assert not decision.approved
