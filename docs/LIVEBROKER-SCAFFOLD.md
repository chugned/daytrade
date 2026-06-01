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

1. **BinanceExchange adapter** (~200-300 LOC, separate branch).
   Calls `ccxt.binance` for `create_market_order`, `fetch_balance`,
   `fetch_position`. Wraps every call in `try/except` that maps to
   `OrderRejected` / `ExchangeUnreachable`. Read-only at first
   (`fetch_*`), then writes.

2. **`SafetyConfig` opt-in field**: e.g. `live_explicit_acknowledgement:
   bool = False` + a separate `live_signed_message: str` that the
   validator must verify against a known hash. So flipping live
   requires two changes in two files, not one.

3. **Paper-on-live-data shadow mode**: run the engine for 7 days with
   `LiveConfig.dry_run = True` against the **Binance** adapter
   (read-only calls only). The broker books all decisions; the
   adapter never actually places orders. Compare the resulting
   dry-run PnL against what the real bot would have done. This is
   the final smoke test before real money.

4. **Tiny live deployment**: €100-200 subaccount, `max_stake_per_trade
   = 10` for week 1, scale up only if PnL tracks the simulation in
   `docs/REAL-MONEY-SIMULATION.md`.

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
