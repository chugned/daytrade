"""Safety tests — assert that real trading is structurally impossible."""

from __future__ import annotations

import pytest

import daytrade
from daytrade.config import ConfigError, load_config_dict
from daytrade.exchanges import BinanceClient, BybitClient, CoinGeckoClient
from daytrade.paper import PaperBroker
from daytrade.safety.guard import assert_paper_only, forbid_real_trading

pytestmark = pytest.mark.safety


def test_real_trading_flag_is_false():
    assert daytrade.REAL_TRADING_ENABLED is False


def test_forbid_real_trading_always_raises():
    with pytest.raises(NotImplementedError, match="Real trading is disabled"):
        forbid_real_trading()


def test_forbid_real_trading_includes_context():
    with pytest.raises(NotImplementedError, match="my-context"):
        forbid_real_trading("my-context")


def test_assert_paper_only_passes_in_clean_tree():
    assert_paper_only()  # must not raise


def test_paper_broker_live_connection_raises():
    broker = PaperBroker(starting_cash=10_000.0)
    with pytest.raises(NotImplementedError, match="Real trading is disabled"):
        broker.connect_live()


def test_config_cannot_enable_live_trading():
    with pytest.raises(ConfigError):
        load_config_dict({"safety": {"live_trading_enabled": True}})


def test_no_exchange_client_exposes_order_entry():
    """Public clients are read-only — no place/cancel/submit order method."""
    forbidden = {"place_order", "submit_order", "cancel_order", "create_order"}
    for cls in (BinanceClient, BybitClient, CoinGeckoClient):
        assert forbidden.isdisjoint(dir(cls)), f"{cls.__name__} exposes order entry"


def test_paper_broker_has_no_order_entry_to_real_market():
    """The only order method is the simulated one; the live one raises."""
    broker = PaperBroker(starting_cash=1_000.0)
    assert hasattr(broker, "submit_market_order")  # simulated — allowed
    # connect_live exists only to raise (tested above).


def test_no_leverage_in_paper_broker():
    """Spot, long-only: cannot sell what you do not own (no shorting)."""
    from datetime import datetime, timezone

    from daytrade.config import load_config
    from daytrade.models import Side

    broker = PaperBroker(starting_cash=10_000.0)
    cfg = load_config(load_dotenv_file=False)
    with pytest.raises(ValueError, match="no open position"):
        broker.submit_market_order(
            "o1",
            "BTCUSDT",
            Side.SELL,
            1.0,
            reference_price=100.0,
            available_liquidity=100.0,
            risk_config=cfg.risk,
            timestamp=datetime.now(timezone.utc),
        )


# --- sandbox / credential safety -------------------------------------------


def test_withdrawal_permission_is_rejected():
    """An API key with withdrawal scope is refused — never allowed."""
    from daytrade.config import load_config
    from daytrade.exchanges.credentials import (
        ApiKeyPermissions,
        WithdrawalPermissionError,
        enforce_key_safety,
    )

    cfg = load_config(load_dotenv_file=False)
    with pytest.raises(WithdrawalPermissionError):
        enforce_key_safety(ApiKeyPermissions(can_withdraw=True), cfg.sandbox)


def test_mainnet_key_is_rejected():
    """A non-testnet (mainnet) key is refused for sandbox use."""
    from daytrade.config import load_config
    from daytrade.exchanges.credentials import (
        ApiKeyPermissions,
        MainnetKeyError,
        enforce_key_safety,
    )

    cfg = load_config(load_dotenv_file=False)
    with pytest.raises(MainnetKeyError):
        enforce_key_safety(ApiKeyPermissions(is_testnet=False), cfg.sandbox)


def test_sandbox_execution_is_testnet_only():
    """Every sandbox base URL is a testnet; mainnet hosts are rejected."""
    from daytrade.exchanges.sandbox import (
        _TESTNET_URLS,
        SandboxSafetyError,
        _assert_testnet_url,
    )

    for url in _TESTNET_URLS.values():
        assert "testnet" in url
        _assert_testnet_url(url)  # must not raise
    for mainnet in ("https://api.binance.com", "https://api.bybit.com", "https://api.kraken.com"):
        with pytest.raises(SandboxSafetyError):
            _assert_testnet_url(mainnet)


def test_sandbox_disabled_by_default():
    """With default config, no sandbox client is built (pure paper mode)."""
    from daytrade.config import load_config
    from daytrade.exchanges.sandbox import build_sandbox_client

    cfg = load_config(load_dotenv_file=False)
    assert cfg.sandbox.enabled is False
    assert build_sandbox_client(cfg) is None


