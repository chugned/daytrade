# Session research output — index

This index covers the research / hardening branches built in this
session. All work is **paper / simulation only** — no live trading,
no wallets, no live orders, no API keys held. The hard-safety rails
in `src/daytrade/config/schema.py::SafetyConfig` remain intact and
will raise at config-load time if any flag is flipped.

## Branches in this session

| # | Branch | Purpose | Verdict | Default state |
| - | ------ | ------- | ------- | ------------- |
| 1 | `Historical-Data-Pagination` | Multi-page kline fetcher tests + cache inspector | Shippable | n/a (infra) |
| 2 | `Richer-Meta-Features` | 9 new causal features for the meta-model | Shippable | active |
| 3 | `Fear-Greed-Index` | Sentiment regime tag + optional contrarian gate | Falsified on 1500d × 1d | gate **off** |
| 4 | `Liquidation-Cascade` | OHLCV-proxy cascade detector + optional gate | Falsified on 1k bars; mixed on 130k | gate **off** |
| 5 | `Cross-Asset-Pairs` | Cointegration + rolling-OOS stat-arb backtest | Not cointegrated at 90d×1m; no gate | none |
| 6 | `research-integration` | Merges 1-5 + this session-index | 364 tests green | n/a |
| 7 | `Cascade-As-Feature` | Acts on 90d finding — cascade footprint exposed as features | Shippable | active |

## What the data said (90-day × 1m sweep)

See `docs/RESEARCH-90D-FINDINGS.md` for the full table. Headline:

- F&G contrarian thesis: **falsified**. Extreme-fear days have *lower*
  forward returns than baseline.
- Cascade-active "knife" thesis: **falsified**. Forward returns after
  CASCADE_ACTIVE are neutral to slightly positive.
- Cascade-exhaustion mean-revert thesis: **symbol-specific edge**.
  +9.7 bp on SOL at 30m horizon (101 events); opposite sign on ETH.
  Acted on by the Cascade-As-Feature branch — let the model learn
  the per-symbol weight.
- ETH/BTC 1m stat-arb: **falsified**. Not cointegrated over 90d
  (ADF p=0.50); 0% win rate across the grid.
- SOL/BTC 1m stat-arb: **falsified**. 0% win rate.

## Per-branch documentation

| Branch | Doc |
| ------ | --- |
| Richer-Meta-Features | `docs/RICHER-META-FEATURES-BRANCH.md` |
| Fear-Greed-Index | `docs/FEAR-GREED-INDEX-BRANCH.md` |
| Liquidation-Cascade | `docs/LIQUIDATION-CASCADE-BRANCH.md` |
| Cross-Asset-Pairs | `docs/CROSS-ASSET-PAIRS-BRANCH.md` |
| Cascade-As-Feature | `docs/CASCADE-AS-FEATURE-BRANCH.md` |
| (Aggregated findings) | `docs/RESEARCH-90D-FINDINGS.md` |

## Suggested merge order (if you choose to merge)

1. `Historical-Data-Pagination` — pure infrastructure, zero risk
2. `Richer-Meta-Features` — adds features the model trains on; backed
   by leakage tests
3. `Fear-Greed-Index` — adds a fetcher + opt-in gate (off by default)
4. `Liquidation-Cascade` — adds a detector + opt-in gate (off by default)
5. `Cross-Asset-Pairs` — pure research module; not wired
6. `Cascade-As-Feature` — extends the feature matrix with the cascade
   footprint; acts on the 90d finding
7. (Either) merge `research-integration` directly — equivalent to all
   six above plus this index

## What to watch when running with the merged tree

- **Feature count**: jumps from the previous baseline by 14 (9 from
  Richer-Meta-Features + 5 from Cascade-As-Feature). The walk-forward
  validator was hardened against single-class folds in the same session
  so this is robust.
- **Disk**: `data/market_history.db` already holds ~120 days of 1m bars
  for the major pairs (`scripts/cache_inspect.py` shows it).
- **Network**: nothing new in the live loop. The Fear-Greed fetcher
  exists but is only called if its gate is enabled (it isn't, by
  default).

## What NOT to do
- Do not flip any gate on without first running the appropriate sweep
  on the deployed symbol set. The defaults are off for empirical reasons,
  not stylistic ones.
- Do not enable `live_trading_enabled` / `allow_real_orders`. The
  validator will refuse and the bot will exit. This is intentional.
