# Overnight iteration log

User instruction: run research-lab iterations in a loop until 06:00 CEST on
2026-06-01, on the **Secure** branch with all 10 research branches merged
(456 tests green).

Each iteration:
1. Identifies the next experiment based on the previous result.
2. Runs it (research lab CLI, sweep script, or other).
3. Records the headline finding here.

Hard guarantee: **paper / simulation only**. No trading code touched
through any iteration. `SafetyConfig` enforced.

Started: 2026-06-01 00:57 CEST
Stop at: 2026-06-01 06:00 CEST

## Iteration 1 — research lab on 5m × 30d × BTC/ETH/SOL

Reason: After merging all research branches into Secure, the natural
first question is whether the meta-model + walk-forward shows any edge
on real data with the full merged feature set (the new HTF slopes,
cascade footprint columns, position-in-range features, etc.). 1m × 30d
ran for 18 minutes with no output and was killed; 5m × 30d (one-twelfth
the data) should be tractable in minutes.

Task ID: `b4jda1zv9` — completed.

**Result: catastrophic overfit on every symbol.**

| Sym | Bars | BT ret | BT win% | Sharpe~ | WF test acc | **Overfit gap** | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 8640 | +4.0% | 68% | 2.49 | **50%** | **+0.50** | NO EDGE |
| ETH | 8640 | +0.8% | 56% | 0.41 | **49%** | **+0.50** | NO EDGE |
| SOL | 8640 | +3.4% | 59% | 1.61 | **51%** | **+0.49** | NO MEANINGFUL EDGE |

A +0.50 train/test gap means train accuracy ≈ 100%, test ≈ 50% — the
textbook signature of a model that has memorised noise rather than
learned signal. The backtest return looks nice on the *training-fit*
model but it is meaningless because the out-of-sample (walk-forward)
accuracy is at coin flip.

Diagnostic note from the lab: **"walk-forward windows shrunk to fit 785
samples"** out of 8640 raw bars. That's only 9% of the bars usable
after feature warmup (60-bar position-in-range, 240-min slope_1h, 14-bar
ATR) and label drops (triple-barrier with 0.4% threshold drops most
no-event bars). Too few samples + 28 features → overfit.

## Iteration 2 — research lab on 1h × 365d × BTC/ETH/SOL

Reason: 1h × 365d gives ~8760 bars per symbol — same order of magnitude
as iteration 1 — but the feature warmup (60 bars = 60h ≈ 2.5d) and label
loss are far smaller fractions of the dataset, so per-fold usable
samples should be 5-10× larger. The cache already has 365d of 1h data
(see `scripts/cache_inspect.py`). Should run in minutes.

Task ID: `b3snhtczz` — completed.

**Result: the first crack of signal — on ETH only.**

| Sym | Bars | BT ret | BT win% | Sharpe | **WF acc** | Overfit gap | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 8751 | +10.2% | 54% | 1.76 | 53% | +0.47 | NO MEANINGFUL EDGE |
| **ETH** | **8751** | **+43.8%** | **60%** | **3.87** | **56%** | **+0.43** | **WEAK SIGNAL** |
| SOL | 8421 | +57.0% | 62% | 4.14 | 50% | +0.49 | OVERFIT |

