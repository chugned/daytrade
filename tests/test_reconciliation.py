"""Startup reconciliation tests."""

from __future__ import annotations

from daytrade.observatory import ObservatoryDB
from daytrade.ops import reconcile_paper_state


def test_clean_db_reconciles_ok(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    report = reconcile_paper_state(db)
    assert report.ok is True
    assert report.anomalies == []
    assert report.open_paper_positions == 0
    db.close()


def test_open_trade_with_valid_long_levels_is_ok(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    db.insert_paper_trade(
        symbol="BTCUSDT",
        side="buy",
        quantity=0.05,
        entry_price=100.0,
        stop=98.0,
        target=104.0,
        fees=0.0,
        slippage=0.0,
        pnl=0.0,
    )
    report = reconcile_paper_state(db)
    assert report.ok is True
    assert report.open_paper_positions == 1
    db.close()


def test_inverted_long_levels_flag_anomaly(tmp_path):
    """A long with stop > entry should be flagged immediately."""
    db = ObservatoryDB(tmp_path / "obs.db")
    db.insert_paper_trade(
        symbol="BTCUSDT",
        side="buy",
        quantity=0.05,
        entry_price=100.0,
        stop=110.0,  # broken — stop above entry
        target=120.0,
        fees=0.0,
        slippage=0.0,
        pnl=0.0,
    )
    report = reconcile_paper_state(db)
    assert report.ok is False
    assert any("long levels broken" in a for a in report.anomalies)
    db.close()


def test_zero_quantity_open_trade_flag_anomaly(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    db.insert_paper_trade(
        symbol="BTCUSDT",
        side="buy",
        quantity=0.0,
        entry_price=100.0,
        stop=98.0,
        target=104.0,
        fees=0.0,
        slippage=0.0,
        pnl=0.0,
    )
    report = reconcile_paper_state(db)
    assert report.ok is False
    assert any("quantity" in a for a in report.anomalies)
    db.close()


def test_orphan_outcome_row_flag_anomaly(tmp_path):
    db = ObservatoryDB(tmp_path / "obs.db")
    # Insert an outcome that references a non-existent prediction.
    db.upsert_outcome(
        prediction_id=99999, symbol="X", predicted_ts="2026-01-01", directionally_correct=1
    )
    report = reconcile_paper_state(db)
    assert report.ok is False
    assert any("orphan" in a for a in report.anomalies)
    db.close()