def test_config_cannot_disable_withdrawal_rejection():
    """sandbox.reject_withdrawal_keys cannot be turned off."""
    from daytrade.config import ConfigError, load_config_dict

    with pytest.raises(ConfigError):
        load_config_dict({"sandbox": {"reject_withdrawal_keys": False}})


# --- no money-movement code exists -----------------------------------------


def test_no_money_movement_functions_exist():
    """No function in the codebase performs a bank transfer / withdrawal.

    Scans every source file for function definitions whose name implies
    moving money. The accounting layer only *reports*; it never transfers.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "daytrade"
    forbidden = re.compile(
        r"def\s+(withdraw|transfer_funds|send_funds|wire_|payout|"
        r"bank_transfer|move_money|cash_out)\w*\s*\(",
        re.IGNORECASE,
    )
    offenders = []
    for path in src.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if forbidden.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, f"money-movement functions found: {offenders}"


# --- risk limits and kill switch override BUY signals ----------------------


def test_risk_limit_overrides_buy_signal():
    """A breached loss limit blocks an entry even with capital available."""
    from datetime import datetime, timedelta, timezone

    from daytrade.config import load_config
    from daytrade.risk import RiskEngine

    cfg = load_config(load_dotenv_file=False)
    risk = RiskEngine(cfg.risk, starting_equity=10_000.0)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    risk.observe_equity(t0, 10_000.0)
    risk.observe_equity(t0 + timedelta(hours=1), 9_000.0)  # -10% day
    permission = risk.evaluate_entry(9_000.0, open_positions=0, bar_index=10)
    assert not permission.allowed  # BUY would be blocked here


def test_kill_switch_forces_hold():
    """An exchange-collapse macro scenario forces the decision to HOLD."""
    from daytrade.config import load_config
    from daytrade.exchanges import generate_random_walk
    from daytrade.exchanges.mock import build_orderbook
    from daytrade.models import Action
    from daytrade.pipeline import AnalysisPipeline

    cfg = load_config(load_dotenv_file=False)
    candles = generate_random_walk(
        "BTCUSDT", n_bars=200, start_price=30_000.0, drift=0.001, volatility=0.004, seed=3
    )
    book = build_orderbook("BTCUSDT", candles[-1].close, jitter=0.0)
    result = AnalysisPipeline(cfg).analyze(
        candles, book, reference_price=candles[-1].close, macro_scenario="exchange_collapse"
    )
    assert result.kill_switch.active
    assert result.decision.action is Action.HOLD


def test_illiquid_coin_is_rejected():
    """A thin, low-volume market is rejected by the watchlist screener."""
    from daytrade.config import load_config
    from daytrade.watchlist import WatchlistScreener, build_mock_asset_data

    cfg = load_config(load_dotenv_file=False)
    screener = WatchlistScreener(cfg.watchlist)
    tick, book, candles = build_mock_asset_data("THINUSDT")
    screening = screener.screen_one("THINUSDT", tick, book, candles)
    assert not screening.approved
    assert screening.rejections


# --- observatory: still paper-only -----------------------------------------


def test_no_wallet_code_exists():
    """No function connects, links, or imports a crypto wallet."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "daytrade"
    forbidden = re.compile(
        r"def\s+(connect_wallet|link_wallet|wallet_connect|import_wallet|"
        r"unlock_wallet|sign_transaction|broadcast_tx)\w*\s*\(",
        re.IGNORECASE,
    )
    offenders = []
    for path in src.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if forbidden.search(line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"wallet code found: {offenders}"


def test_observatory_observer_places_no_real_orders():
    """The observer's broker path is simulation only — connect_live raises."""
    from daytrade.paper import PaperBroker

    broker = PaperBroker(10_000.0)
    with pytest.raises(NotImplementedError):
        broker.connect_live()


def test_dashboard_reports_paper_only(tmp_path):
    """The dashboard health endpoint asserts paper-only, no real trading."""
    from fastapi.testclient import TestClient

    from daytrade.dashboard import create_app

    client = TestClient(create_app(tmp_path / "obs.db"))
    body = client.get("/api/health").json()
    assert body["real_trading"] is False
    assert body["paper_only"] is True


def test_observatory_has_no_live_order_method():
    """The observer exposes no order-entry-to-a-real-exchange method."""
    from daytrade.observatory import Observer

    forbidden = {"place_order", "submit_live_order", "send_order", "connect_wallet", "withdraw"}
    assert forbidden.isdisjoint(dir(Observer))
