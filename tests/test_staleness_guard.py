"""Staleness guard tests — never act on stale market data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from daytrade.config import WatchlistConfig, load_config
from daytrade.observatory import LiveMockFeed, ObservatoryDB, Observer


class _StaleFeed:
    """Wraps LiveMockFeed but pins candles to a fixed past timestamp.

    Simulates the real-world case where the upstream data source has
    stopped publishing — the candles the bot retrieves are from
    ``frozen_at`` regardless of how late `now` becomes.
    """

    def __init__(self, frozen_at: datetime) -> None:
        self._inner = LiveMockFeed()
        self._frozen = frozen_at

    def candles_at(self, symbol, now, n_bars=240):
        # Always ask the inner feed for candles as of `frozen_at`, then
        # return them unchanged — their timestamps therefore lag `now`.
        return self._inner.candles_at(symbol, self._frozen, n_bars)

    def orderbook_at(self, symbol, now):
        return self._inner.orderbook_at(symbol, now)

    def tick_at(self, symbol, now):
        return self._inner.tick_at(symbol, now)

    def price_at(self, symbol, when):
        return self._inner.price_at(symbol, when)


def _make_observer(tmp_path, max_age_seconds: int = 300, feed=None):
    cfg = load_config(load_dotenv_file=False)
    new_runtime = cfg.runtime.model_copy(update={"max_data_age_seconds": max_age_seconds})
    cfg = cfg.model_copy(update={"runtime": new_runtime})
    obs = Observer(
        cfg,
        WatchlistConfig(symbols=["BTCUSDT"]),
        db=ObservatoryDB(tmp_path / "obs.db"),
        feed=feed or LiveMockFeed(),
    )
    obs.start()
    return obs


def test_fresh_data_is_observed(tmp_path):
    """Fresh candles -> symbol is assessed normally."""
    obs = _make_observer(tmp_path, max_age_seconds=300)
    now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    summary = obs.run_once(now)
    assert summary.symbols_observed == 1
    assert summary.tradeable >= 1
    obs.stop()
    obs.db.close()


def test_stale_data_is_skipped(tmp_path):
    """Candles older than the floor -> no assessment, activity-feed warning."""
    frozen = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    now = frozen + timedelta(seconds=400)  # 400 s in the future
    obs = _make_observer(tmp_path, max_age_seconds=120, feed=_StaleFeed(frozen))
    summary = obs.run_once(now)
    assert summary.tradeable == 0  # nothing was assessed
    # An activity-feed event explicitly tagged "stale data".
    events = obs.db.recent_activity(limit=10)
    assert any("stale data" in (e.get("detail") or "") for e in events)
    obs.stop()
    obs.db.close()
