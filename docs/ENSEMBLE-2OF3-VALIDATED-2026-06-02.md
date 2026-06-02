# 2-of-3 ensemble — empirical validation (Phase A, signal 5 of 5)

**Date:** 2026-06-02
**Sweep:** `reports/ensemble_2of3_sweep_20260602T181345Z.md`
**Script:** `scripts/sweep_ensemble_2of3.py`

## TL;DR

**The 2-of-3 ensemble does NOT compound precision.** In fact, it goes
the wrong way: as more BUY-favorable signals agree on a bar, the
forward 24h return *decreases* relative to baseline.

| k-of-3 favorable | n | mean fwd 24h | lift vs base |
|----:|-:|-:|-:|
| 0 | 78 | +0.428% | +0.321 pp |
| 1 | 5,306 | +0.191% | +0.085 pp |
| 2 | 2,202 | −0.098% | **−0.205 pp** |
| 3 | 49 | −0.386% | **−0.492 pp** |

This is the opposite of the desired ensemble behavior. Recommendation:
**do not ship the 2-of-3 ensemble.** Deploy each signal independently
according to its own validated rules (see signals 1, 2, 4 docs).

## What went wrong (the diagnosis matters)

The honest answer is **three concurrent problems** that the sweep
exposed:

### 1. The funding-rate condition (C1) is effectively a no-op as designed

C1 fires on 98.7% of bars (n=7,538 of 7,635). At threshold `funding ≤
+0.0001` the condition is almost always true — it can't add information
to the ensemble. The signal #1 validation showed the ACTIVE side of
the funding gate (block BUY when funding is extreme positive) carries
the lift; phrasing it as a BUY-favorable side at the same threshold
loses the discrimination.

**Lesson:** the right C1 for an ensemble is `funding ≤ +0.00005` (much
stricter), or better, the *complement* of the avoidance gate — i.e.,
exclude bars where C1=0 rather than count C1=1 as evidence. The
filter and the booster are not symmetric.

### 2. Sample restriction destroyed signal #2 and #4's magnitudes

Funding history is limited to the most-recent 1000 records per symbol
(~333 days). When we restrict to bars where all 3 conditions are
computable (funding non-null + vol_z warmup + 12h BTC return), the
usable sample drops from 60K bar-alt-hours to 7,635. In that
restricted sample:

- C2 alone (green volume z≥+4) shows **−0.738 pp** lift on fwd 24h.
  Signal #2's full-sample 24h finding for the same condition was
  **+0.087 pp**. n collapsed from 1,018 → 123. The signal is just
  noisy at that sample.
- C4 alone (BTC-up matched OR BTC-dn over-fell) shows **−0.179 pp**
  lift. Signal #4's full-sample finding for the same cells was
  **+0.082 to +0.184 pp**. n is decent (2,196) but the magnitudes
  reversed.

**Lesson:** the recent 333-day regime is genuinely different from the
500-day regime tested in signals #2/#4. Restricting to a recent
window degraded all signals individually before the ensemble was
even tested. This isn't a flaw of the ensemble *concept*; it's a
flaw of how I constructed the test.

### 3. The conditions are not as independent as they need to be

When C1 is always-on, the ensemble effectively reduces to "C2 AND C4."
Both are intraday signals that fire in fast, choppy regimes — they
co-fire on bars where momentum has already been violent. Such bars
are more likely to revert than to continue, so requiring BOTH to
agree biases the sample toward over-extended setups that revert.

## What this means for the bot

**Do not deploy a "2-of-3 conviction stack" rule.** Instead:

- Signal #1 (funding gate): deploy as a unilateral BUY-side veto when
  funding ≥ +0.0001 (already validated, recommendation stands).
- Signal #2 (volume z≥+4 green): deploy as a 1h-horizon BUY-side
  conviction booster on the alts where it cleared net cost (ETH, SOL,
  LINK, AVAX — not BTC, not BNB at z=4; BTC and BNB only at z≥5).
- Signal #4 (cross-asset divergence): deploy as a BUY-side filter
  (block when alt is over-extended vs BTC).

These are **complementary specialists**, not a voting ensemble. Each
fires on a different regime, addresses a different failure mode, and
saves loss without depending on the others.

## What I would test next, if extending Phase A

The ensemble concept isn't dead — the *implementation* was wrong.
A re-test that:

- Uses a stricter funding threshold (`≤ +0.00005`),
- Restricts to bars where each signal's INDIVIDUAL effect is positive
  in the recent sample (so the ensemble is built from validated parts,
  not assumed-equivalent parts),
- Tests pairs (C2 ∧ C4) without the broken C1,

...could yield a positive result. But the marginal value over deploying
the three signals individually is uncertain; ensembles only pay off
when their component signals are **uncorrelated and individually
positive**. The first condition is plausible here; the second is no
longer obvious in the recent 333-day regime.

## Caveats

- The funding-rate API's 1000-record limit is structural; longer
  funding history is only available via the futures klines indirect
  path or a paid data source. Working around this would require
  extending the funding fetcher.
- The "ensemble didn't work" finding is dependent on the specific
  conditions I picked. Other definitions of "BUY-favorable" might
  succeed. The negative result here is honest about *these conditions*,
  not about ensembles in general.
- I tested 2-of-3 stacking on the BUY side only. The SELL side
  (i.e., stacked AVOID-BUY conditions) wasn't tested and could
  produce a useful filter.

## Reproduce

```bash
PYTHONPATH=src python3 scripts/sweep_ensemble_2of3.py \
  --alts ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT --days 500
```

Output: `reports/ensemble_2of3_sweep_*.md` (timestamped).

---

_This concludes Phase A (edge discovery): 5 signals researched, 3
worth shipping (funding gate, volume spike, cross-asset divergence),
1 useful as a per-symbol filter (taker-buy ratio), 1 negative result
(2-of-3 ensemble as designed). Next phase: ship the validated
signals into the bot's gating layer with proper tests and ADRs._
