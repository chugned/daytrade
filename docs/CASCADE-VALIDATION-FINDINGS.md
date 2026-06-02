# Cross-asset CASCADE_EXHAUSTION validation — findings

**Date:** 2026-06-02
**Script:** `scripts/sweep_cascade_validation.py`
**Module:** `daytrade.research.cascade_validation`

## The hypothesis

The overnight research (`docs/RESEARCH-90D-FINDINGS.md`) found that
`CASCADE_EXHAUSTION` bars on SOLUSDT, at the 30-minute forward-return
horizon, produced **+9.7 bp** mean return on n=101 events vs −0.1 bp
baseline. The question this validation answers: does the edge
generalise across symbols, or is it SOL-specific?

## Method

- 30 days of real 1-minute candles per symbol, from the existing
  `research.history` cache (no fresh downloads, no network for the
  validation itself).
- Walk each bar; classify with the existing
  `observatory.liquidation_cascade.detect_cascade` detector; if state
  is `CASCADE_EXHAUSTION`, record the forward return over the
  configured horizon.
- Report mean / median / win-rate / event count per symbol, plus the
  all-bars baseline forward return as a comparison line.

## Results — 30-minute horizon

| Symbol | Events | Mean ret | Median | Win rate | Baseline | Edge vs base |
| --- | --- | --- | --- | --- | --- | --- |
| **SOLUSDT**  | 39 | **+18.87 bp** | **+18.11 bp** | **69.2%** | −0.12 bp | **+18.99 bp** |
| **BNBUSDT**  | 45 | +9.15 bp | +8.80 bp | 71.1% | +0.80 bp | +8.35 bp |
| **AVAXUSDT** | 25 | +10.46 bp | +11.01 bp | 64.0% | −0.14 bp | +10.59 bp |
| **BTCUSDT**  | 63 | +10.04 bp | +7.49 bp | 68.3% | −0.69 bp | +10.73 bp |
| ETHUSDT      | 65 | +1.24 bp | +3.66 bp | 63.1% | −1.00 bp | +2.24 bp |
| LINKUSDT     | 16 | −8.22 bp | +2.07 bp | 50.0% | −0.06 bp | −8.15 bp |

## Read

**The edge reproduces.** 4 of 6 symbols (SOL, BNB, AVAX, BTC) show a
materially positive mean forward return after CASCADE_EXHAUSTION with
win rates 64-71%, on samples of 25-63 events over 30 days. SOL — the
symbol that flagged in the overnight research — has the highest mean
(+18.87 bp) **and** the highest win rate (69.2%).

ETH is weak (+1.24 bp). LINK is negative but the sample is tiny (16
events) and the median is +2.07 bp, suggesting one or two outlier
losses dragged the mean.

## Significance check

Sample sizes are small. A back-of-envelope t-test on SOL:

```
mean = 18.87 bp, n = 39
if per-event σ ≈ 30 bp (eyeballed)
SE = σ/√n ≈ 4.8 bp
t ≈ 18.87 / 4.8 ≈ 3.9
```

Significant at p < 0.001 — but the σ is a guess, not measured, and
"30 days of 1m bars" is a single regime, so this is *suggestive*,
not *proven*.

## What this does NOT prove

- That the edge survives **regime shifts**. The overnight research
  found that most "WEAK SIGNAL" 365d results collapsed at 730d. This
  test is 30 days — one regime.
- That the edge survives **realistic round-trip costs**. See next
  section.
- That the detection is **causal** in the trading sense. The
  exhaustion bar is detected at its close — by then a fraction of
  the rebound has already happened. Live, you'd enter on the next
  bar's open, losing ~2-5 bp of edge.

## Net of costs

Re-computing with 24 bps round-trip (10 bps fee × 2 + 2 bps slippage × 2):

| Symbol | Mean ret | After 24 bp cost |
| --- | --- | --- |
| SOLUSDT | +18.87 | **−5.1 bp** |
| BTCUSDT | +10.04 | −14.0 bp |
| AVAXUSDT | +10.46 | −13.5 bp |
| BNBUSDT | +9.15 | −14.9 bp |

**No symbol clears retail-tier costs at the 30m horizon.** The edge
exists in *direction* — the detector is predicting mean-reversion
correctly — but the raw magnitude is too small to overcome fees on
its own.

## What this means operationally

1. The CASCADE_EXHAUSTION detector is **measuring something real**.
   Direction calls are right ~65-70% of the time across symbols.
2. The edge is **not tradeable as a standalone signal** at retail
   costs. Magnitudes are ~10-20 bp, costs are ~24 bp.
3. **Combine with other gates** — the meta-model gate
   (`gating.meta_label_edge_multiple = 2.0`) already requires the
   ML to score it above 2× its base rate; layering the CASCADE
   feature might lift precision enough to clear costs. This is the
   next experiment.

## Reproduce

```bash
PYTHONPATH=src python3 scripts/sweep_cascade_validation.py \
  --symbols "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT" \
  --days 30 \
  --horizons "15,30,60"
```

Tests covering the validation function: `tests/test_cascade_validation.py`
(8 unit tests, all green).
