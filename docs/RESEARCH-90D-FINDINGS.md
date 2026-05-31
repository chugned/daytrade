# 90-day research sweep: what actually works

The four research branches (Fear-Greed, Liquidation-Cascade, Cross-Asset-
Pairs, Richer-Meta-Features) were each justified by a thesis we'd seen in
the literature or in crypto folk-wisdom. This document records what the
data says about each thesis at a proper sample size: 90 days × 1m bars
across BTC, ETH, SOL — ≈130 000 bars per symbol, drawn from the cached
multi-month history (see Historical-Data-Pagination).

## TL;DR

| Thesis | Sample | Holds? | What to do |
| --- | --- | --- | --- |
| F&G extreme greed → sell, extreme fear → buy | 1500 days × 1d | **No** | Keep gate off; reading is still a useful dashboard tag. |
| Block longs during CASCADE_ACTIVE | 130k × 1m × 3 syms | **No** | Keep gate off. Active bars are slightly *positive*-skew forward. |
| CASCADE_EXHAUSTION → mean-revert long | 130k × 1m × 3 syms | **Yes on SOL, no on ETH, weak on BTC** | Symbol-specific. Don't ship a blanket gate; ship as a *feature*. |
| ETH/BTC stat-arb at 1m | 130k aligned | **No** | Spread is not cointegrated at the 90-day horizon (ADF p=0.50). |
| SOL/BTC stat-arb at 1m | 130k aligned | **No** | Borderline-cointegrated (p=0.05), but 0% win rate across the grid. |
| Richer-Meta features improve the meta-model | n/a | (Architectural change, no thesis to falsify) | Merge; the model is strictly given more information, the leakage test is green. |

## Detail: cascade detector forward-return by state

```
== BTCUSDT × 90d × 1m  (bars=129428) ==
  h= 5m   baseline +0.000%  | active -0.004% (n=1003)  | exhaust -0.026% (n=171)
  h=15m   baseline +0.001%  | active +0.002% (n=1002)  | exhaust -0.007% (n=171)
  h=30m   baseline +0.002%  | active +0.008% (n=1002)  | exhaust +0.013% (n=171)

== ETHUSDT × 90d × 1m  (bars=129426) ==
  h= 5m   baseline +0.000%  | active -0.009% (n= 966)  | exhaust -0.025% (n=180)
  h=15m   baseline +0.000%  | active -0.008% (n= 966)  | exhaust -0.017% (n=180)
  h=30m   baseline +0.001%  | active +0.003% (n= 966)  | exhaust -0.018% (n=180)

== SOLUSDT × 90d × 1m  (bars=129600) ==
  h= 5m   baseline -0.000%  | active -0.003% (n= 705)  | exhaust -0.003% (n=101)
  h=15m   baseline -0.000%  | active +0.012% (n= 705)  | exhaust +0.055% (n=101)
  h=30m   baseline -0.001%  | active +0.021% (n= 705)  | exhaust +0.097% (n=101)
```

The headline number: **SOL exhaustion bars at 30m horizon have +9.7 bp
forward return on 101 events vs −0.1 bp baseline**. That is a real
edge — but it's *symbol-specific* (ETH exhaustion has the opposite sign)
and we have only 101 events, so the confidence interval is wide.

## Detail: ETH/BTC and SOL/BTC 1m stat-arb (paper, no orders)

```
ETH/BTC: β=0.71, ADF p=0.50 — NOT cointegrated at 90d × 1m
  Every (lookback, entry_z, exit_z) cell: 0% win rate, negative PnL.
SOL/BTC: β=0.32, ADF p=0.05 — borderline
  Every cell: 0% win rate, negative PnL.
```

The 1000-bar sweep on the Cross-Asset-Pairs branch had reported
"cointegrated (ADF p=0.01)" for ETH/BTC — that was a small-sample
artifact. At 90 days the relationship is far less stable, and even where
the ADF passes (SOL/BTC marginally) the rolling-OOS strategy loses on
every parameter setting. The honest read is: **don't ship a stat-arb
gate or signal until a multi-year dataset and proper transaction-cost
model are in place.**

## What this changes in the codebase

Nothing yet. All four gates already ship **disabled by default**. The
research-integration branch keeps them that way. The next responsible
edit is to:

1. Expose `CASCADE_EXHAUSTION` as a continuous feature in the
   FeaturePipeline (in the same spirit as the Richer-Meta-Features
   branch — let the model decide, instead of hand-tuned thresholds).
2. Drop the Cross-Asset-Pairs gate from the active research backlog
   until we have a multi-year dataset.
3. Add a SOL-specific sweep that scopes the exhaustion edge: is it the
   30m-horizon, the 101 events, or the symbol's intrinsic volatility?

## Hard guarantees preserved
- Paper-only, simulation-only. No live trading code added.
- No new external services. The sweep runs against the existing local
  cache (`data/market_history.db`) — no fresh API hits.
- All gates ship **off** by default. Enabling any of them requires an
  explicit YAML override.