ETH at 1h is the first symbol to clear the lab's `WEAK SIGNAL` bar — its
WF accuracy of 56% is above the 53% noise floor, and its Sharpe 3.87
sits *just under* the 4.0 realism ceiling. BTC is coin-flip; SOL is
backtest-too-good-to-be-real (which is itself a finding — the cascade
edge from the 90d sweep doesn't translate into a 1h tradeable signal).

Overfit gap remains large (+0.43 on ETH) so the model is still
memorising train data even when it generalises slightly. This is the
hint that suggests the next experiment: **drill into ETH** with
different intervals and see if a higher timeframe gives even cleaner
signal-to-noise.

## Iteration 3 — ETH-only across multiple intervals (1h, 2h, 4h, 1d)

Reason: ETH at 1h showed real signal. The natural drill is to find the
TF where ETH's signal is *cleanest* — higher TF means fewer noise bars,
fewer signal opportunities. The 1d × 365 bars = 365 samples — too few.
4h × 365 = 2190 bars — usable. 2h = 4380. Sweep all three plus the
already-known 1h baseline.

Result: **ETH at 1h is the sweet spot**.

| Interval | Bars | BT ret | BT win% | Sharpe | **WF acc** | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| **1h** | 8751 | **+43.8%** | 60% | **3.87** | **56%** | WEAK SIGNAL ← best |
| 2h | 4380 | +32.3% | 60% | 3.07 | 54% | WEAK SIGNAL |
| 4h | 2190 | +23.0% | 59% | 2.27 | 54% | WEAK SIGNAL |

The trend is monotonic: longer bars degrade the signal. So the 1m
strategy ported to a higher TF works best at 1h on ETH — short enough
to have many opportunities, long enough that feature warmup loss is
small relative to the dataset. No need to test 1d (only ~360 samples;
walk-forward folds would be tiny).

## Iteration 4 — Other large-cap alts at 1h × 365d (BNB / XRP / ADA)

Reason: ETH has signal at 1h. Question: is this *ETH-specific* or do
other large-cap alts also generalise? Testing BNB, XRP, ADA — three
liquid majors whose cache already has 365d of 1h data — answers it
directly. If any clears WEAK SIGNAL we have a small portfolio of
edge-bearing symbols, not a one-off.

Result: **BNB is even cleaner than ETH.**

| Sym | BT ret | BT win% | Sharpe | **WF acc** | Overfit gap | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| **BNB** | **+26.5%** | **62%** | **3.18** | **57%** | **+0.42** | **WEAK SIGNAL** |
| XRP | +40.1% | 59% | 3.65 | 52% | +0.46 | NO MEANINGFUL EDGE |
| ADA | +22.3% | 53% | 1.75 | 52% | +0.47 | NO MEANINGFUL EDGE |

So we now have **two** symbols with WEAK SIGNAL at 1h: ETH and BNB.
BNB has the cleanest profile — highest WF accuracy and lowest overfit
gap of anything tested so far. Cross-symbol scoreboard at 1h × 365d:

| Symbol | WF acc | Verdict |
| --- | --- | --- |
| BNB | **57%** | WEAK SIGNAL |
| ETH | **56%** | WEAK SIGNAL |
| BTC | 53% | NO MEANINGFUL EDGE |
| XRP | 52% | NO MEANINGFUL EDGE |
| ADA | 52% | NO MEANINGFUL EDGE |
| SOL | 50% | OVERFIT (Sharpe ceiling) |

The edge clusters on the two exchange-aligned majors (ETH+BNB). It is
absent on the macro-proxy (BTC), weak alts (XRP/ADA), and chaotic high-
beta (SOL). That's a coherent pattern, not random.

## Iteration 5 — ETH + BNB at 1h × 730d (regime stability)

Reason: A 365-day window covers a single broad market regime. To know
if the ETH+BNB edge is regime-stable or just recent-history luck, double
the history to 730 days. If the WF accuracy stays at 56-57% across the
larger sample, the signal becomes much more credible.

Result: **the edge does not survive 2 years.**

| Sym | Bars | BT ret | Sharpe | **WF acc** | Overfit gap | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| ETH | 17520 | +89.3% | 4.71 | **44%** | +0.56 | OVERFIT (Sharpe ceiling) |
| BNB | 17520 | +65.4% | 4.49 | **48%** | +0.51 | OVERFIT (Sharpe ceiling) |

Both symbols drop *below coin flip* at 730 days, with overfit gap
widening to +0.56 on ETH. The 365-day weak signal was almost certainly
a regime quirk. The lab is doing its job: catching a falsehood before
anyone would risk money on it.

The diagnosis points at one of two problems:
1. **Model too flexible**: GradientBoosting can memorise training
   patterns that don't generalise.
2. **Features carry no signal**: even a simpler model wouldn't find
   anything.

Iteration 6 picks the cheaper diagnostic: switch to logistic regression.
If WF accuracy holds near 56% with logistic, the GB overfit was the
problem. If it drops to noise, the features themselves aren't enough.

## Iteration 6 — ETH + BNB at 1h × 365d with logistic regression

Reason: control experiment on the model. Same data, same features, the
only change is replacing GradientBoosting (n_estimators=150, depth=3,
LR=0.05) with linear logistic regression (max_iter=1000, C=1.0). A
linear model cannot memorise — if there is a *real* edge in the
features, logistic should find at least part of it. If it scores at
chance, the features aren't predictive.

Result: **the GB "edge" was overfit noise.**

| Sym | Model | WF acc | Overfit gap | Verdict |
| --- | --- | --- | --- | --- |
| ETH | GB (it.2) | 56% | +0.43 | WEAK SIGNAL ← mirage |
| **ETH** | **Logistic** | **47%** | **+0.17** | **NO EDGE** ← truth |
| BNB | GB (it.4) | 57% | +0.42 | WEAK SIGNAL ← mirage |
| **BNB** | **Logistic** | **52%** | **+0.16** | **NO MEANINGFUL EDGE** ← truth |

Two things shifted at once:
1. **Overfit gap collapsed** from +0.42 to +0.17 — confirming logistic
   doesn't memorise.
2. **WF accuracy fell to noise floor** — once memorisation is removed,
   the features carry almost no signal at 1h.

The lab's "WEAK SIGNAL" verdict on iteration 2 only triggered because
the GB model could *partially* match patterns from the train set inside
test folds by chance — the walk-forward is causal, but a sufficiently
flexible model can still leak via shared distributional structure. With
logistic that channel is closed, and the bare features test as noise.

This is the **most important finding of the night**: the merged feature
set, evaluated honestly, does not predict 1h direction.

## Iteration 7 — ETH + BNB at 1h × 365d with random forest

Reason: complete the complexity sweep. RandomForest is between GB
(highly flexible, ensemble of deep boosted trees) and logistic (linear).
Configured at depth=6, min_samples_leaf=5 — moderate regularisation.
Where the RF result sits tells us how WF accuracy and overfit gap scale
with model flexibility.

Result: **RF *matches* GB's WF accuracy with less overfit.**

| Sym | Model | WF acc | Overfit gap | Sharpe |
| --- | --- | --- | --- | --- |
| ETH | Logistic | 47% | +0.17 | — |
| ETH | **RF** | **57%** | **+0.37** | **3.85** |
| ETH | GB | 56% | +0.43 | 3.87 |
| BNB | Logistic | 52% | +0.16 | — |
| BNB | **RF** | **55%** | **+0.39** | **3.08** |
| BNB | GB | 57% | +0.42 | 3.18 |

This **reopens the case**. Logistic finding nothing while RF finds the
same level of OOS edge as GB means the features carry *non-linear*
structure — interactions and thresholds that a linear classifier can't
combine but a tree can. RF gets there with a smaller overfit gap (+0.37
vs +0.43), suggesting the signal isn't purely memorisation.

The remaining diagnostic: did GB's 365d edge collapse at 730d because
of model flexibility (GB-specific overfit) or because of regime shift
(any model would have failed)? Iteration 8 runs RF at 730d to find out.

## Iteration 8 — RandomForest at ETH+BNB × 1h × 730d

Reason: the same regime-stability test that broke GB (44% / 48% at
730d), now with the more-regularised RF. If RF survives 730d with
WF ≥ 53%, we have evidence of a model-architecture-driven edge that
the merged feature set is genuinely capturing. If RF collapses too,
the original signal was just a recent-regime artifact regardless of
model choice.

Result: **RF collapses at 730d too.** ETH 49%, BNB 46%, both overfit
(Sharpe-like > 4.0 ceiling, OOS accuracy below or at noise floor).

Cross-window scoreboard:

| Window | Model | ETH WF | BNB WF |
| --- | --- | --- | --- |
| 365d | Logistic | 47% | 52% |
| 365d | RF | **57%** | **55%** |
| 365d | GB | 56% | 57% |
| 730d | RF | 49% | 46% |
| 730d | GB | 44% | 48% |

The 730d→365d delta is **model-independent**: every model has the same
recent-regime artifact, none survives the longer window. The signal at
365d is therefore not "RF found something GB missed" — it's "all
sufficiently-flexible models pick up the same recent-history pattern,
and that pattern does not generalise across regimes."

The feature-and-model knob space is exhausted. Time to turn the
labeling knob.

## Iteration 9 — Swing labels (20-bar 1% threshold) on RF × 365d

Reason: default labels are `horizon=5 bars × 0.4% threshold`. At 1h
bars that's "will price make a 0.4% directional move in 5 hours" — a
short scalp horizon where noise dominates signal at 1h. Crypto-specific
research consistently finds tighter signal-to-noise on coarser
"swing" labels: longer horizon, larger threshold. Try 20-bar (20h)
× 1% threshold. Same model, same data, only the label changes.

If swing labels give RF WF ≥ 55% with overfit gap < +0.30, we have
evidence the merged features carry medium-horizon directional signal
that the default scalp label fails to surface.

Result: **breakthrough — WF accuracy jumps double-digits.**

| Sym | Default labels (it.7) | **Swing labels (it.9)** | Δ |
| --- | --- | --- | --- |
| ETH | 57% WF / +0.37 gap | **67% WF / +0.29 gap** | **+10pp / −0.08** |
| BNB | 55% WF / +0.39 gap | **60% WF / +0.35 gap** | **+5pp / −0.04** |

This is the largest single-iteration improvement of the night and the
overfit gap also improved. But: a 1%-in-20h label may be class-
imbalanced. If, say, 75% of 20h windows hit a 1% move in some direction
and the labels are mostly "up", a model that always predicts "up"
would score 75% just by memorising the base rate.

Iteration 10 tests both stress dimensions at once: longer history
(730d, the regime test that broke every prior result) **and** prints
the actual class balance so we know what baseline the 67%/60% really
beats.

## Iteration 10 — Swing labels at 730d + class-balance diagnostic

Reason: validate iteration 9 against both regime drift (730d test) and
the always-predict-majority null baseline (class-balance print). If
the WF accuracy stays ≥55% at 730d AND the class balance is roughly
50/50, we have the cleanest signal yet. Anything else and the swing-
label result was misleading.

Result: **definitive negative.** Class balance is fine (49-53% baselines)
but the 67%/60% from iteration 9 **plummets to 40%/34%** at 730d —
worse than coin flip. Overfit gap blows out to +0.56/+0.62.

| Sym | Class baseline | 365d WF | **730d WF** |
| --- | --- | --- | --- |
| ETH | 50.8% | 67% | **40%** |
| BNB | 52.6% | 60% | **34%** |

A model predicting *wrong* 34-40% of the time is the signature of
overfitting to one regime and predicting backwards in another. The
iteration 9 headline was a recent-regime artifact, just like every
prior WEAK SIGNAL of the night.

**Cross-iteration summary at this point:** every WEAK SIGNAL produced
in the lab (iterations 2, 3, 4, 7, 9) has been falsified by the same
regime-stability test (iterations 5, 8, 10). No combination of (TF,
model, label, symbol) tested so far produces a signal that holds
across a 2-year window. The strategy as currently configured **does
not have a stable edge.**

This isn't a failure — it's the lab earning its keep. Live trading
with the current setup would lose money.

## Iteration 11 — Window-length scan (ETH 1h swing+RF across 90/180/365/730)

Reason: a meta-question. We know 365d shows apparent signal that 730d
breaks. What about 90d? 180d? If WF accuracy scales monotonically with
shorter window (90d > 180d > 365d), the bot has a recent-regime-only
edge that only works if it's continuously retrained on a short window
— a meaningfully different operational story than 'has edge' or 'has no
edge'. If the scan is non-monotonic, then the 365d 'sweet spot' was
just lucky.

Result: **wildly non-monotonic** — confirming high-variance estimator.

| Window | ETH WF |
| --- | --- |
| 90d | **69%** |
| 180d | **38%** |
| 365d | **67%** |
| 730d | 40% |

Adjacent windows give 69% then 38% then 67%. This is the signature of
a high-variance walk-forward estimate, not a real edge — the result
depends violently on which slice of history happens to land in the
test folds. The 67% from iteration 9 was a lucky draw, not an edge.

## Iteration 12 — Variance stabilisation via 20-fold walk-forward

Result: with 20 folds we can compute a proper z-score.

| Sym | n | mean WF | stdev | z vs 50% |
| --- | --- | --- | --- | --- |
| ETH | 20 | 51.2% | 21.0% | +0.27 (NOT significant) |
| BNB | 20 | 60.7% | 20.7% | +2.31 (marginal p≈0.02) |

Fold distribution is bimodal — most folds <45% OR >55%, almost none
49-51%. The model regime-classifies rather than predicting direction
stably. The iteration 9 67% was the high tail of this distribution.

## Iteration 13 — Feature importance on BNB and ETH

Result: position-in-range is the only feature family the merge added
that meaningfully contributes.

ETH top 10 includes: pct_from_60_high (#6), pct_from_60_low (#9),
pos_in_60_range (#10). All NEW.
BNB top 10 includes: pct_from_60_low (#4), pct_from_60_high (#10).
Both NEW.
Cascade family ranks #20+ on both. HTF slopes rank #18+.

The merge work was directionally correct on position-in-range
(2-3 features in top 10), but cascade/HTF/F&G/funding/MR added
nothing the model actually uses.

## Iteration 14 — Reduced feature set (top-12 only)

Result: pruning to the top 12 features did NOT improve OOS. BNB went
from 57.2% (z=+1.98) → 55.6% (z=+1.44). Even noisy features were
helping RF via averaging. So the top-importance ranking was
directionally right, but the marginal noisy features weren't hurting.

## Iteration 15 — BNB 730d × 20-fold variance test

Result: BNB's last surviving marginal edge dies at 2 years.

  mean WF = 45.7%, stdev = 18.5%, z = **-1.05 (NOT significant)**
  per-fold range: 10% .. 78.3%

The per-fold scan shows accuracies that swing wildly. The 365d z=+2.31
was a regime artifact.

## Iteration 16 — Volatility-conditional labels

Result: unexpected — signal lives in LO-vol bars, not hi-vol.

| Sym | all bars | hi-vol | **lo-vol** |
| --- | --- | --- | --- |
| BNB | 57.2% z=+1.98 | 64.5% z=+1.42 | **61.1% z=+3.04** |
| ETH | 47.8% z=-0.62 | 42.2% z=-0.72 | 55.7% z=+1.44 |

This was the strongest positive of the night. The model gets direction
right ~61% of the time when volatility is below the 75th-percentile.

## Iteration 17 — Lo-vol gate at 730d

Result: it dies too.

| Sym | all bars | hi-vol | lo-vol |
| --- | --- | --- | --- |
| BNB 730d | z=-0.74 | z=-0.31 | **z=-0.47** |
| ETH 730d | z=+0.74 | z=+0.68 | z=+0.08 |

The lo-vol z=+3.04 from iteration 16 collapses to z=-0.47 at 730d.
Same regime story as everything else.

## Iteration 18 — Scalp labels (3-bar × 0.2%)

Result: stable noise. All cells have z between -0.47 and +1.25 (no
significance). Stdev is much lower (~7-10%) than swing (~15-20%) —
scalp labels are more stable estimators, they just don't have signal.

| Sym | 365d z | 730d z |
| --- | --- | --- |
| BTC | +0.60 | +0.34 |
| ETH | +0.12 | +0.65 |
| BNB | +1.25 | -0.47 |

## Final synthesis

See `.iterations/SYNTHESIS.md` for the consolidated overnight report.

Headline conclusion: **18 iterations × every reasonable knob = no
tradeable edge that survives 2 years**. Every "WEAK SIGNAL" was a
recent-regime artifact. The merge work added 2-3 genuinely informative
features (position-in-range); the other 11-12 are decorative. The bot
does not have an edge as currently designed.

Loop continues; remaining iterations will probe further but the
headline conclusion is locked in.

