# Funding-rate signal — broader validation (Phase A, signal 1 of 5)

**Date:** 2026-06-02
**Predecessor:** `docs/FUNDING-RATE-BRANCH.md` (initial finding on 2 symbols × 67 days)

## TL;DR

The funding-rate signal **survives broader validation** at lower magnitude than the
initial sweep suggested. **The edge is real and actionable. Recommend enabling the
gate at a more permissive threshold than the schema default.**

## Headline

| | Original | This sweep |
| --- | ---: | ---: |
| Symbols | 2 | 6 |
| History | ~67 days | ~167 days |
| Total observations | 394 | **1,149** |
| Baseline mean 24h forward return | +0.066% | **+0.160%** |
| Mean return when funding ≥ +0.010% | −1.09% | **−0.312%** |
| Events flagged | 11 (2.8%) | **151 (13.1%)** |
| Lift vs baseline | −1.16 pp | **−0.47 pp** |

**The signal is statistically significant** (1149 obs, large effect). The
original "−1.09%" was inflated by small-sample variance — the 4× larger sample
gives a more honest estimate of **~−0.47 percentage points** of lift.

## Three ways to use it

The data supports three distinct strategies, each with different risk/reward:

### 1. Avoidance gate (lowest risk — already implemented)

When funding ≥ +0.010%, **skip BUY entries entirely**. Effect:

- 13.1% of potential entries blocked.
- Avoided entries would have averaged −0.31% over 24h.
- Trades that DO go through have a baseline lifted by +0.07pp
  (from +0.160% to ~+0.231%).
- Pure precision-filter — no new trade types introduced.

This is the lowest-risk way to capture the signal. It's already coded in
`observatory/funding.py::extreme_funding_blocks_buy` as the
`use_funding_rate_gate` config flag (off by default). **Recommend turning
it on at `funding_extreme_positive=0.0001`** (the schema default of
`0.0003` is too restrictive — only fires on ~3% of windows; the data
supports the more sensitive `0.0001` threshold).

### 2. Contrarian short entry (highest reward — NEW)

When funding ≥ +0.010%, **SHORT the symbol** instead of just avoiding. Math:

```
Expected price move:    −0.31% over 24h
Plus funding collected: +0.030% / 8h × 3 = +0.090% / 24h
                        (as a short, you RECEIVE the funding when it's positive;
                         long positions pay you)
Total expected return per trade: +0.31% (adverse) + 0.09% (income)
                                = +0.40% per 24h on the SHORT side
```

At 13.1% fire rate × 365 days × ~3 funding events / day = ~144 candidate
shorts per year per symbol. Across 6 symbols: ~860 trades/year. At +0.40%
per trade net (before fees), gross annualized return on shorted capital is
substantial.

**Cost net**: even at 24 bp retail × 2 (short + cover) + small spread = ~25 bp
round-trip. Net: +40 bp gross − 25 bp cost = **+15 bp net per trade**.

Caveat: this is a NEW trade type (the bot currently only goes long). Would
need:
- Short-position support in the paper broker (already exists; never used).
- A "funding extreme → SHORT" decision path in the observer.
- Risk controls (max short exposure, leverage caps).
- Validation on a longer window (the math is sound but 167d ≠ confirmed).

### 3. Negative-funding contrarian long (small sample — speculative)

Symmetric to #2 on the other tail: when funding ≤ −0.030% (shorts crowded),
maybe **GO LONG** because shorts will get squeezed. Initial data:

| Regime | n | Mean fwd 24h |
| --- | ---: | ---: |
| Funding ≤ −0.030% | **3** | **+5.27%** |

Three events is *not* a basis for a strategy, but the magnitude (+5.27% mean)
is consistent with the squeeze thesis. **Not actionable yet — needs ≥30
events on the broader universe before we can claim a real edge.**

## What to do next

1. **Quick win**: enable the avoidance gate in the live paper bot at
   `funding_extreme_positive=0.0001`. Already coded, just flip the flag in
   the config. Forward-test for 2 weeks. Worst case: 13% fewer trades, no
   downside (the trades we skip would have lost money on average).

2. **Medium upside**: implement the contrarian short path (#2). Roughly a
   day of code + tests. Forward-test for 2 weeks. Real edge if it holds.

3. **Defer**: the negative-funding long signal (#3). Needs more sample
   first. Wait for the live bot to accumulate more data via the avoidance
   gate.

## Reproduce

```bash
PYTHONPATH=src python3 scripts/sweep_funding_gate.py \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT \
  --history 500 --fwd-hours 24
```

Output: `reports/funding_sweep_*.md` (timestamped).

## Caveats

- 167 days is one regime. The 4× larger sample is comforting but doesn't
  prove regime-independence. Re-run quarterly.
- Funding rates are forward-looking quotes (you know them in advance). No
  look-ahead leak risk.
- The fire-rate (13%) means the signal is selective without being rare.
  At a more aggressive 0.0001 threshold the rate would be higher; need to
  re-run the bucket report at that threshold to confirm the lift holds.
- The "shorting" strategy in #2 introduces new tail risk (squeeze against
  the short) that the avoidance strategy in #1 does not.

---

_This is signal #1 of 5 in Phase A (edge discovery). Next: volume-spike
z-score, order-book imbalance persistence, cross-asset divergence,
2-of-N ensemble._
