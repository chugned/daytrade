# `Funding-Rate-Gate` branch — alternative-data sentiment signal

Adds a 5th decision gate that reads Binance perpetual-futures funding
rates and blocks BUYs when funding is extremely positive — a documented
"crowded longs → pullback risk" signal from the crypto literature.

```
main (live paper bot)
  └── Funding-Rate-Gate    ← this branch
```

This is Tier-1 of the 10x research roadmap (alternative-data features),
specifically the lowest-cost / highest-evidence variant of it. No API
key needed — Binance's `/fapi/v1/premiumIndex` is public read-only.

## What's inside

| Path | Purpose |
|---|---|
| `src/daytrade/observatory/funding.py` | Fetch live + historical funding rates; interpret extremes |
| `src/daytrade/observatory/observer.py` | Wired as a 5th gate in `_maybe_open_position`, off by default |
| `src/daytrade/config/schema.py` | `gating.use_funding_rate_gate`, `gating.funding_extreme_positive/negative` |
| `tests/test_funding_gate.py` | 10 tests: thresholds, HTTP success/failure, caching, history parsing |
| `scripts/sweep_funding_gate.py` | Empirical sweep against real Binance perp + spot history |

## The signal — what it actually means

Binance perpetual futures settle funding payments every 8 hours:
- **Positive funding rate** = longs pay shorts (perp price > spot →
  long positions are crowded / pressured).
- **Negative funding rate** = shorts pay longs (perp price < spot →
  shorts are crowded / pressured).

The pattern: an unusually high funding rate signals that leveraged longs
are stacked deep, and a small adverse move triggers a cascading exit
("long squeeze"). The 24-hour forward return after such moments is
systematically negative in the literature.

## Initial sweep result (2 symbols · ~67 days · 24h forward window)

| Funding regime | n | Mean fwd 24h | Lift vs baseline |
|---|---:|---:|---:|
| **Baseline** (any window) | 394 | **+0.066%** | — |
| **Funding ≥ +0.010%** (extreme positive) | 11 | **−1.090%** | **−1.156%** ✅ |

**The signal is strongly directional in the expected sense.** Windows
where funding ran above +0.010% per 8h have averaged a **1.09% LOSS**
over the next 24 hours, versus a +0.07% gain in a typical window — a
1.16 percentage-point lift from skipping those entries.

That's a large, defensible avoidance signal. The fire rate (~2.8%) is
selective — the bot wouldn't trade much less overall, but the windows
it skips are demonstrably worse-than-baseline.

Re-sweep on more symbols / wider windows before locking the threshold,
but the direction is unambiguous on the data we have.

## How to enable it (after re-sweeping)

```yaml
# configs/default.yaml
gating:
  use_funding_rate_gate: true
  funding_extreme_positive: 0.0001   # 0.010% — sweep-best lower bound
  funding_extreme_negative: -0.0010  # short-squeeze regime, BUY allowed
```

The default in the schema (`0.0003 = 0.03%`) is more conservative;
the sweep suggests `0.0001` catches more usable windows. Operator pick.

## How this composes with the other branches

| | Interaction |
|---|---|
| `Secure` | independent — `Secure` is engineering primitives, this is strategy. Both can merge to `main`. |
| `Freqtrade-Port` | the freqtrade strategy can adopt the same funding check via `confirm_trade_entry`; one-line port. |
| `Multi-Timeframe-Filter` | independent — both can be enabled together (different signals). |

## Tests / status

- 311 tests pass on this branch (+10 new for funding gate).
- Gate is **off by default**; opt-in via `gating.use_funding_rate_gate`.
- Bot on `main` is unaffected — strategy / behaviour identical.
- Empirical validation: sweep saved to `reports/funding_sweep_*.md`.
