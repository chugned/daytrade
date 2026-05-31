# Liquidation-Cascade branch

## What this branch ships
- `src/daytrade/observatory/liquidation_cascade.py` — public-data proxy for
  perp liquidation cascades. Classifies the latest 1m bar as:
  - `QUIET` — nothing unusual.
  - `CASCADE_ACTIVE` — large bearish body + volume spike, still falling.
  - `CASCADE_EXHAUSTION` — long lower wick + volume spike, *after* a
    cascade-active bar.
- `GatingConfig.use_liquidation_cascade_gate` (default **False**) +
  `cascade_body_atr_threshold` (default 2.0) +
  `cascade_volume_spike_ratio` (default 3.0).
- `tests/test_liquidation_cascade.py` — 12 tests covering quiet baselines,
  active detection, exhaustion sequencing, parameter knobs, gate helpers.
- `scripts/sweep_cascade_gate.py` — empirical edge check on 1m data.

## Why this is a *proxy*
The authoritative liquidation feed
(`fapi.binance.com/fapi/v1/allForceOrders`) requires authentication that
the paper-only bot does not hold. The detector therefore infers cascades
from public OHLCV+volume — a deliberate decision: it costs zero extra API
calls and is reproducible from data the bot already has.

The 1m footprint we look for ('big down body in ATR units + volume spike'
followed by 'long lower wick + volume spike') is the public-data
fingerprint of a real cascade, but it is not the cascade itself.

## Why the gate ships disabled

Empirical sweep on real 1-minute data refused to confirm the naive
"don't buy into a cascade" thesis:

```
BTCUSDT 1m × 1000 bars  (horizon 15m, bars eval=955)
  baseline (all)         n= 955 (100.0%)  mean=-0.010%  win%=47.0
  CASCADE_ACTIVE         n=  15 ( 1.6%)  mean=+0.034%  win%=60.0
  active edge vs baseline (negative would justify blocking longs): +0.044%

SOLUSDT 1m × 1000 bars  (horizon 15m, bars eval=955)
  baseline (all)         n= 955 (100.0%)  mean=-0.025%  win%=46.4
  CASCADE_ACTIVE         n=   7 ( 0.7%)  mean=+0.030%  win%=57.1
  active edge vs baseline: +0.054%
```

On BTC and SOL the cascade-active bars are followed by *positive* forward
returns — the gate would block trades that on average win. Sample sizes
(7-15 cascade bars per 1000 1m candles) are too thin to overrule, but
they also do not support flipping the gate on.

So the gate ships disabled. The reading stays valuable as:

1. A regime tag for dashboards/metrics.
2. A candidate continuous feature for the meta-model (same direction as
   the Richer-Meta-Features branch: let the model decide rather than
   gating on hand-picked thresholds).
3. Infrastructure for later sweeps with larger samples, longer horizons,
   or combined with the regime/funding gates.

## Hard guarantees preserved
- No live trading. No wallets. No live orders. No API keys.
- Uses only the same Binance public read-only kline endpoint the bot
  already calls.
- The gate, even if enabled in config, can only *block* a long — it
  cannot create one.

## Verification
```
$ python3 -m pytest -q
314 passed, 1 warning
```
