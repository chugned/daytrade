# Fear-Greed-Index branch

## What this branch ships
- `src/daytrade/observatory/fear_greed.py` — read-only fetcher for the
  daily Crypto Fear & Greed Index (alternative.me), with a one-process TTL
  cache, fail-safe error handling, and pure helpers:
  - `fetch_fear_greed(cache_ttl_s=..., now=...)`
  - `extreme_greed_blocks_buy(reading, threshold)`
  - `extreme_fear_blocks_sell(reading, threshold)`
  - `regime_label(reading)` → one of
    `EXTREME_FEAR / FEAR / NEUTRAL / GREED / EXTREME_GREED / UNKNOWN`
- New `GatingConfig` fields, **all opt-in and OFF by default**:
  `use_fear_greed_gate`, `fear_greed_extreme_greed` (default 80),
  `fear_greed_extreme_fear` (default 20).
- `tests/test_fear_greed.py` — 20 tests, fully offline (no network).
- `scripts/sweep_fear_greed.py` — empirical sweep against BTC daily.

## Why the gate ships disabled

The classic contrarian thesis ("extreme greed → reverse, extreme fear →
bounce") is a **strongly held folk belief** in crypto Twitter. So the very
first thing we did was check whether it actually holds. It does not, on the
sample available:

```
$ python3 scripts/sweep_fear_greed.py --horizon-days 7 --limit 1500
Fear & Greed → BTC forward 7-day return
Bucket                    n      mean    median    win%
--------------------------------------------------------
EXTREME_FEAR (<=20)     106    -0.19%     0.30%   50.9%
FEAR (21-40)            206    +0.82%    +0.55%   56.8%
NEUTRAL (41-59)         218    +0.98%    +1.02%   57.3%
GREED (60-79)           417    +1.31%    +0.58%   55.6%
EXTREME_GREED (>=80)     45    +0.56%    +0.03%   51.1%
(all)                   992    +0.94%
```

At horizons of 1, 3, and 7 days, extreme-fear days underperform the
baseline, and extreme-greed days are roughly in line with it. The
contrarian gate would, on this sample, **lose money on average**.

So the code ships disabled. The reading remains available as:
1. a **dashboard regime tag** for context, and
2. a future **feature** (continuous, model-learned) rather than a hard
   gate — which is the same principle that drove the Richer-Meta-Features
   branch: let the model decide.

## Hard guarantees preserved
- No live trading, no wallets, no order routing, no API keys read.
- The fetcher hits only the free, public alternative.me JSON endpoint.
- Network failure ⇒ `None` ⇒ gate falls back to "ALLOW". A flaky internet
  cannot lock the bot.

## Verification
```
$ python3 -m pytest -q
322 passed, 1 warning
```
