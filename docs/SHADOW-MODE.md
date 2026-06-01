# Shadow mode — the final smoke test before real money

`daytrade shadow` runs the engine in a configuration that combines
**real-Binance reads** with **mock-only writes**. It exists to surface
every live-wiring problem before any euro is at risk.

## What "shadow" means here

```
       Public OHLCV (data) ──────────────►  Observer
                                            (unchanged engine)
                                                  │
                                                  ▼
                                          LiveBroker
                                                  │
                                                  ▼
                                       ShadowExchange
                                          ┌─────┴─────┐
                                          │           │
                                 reads ───┘           └─── writes
                                          ▼                    ▼
                              BinanceExchange         MockExchange
                              (writes_enabled=False)  (in-memory)
                                  │                       │
                                  ▼                       ▼
                            REAL Binance              fake fills
                            balance/positions         booked locally
```

Reads (balance, positions, open orders) hit your real Binance account
via the authenticated read endpoints. Writes (`place_market_order`,
`cancel_order`) go to a `MockExchange` and never touch the network.

## How to run it

### Without credentials — pure-wiring smoke test

```bash
PYTHONPATH=src python3 -m daytrade.cli.main shadow --interval 5
```

Uses a `MockExchange` as the reader too. Validates the broker wiring,
the Observer integration, and that the engine cycles cleanly. Doesn't
test against real Binance balance state.

### With credentials — full live-wiring smoke test

```bash
export DAYTRADE_BINANCE_KEY="..."        # trade-only key
export DAYTRADE_BINANCE_SECRET="..."

PYTHONPATH=src python3 -m daytrade.cli.main shadow --interval 300
```

The bot will:

1. **Validate the key.** `inspect_key` hits Binance's
   `/sapi/v1/account/apiRestrictions` endpoint. If the key has
   withdrawal or transfer permission, the command **refuses to start**.
2. **Build a `BinanceExchange` adapter with `writes_enabled=False`.**
   Even if a bug somewhere tried to call `place_market_order` on this
   adapter directly, it would raise `ShadowModeError`.
3. **Sync starting balance** from the real Binance account into the
   `MockExchange` writer, so the bot's accounting matches your real
   account size from cycle 1.
4. **Run the Observer** as it would in production — same gates, same
   risk engine, same per-cycle reconciliation. Decisions to enter or
   exit positions flow through the broker as normal; the broker hands
   them to the `ShadowExchange`; only the `MockExchange` ever sees a
   write.

## Required Binance API-key configuration

The trade-only key validator
(`daytrade.ops.api_keys.assert_trade_only`) requires:

  - `enableSpotAndMarginTrading`: **true**
  - `enableWithdrawals`: **false** (cardinal)
  - `enableInternalTransfer`: **false**
  - `permitsUniversalTransfer`: **false**

Strongly recommended:

  - **IP-allowlist the key to the host you're running on.** A bare
    key gives less defence-in-depth than an IP-bound key.
  - Disable margin / futures unless explicitly required.
  - Generate a **new key on a fresh subaccount** with only the
    capital you're willing to lose.

## What shadow mode does NOT do

- **Does not place orders.** Verified by 9 tests in
  `tests/test_shadow_exchange.py` including one that asserts the
  reader's `place_market_order` is *never* called even after a full
  open/close cycle.
- **Does not enable any of the four live-trading gates.**
  `LiveConfig.dry_run` stays `True`, `BinanceExchange.writes_enabled`
  stays `False`, the `SafetyConfig` rails are unchanged. Flipping any
  of these still requires the four-step opt-in documented in
  `docs/LIVEBROKER-SCAFFOLD.md`.

## When to stop shadow mode

After **at least 7 days** of clean cycling and at least 10 simulated
shadow trades, with:

  - Zero `ExchangeUnreachable` errors that did not auto-recover.
  - Zero reconciliation drift warnings.
  - Per-trade PnL that tracks your live paper bot to within ±15%.

If any of those fail, fix the root cause before considering real
money. Shadow mode is cheap; live trading isn't.
