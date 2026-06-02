"""Historical research lab tests (hermetic — no network)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from daytrade.config import load_config
from daytrade.models import BacktestMetrics, WalkForwardFold, WalkForwardReport
from daytrade.research import history as history_mod
from daytrade.research import lab as lab_mod
from daytrade.research.history import INTERVAL_MS, HistoryStore, download_history
from daytrade.research.lab import _verdict, run_research


def _fake_klines(n: int, interval_ms: int) -> list:
    """n synthetic Binance kline rows ending ~now."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows = []
    for i in range(n):
        t = now_ms - (n - i) * interval_ms
        base = 100.0 + i * 0.1
        rows.append([t, f"{base}", f"{base + 1}", f"{base - 1}", f"{base + 0.3}", "10.0"])
    return rows


# --- history store ---------------------------------------------------------


def test_interval_ms_table():
    assert "1h" in INTERVAL_MS and INTERVAL_MS["1h"] == 3_600_000
    assert "1d" in INTERVAL_MS


def test_history_store_roundtrip(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    store.write("BTCUSDT", "1h", _fake_klines(50, 3_600_000))
    lo, hi, count = store.cached_span("BTCUSDT", "1h")
    assert count == 50 and lo < hi
    candles = store.read("BTCUSDT", "1h", lo, hi)
    assert len(candles) == 50
    assert all(c.symbol == "BTCUSDT" for c in candles)
    store.close()


def test_download_history_rejects_bad_interval():
    with pytest.raises(ValueError):
        download_history("BTCUSDT", interval="7s", days=1)


def test_download_history_caches(tmp_path, monkeypatch):
    """First call fetches; the second is served from the cache."""
    calls = []

    def _fake_range(symbol, interval, start_ms, end_ms, timeout=15.0):
        calls.append((symbol, interval))
        step = INTERVAL_MS[interval]
        return _fake_klines(int((end_ms - start_ms) // step) + 5, step)

    monkeypatch.setattr(history_mod, "_download_range", _fake_range)
    store = HistoryStore(tmp_path / "h.db")

    a = download_history("BTCUSDT", interval="1h", days=2, store=store)
    b = download_history("BTCUSDT", interval="1h", days=2, store=store)
    assert len(a) > 0 and len(b) > 0
    assert len(calls) == 1  # second call hit the cache, no re-fetch
    store.close()


# --- verdict logic ---------------------------------------------------------


def _bt(*, ret=0.0, sharpe=1.0, win=0.5) -> BacktestMetrics:
    return BacktestMetrics(
        symbol="BTCUSDT",
        start=0,
        end=0,
        bars=500,
        starting_equity=1000.0,
        ending_equity=1000.0 * (1 + ret / 100),
        total_return_pct=ret,
        sharpe_like=sharpe,
        win_rate=win,
    )


def _wf(*, acc=0.5, leakage=False, folds=1) -> WalkForwardReport:
    fold_list = [
        WalkForwardFold(
            fold=i,
            train_start=0,
            train_end=0,
            test_start=0,
            test_end=0,
            train_samples=200,
            test_samples=80,
            train_accuracy=0.6,
            test_accuracy=acc,
        )
        for i in range(folds)
    ]
    return WalkForwardReport(
        model_kind="gradient_boosting",
        folds=fold_list,
        mean_test_accuracy=acc,
        leakage_suspected=leakage,
    )


CFG = load_config(load_dotenv_file=False)


def test_verdict_no_folds_is_insufficient():
    v, _ = _verdict(_bt(), _wf(folds=0), CFG)
    assert v == "INSUFFICIENT DATA"


def test_verdict_leakage_is_suspect():
    v, _ = _verdict(_bt(), _wf(acc=0.92, leakage=True), CFG)
    assert v.startswith("SUSPECT")


def test_verdict_high_sharpe_is_overfit():
    v, _ = _verdict(_bt(sharpe=12.0), _wf(acc=0.6), CFG)
    assert v.startswith("OVERFIT")


def test_verdict_coin_flip_is_no_edge():
    v, _ = _verdict(_bt(ret=5.0), _wf(acc=0.46), CFG)
    assert "NO EDGE" in v


def test_verdict_near_50_is_no_meaningful_edge():
    v, _ = _verdict(_bt(ret=5.0), _wf(acc=0.515), CFG)
    assert "NO MEANINGFUL EDGE" in v


def test_verdict_losing_backtest_is_no_edge():
    v, _ = _verdict(_bt(ret=-8.0), _wf(acc=0.56), CFG)
    assert "NO EDGE" in v


def test_verdict_weak_signal_requires_acc_and_profit():
    v, _ = _verdict(_bt(ret=6.0, sharpe=1.5), _wf(acc=0.57), CFG)
    assert v.startswith("WEAK SIGNAL")
    assert "invest" not in v.lower()  # never says 'safe to invest'


# --- run_research end-to-end (mocked download) -----------------------------


def test_run_research_produces_verdict(monkeypatch):
    from daytrade.exchanges import generate_random_walk

    candles = generate_random_walk(
        "BTCUSDT", n_bars=420, start_price=60_000.0, drift=0.0003, volatility=0.006, seed=4
    )
    monkeypatch.setattr(lab_mod, "download_history", lambda symbol, interval, days: candles)
    results = run_research(["BTCUSDT"], interval="1h", days=30, config=CFG)
    assert len(results) == 1
    r = results[0]
    assert r.symbol == "BTCUSDT" and r.bars == 420
    assert r.backtest is not None and r.walkforward is not None
    assert r.verdict and r.error is None


def test_run_research_insufficient_data(monkeypatch):
    from daytrade.exchanges import generate_random_walk

    monkeypatch.setattr(
        lab_mod,
        "download_history",
        lambda symbol, interval, days: generate_random_walk("BTCUSDT", n_bars=30, seed=1),
    )
    results = run_research(["BTCUSDT"], interval="1h", days=1, config=CFG)
    assert results[0].verdict == "INSUFFICIENT DATA"
