# LiveBroker scaffold

This branch adds the `daytrade.live` package — the infrastructure for
trading real money on a real exchange — without enabling any live
trading. Three things ship; one thing intentionally does not.

## What was added

| File | Purpose |
| --- | --- |
| `src/daytrade/live/exchange.py` | `Exchange` Protocol + `MockExchange` + `ExchangeOrder` / `OrderState` / `OrderRejected` / `ExchangeUnreachable` |
| `src/daytrade/live/broker.py` | `LiveBroker` — mirrors PaperBroker, routes orders through the `Exchange` Protocol |
| `src/daytrade/live/config.py` | `LiveConfig` (paranoid defaults: dry_run=True, max_stake=€25, max_daily_loss=€30) |
| `tests/test_live_broker.py` | 17 tests, all against MockExchange — no network, no money |

## What was NOT added (intentionally)

- A real Binance adapter. That goes in a separate follow-up branch
  guarded by `SafetyConfig.live_trading_enabled = true` + explicit
  acknowledgement field.
- A `daytrade live` CLI command. Same reason.
- Any change to `SafetyConfig`. The existing validator continues to
  raise on `live_trading_enabled = true`.

## Defensive properties already enforced

| Property | How |
| --- | --- |
| Idempotent orders | `ops.order_ids.generate_client_order_id` per (sym, side, minute) — re-submission within 60 s dedupes at the exchange |
| Fail-closed on network errors | `ExchangeUnreachable` raises `LiveBrokerError`; no state change |
| Fail-closed on rejection | `OrderRejected` propagates; no state change |
| Stake cap | `max_stake_per_trade` (default €25) — orders above raise |
| Position cap | `max_open_positions` (default 3) — concurrent BUYs blocked |
| Daily loss cap | `max_daily_loss` (default €30) — realised loss above this halts new BUYs until UTC midnight; SELLs (closes) still allowed |
| Reconciliation | After N orders, broker pulls exchange position and alerts on drift > 0.1% |
| No silent live calls | `test_live_module_does_not_call_real_exchange_directly` greps the package for `import ccxt` / `from binance` — fails CI if anyone tries to wire a real adapter inline |

## How to use it (dry-run, today)

```python
from daytrade.live import LiveBroker, LiveConfig, MockExchange
from daytrade.models import Side

ex = MockExchange(starting_balance_usdt=1000)
broker = LiveBroker(LiveConfig(dry_run=True), ex)

fill = broker.submit_market_order(
    "BTCUSDT", Side.BUY, quantity=0.001,
    reference_price=100_000.0,
)
print(broker.position("BTCUSDT"))
```

This runs the **exact same code path** that a real-money deployment
would run — minus the adapter that talks to Binance. The MockExchange
mirrors the paper broker's slippage + fee math, so "dry-run live" PnL
is directly comparable to "paper" PnL.

## What's next on the path to actual live trading

1. ✅ **BinanceExchange adapter** — done in this branch
   (`src/daytrade/live/binance.py`). Read paths fully wired
   (`fetch_balance`, `fetch_position`, `list_open_orders`). Writes
   exist but are gated by ``writes_enabled=False`` default; calling
   ``place_market_order`` in shadow mode raises ``ShadowModeError``.
   Trade-only key check (``WithdrawalPermissionForbidden``) runs at
   construction. **Cannot start with a withdraw-enabled key, ever.**
   23 tests, all using mocked ccxt clients.

2. ✅ **`SafetyConfig` two-key opt-in** — done in this branch. Going
   live requires all three flag flips
   (``live_trading_enabled``, ``allow_real_orders``,
   ``not paper_trading``) **and** an exact-match acknowledgement
   phrase in ``live_acknowledgement``. The validator refuses any
   partial or inconsistent combination. 16 tests cover every
   permutation. Single-line diffs cannot enable live trading.

3. ⏳ **Wire LiveBroker into the Observer engine** — DEFERRED.
   Current Observer writes trade records straight to the SQLite DB
   via ``db.insert_paper_trade`` / ``db.close_paper_trade``. It
   does *not* go through PaperBroker, so swapping in LiveBroker
   isn't a one-line change — it's a careful refactor that touches
   the live engine that is currently profitable.

   Plan for step 3:
   - Introduce a `BrokerProtocol` whose interface is what the
     Observer's ``_open_position`` and ``_close_position`` actually
     need (a tiny surface).
   - Default to a thin ``DBPaperBroker`` adapter that wraps the
     current direct-DB code (so existing behaviour is bit-for-bit
     unchanged).
   - Optional ``broker: BrokerProtocol`` parameter on ``Observer``;
     when ``None`` the default ``DBPaperBroker`` is used.
   - Tests against the current observatory cycle to prove the
     refactor is behaviour-preserving.
   - Then a new ``LiveBrokerAdapter`` that routes through the
     ``LiveBroker``+``BinanceExchange`` chain.

   This is a single follow-up branch. Don't break what's already
   profitable.

4. **Shadow mode end-to-end smoke** — after step 3, run the engine
   for 7 days with the real ``BinanceExchange`` (``writes_enabled=
   False``) and the LiveBroker (``dry_run=True``). The engine
   reads real balances + positions from Binance, decides as
   normal, the broker books trades into MockExchange, and we
   compare with what the real bot would have done. Final smoke
   test.

5. **Tiny live deployment** — €100-200 subaccount,
   ``max_stake_per_trade = 10`` for week 1, scale up only if PnL
   tracks the simulation in ``docs/REAL-MONEY-SIMULATION.md``.

## Verification

```
$ python3 -m pytest -q
473 passed, 1 warning
```

17 new tests cover: bookkeeping, idempotency, stake cap, position
cap, daily loss cap, day rollover, exchange unreachable, exchange
rejection, equity, fee-driven negative PnL on flat round-trip, and
the defence-in-depth grep that prevents accidental real-exchange
imports.
