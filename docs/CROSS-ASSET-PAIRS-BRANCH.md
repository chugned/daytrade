# Cross-Asset-Pairs branch

## What this branch ships
- `src/daytrade/observatory/pairs.py` — pure-numpy / optional-statsmodels
  pairs / stat-arb module:
  - `fit_pair(y, x)` — log-price OLS hedge ratio + ADF stationarity check
    (with a MacKinnon-table fallback when statsmodels is unavailable)
  - `latest_z(y, x, fit)` — current spread z-score
  - `signal_from_z(z, entry_z, exit_z)` →
    `LONG_SPREAD / SHORT_SPREAD / HOLD / EXIT`
  - `analyse_pair(...)` — end-to-end wrapper
  - `backtest_pair(...)` — rolling out-of-sample backtest with **entry-time
    β/μ/σ frozen for the round trip** (the obvious bug in the first draft
    inflated PnL by ~1000× because a mid-trade refit shifted the spread's
    reference frame; fixed in the same commit)
- `tests/test_pairs.py` — 20 tests (synthetic cointegrated + independent
  random-walk fixtures, plus a defence-in-depth grep that asserts no
  order-execution code exists in this file)
- `scripts/sweep_pairs.py` — real-data sweep on ETH/BTC and SOL/BTC.

## Empirical finding (honest)

```
ETHUSDT vs BTCUSDT (1000 × 1m bars, ADF p=0.01 — cointegrated)
  lookback  entry_z  exit_z  trades   win%  totalPnL
       240      1.5     0.3       2   0.0%   -0.001
       240      2.0     0.5       1   0.0%   -0.001
       240      2.5     0.5       0     —      0.0
       360-480     ...           0     —      0.0
```

Two conclusions:

1. **The pair really is cointegrated** at the 1m horizon — ADF p-value
   sits at 0.01 and β stays around 1.62 (log-space). The relationship is
   real.
2. **A 1000-minute window is far too short for stat-arb evaluation.**
   At any reasonable entry threshold the strategy fires 0-2 trades.
   Pairs trading is a multi-year-of-history strategy; expecting an edge
   to surface in 16 hours of 1m data was always optimistic. The module
   ships ready for a future, much longer historical backtest.

## What this branch does NOT do
- Does not register a new gate in `GatingConfig`. The pair signal is
  read-only research code, not yet wired into the trading decision.
- Does not place orders. Does not connect wallets. Paper-only — verified
  by `test_module_has_no_order_execution_symbols`, which greps the file
  for forbidden symbols.

## Future work
- Pull a multi-month dataset (multiple paginated Binance kline calls)
  and rerun the rolling-OOS sweep.
- Add the pair-trade signal as a *feature* of the meta-model (same
  philosophy as Richer-Meta-Features): let the model learn whether a
  divergent z-score predicts the directional symbol move.
- Multi-pair monitoring: rank N×N pairs by ADF p-value, trade the most
  stable handful.

## Verification
```
$ python3 -m pytest -q
334 passed, 1 warning
```
