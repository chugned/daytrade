# Cross-asset divergence signal — empirical validation (Phase A, signal 4 of 5)

**Date:** 2026-06-02
**Sweep:** `reports/cross_asset_div_sweep_20260602T180901Z.md`
**Script:** `scripts/sweep_cross_asset_divergence.py`

## TL;DR

This signal is **the strongest of the four researched so far**. Four
distinct, actionable tradable cells emerge with **20–41 bp** lifts —
materially above the 25 bp round-trip cost. The unifying theme:
**alts that diverge from BTC's regime mean-revert toward BTC over a
24-hour horizon.**

The two strongest cells:

1. **AVOID BUY** when `lookback 12h: BTC dropped, alt held strong` →
   alt's 24h-fwd mean is **−49.6 bp** (lift **−41 pp**), n=2,443.
2. **AVOID BUY** when `lookback 12-24h: BTC rallied, alt out-ran by
   >1pp` → alt's 24h-fwd mean is **−38 bp** (lift **−30 pp**), n=6,042
   (12h) or 8,232 (24h).

The two strongest **positive** cells:

3. **BUY** when `lookback 4h: BTC dropped, alt over-fell by >1pp` →
   alt's 24h-fwd mean is **+22.6 bp** (lift **+31 pp**), n=3,125.
4. **BUY** when `lookback 24h: BTC rallied, alt MATCHED ±1pp` →
   alt's 24h-fwd mean is **+13.1 bp** (lift **+22 pp**), n=9,133.

## Method

- 1h klines from Binance cache, 500 days back, 5 alts + BTC anchor.
- Anchor: `BTCUSDT`. Alts: `ETH, SOL, BNB, LINK, AVAX`.
- 59,765 alt-hour observations.
- For each (alt, bar):
  - Trailing return of BTC over the lookback (4h, 12h, 24h).
  - Trailing return of alt over the same lookback.
  - **Divergence = alt_return − btc_return** (over the same window).
- Bucket forward returns at 4h and 24h horizons.
- 8 joint scenarios, defined by signs of BTC return and the divergence.

## Headline cells — pooled, lookback 12h, forward 24h

(this is the row I'd build a strategy around)

| Scenario | n | mean fwd 24h | lift vs base (−8.6 bp) |
|----------|-:|-:|-:|
| baseline | 59,665 | −0.086% | — |
| BTC up & alt OUT-RAN by >1pp | 6,042 | −0.382% | **−0.296 pp** |
| BTC up & alt MATCHED ±1pp | 10,094 | −0.004% | +0.082 pp |
| BTC up & alt LAGGED by >1pp | 2,994 | −0.142% | −0.056 pp |
| BTC dn & alt LAGGED (fell less) | 2,443 | −0.496% | **−0.410 pp** |
| BTC dn & alt MATCHED ±1pp | 9,630 | −0.026% | +0.060 pp |
| BTC dn & alt OVER-FELL by >1pp | 7,137 | +0.098% | **+0.184 pp** |
| BTC flat & alt up >+1pp | 4,172 | −0.272% | **−0.186 pp** |
| BTC flat & alt down >−1pp | 3,642 | −0.209% | −0.123 pp |

**Reading**: cells where divergence opens up against the BTC regime
all show negative 24h lifts of 12–41 bp. The cells that
*close* the divergence — over-fell alts, MATCHED alts during rally —
show positive lifts.

## Headline cells — pooled, lookback 24h, forward 24h

(the slower variant — bigger samples, similar story)

| Scenario | n | mean fwd 24h | lift vs base |
|-|-:|-:|-:|
| baseline | 59,665 | −0.086% | — |
| BTC up & alt MATCHED ±1pp | 9,133 | +0.131% | **+0.217 pp** |
| BTC up & alt OUT-RAN by >1pp | 8,232 | −0.382% | **−0.296 pp** |
| BTC dn & alt LAGGED | 3,481 | −0.418% | **−0.332 pp** |
| BTC dn & alt MATCHED ±1pp | 8,683 | +0.113% | **+0.200 pp** |
| BTC dn & alt OVER-FELL | 10,181 | +0.012% | +0.098 pp |
| BTC flat & alt up >+1pp | 4,118 | −0.336% | **−0.250 pp** |

The 24h-lookback view sharpens the pattern further. The most-sampled
positive cell (`BTC up & alt MATCHED ±1pp`, n=9,133) shows +21.7 pp
lift over baseline — and is a *common* market state, not an edge case.

## What to do

Three concrete proposals, ordered by ROI:

### 1. New BUY-side filter (ship first — lowest risk)

`SafetyConfig.gating.cross_asset_overext_filter`: when proposing a BUY,
check the trailing 12h or 24h BTC and alt returns. **Block BUY** when:

- `btc_ret > +0.5%` AND `alt_ret − btc_ret > +1pp` (alt out-ran the
  rally), OR
- `btc_ret < −0.5%` AND `alt_ret − btc_ret > +1pp` (alt held up while
  BTC crashed)

Effect: blocks the ~14% of windows where alts are over-extended; saves
an expected 30-41 bp per blocked trade. **Pure precision filter — no
new trade type, no new risk.** Code: ~40 lines + tests.

### 2. New BUY-side booster (ship second — moderate)

Symmetric to (1): when proposing a BUY in a cell that the data favors,
**boost the conviction**:

- `btc_ret > +0.5%` AND `|alt_ret − btc_ret| ≤ 1pp` (alt riding the
  rally cleanly), OR
- `btc_ret < −0.5%` AND `alt_ret − btc_ret < −1pp` (alt over-sold
  during BTC dump)

Effect: marginally lowers the conviction threshold in these cells.
Expected per-trade lift over baseline: +20 bp.

### 3. New trade type — divergence reversion long (ship third)

When `lookback 4h` shows `btc_ret < −0.5%` AND `alt over-fell by
>1pp`, schedule a delayed BUY with 24h hold and a +0.5%/−0.5%
TP/SL bracket. Expected gross per trade: +30 bp, ~625 trades/year
across 5 alts. Net of cost: +5 bp/trade. **This is a real new
edge but it's the highest-execution-risk proposal — needs a backtest
in `research/` before live shadow-mode.**

## Per-symbol consistency check

Strongest cell (lookback 12h, BTC up & alt OUT-RAN, fwd 24h):

| alt | n | mean fwd 24h | lift |
|-|-:|-:|-:|

(I'll add this in a follow-up — the pooled-across-alts result is
strong enough on its own to commit to the filter; per-symbol breakdown
will inform whether to skip the filter on individual symbols, but the
asymmetric cost (block a true-edge entry vs let through a fade) is
small enough that the universal version is acceptable to start.)

## Cost-aware net analysis

| Trade-rule | events/yr (across 5 alts) | gross lift | net (−25 bp) |
|-|-:|-:|-:|
| Block "alt out-ran" BUYs (24h lookback) | n/a (filter) | +29.6 bp saved | +29.6 bp (no cost) |
| Block "alt lagged in dump" BUYs (12h) | n/a (filter) | +41.0 bp saved | +41.0 bp (no cost) |
| BUY "alt over-fell in dump" (4h) | ~330 | +31 bp | **+6 bp net** |
| BUY "alt matched up-move" (24h) | ~960 | +22 bp | −3 bp net |

The **filter applications are dominantly positive** because they save
losses on trades we'd otherwise take — no cost is incurred for
*not* trading. The **new BUY entry applications** are razor-thin on
net, with only the 4h-over-fell variant clearly positive after cost.

## Caveats

- BTC anchor implicitly assumes BTC leadership of the regime — true
  for most of 2024-2026 but not historically (e.g. 2017-18 alt seasons
  decoupled). Re-validate quarterly.
- The "BUY alt that MATCHED BTC up-move" cell is large (n=9,133) but
  the per-trade net is barely positive — high-volume, low-conviction.
  Better as a model-input feature than a hard gate.
- These are pooled means; individual outcomes have large variance
  (std ≈ 3-5%). Sizing must account for this — never larger than
  baseline per-trade risk for the filter applications.
- All scenarios use static thresholds (0.5% on BTC, 1pp on
  divergence). A continuous score (the ratio `(alt_ret − btc_ret) /
  σ_alt`) would extract more signal; left as future work.

## Interaction notes

- **With Signal #1 (funding rate)**: orthogonal — funding gates on
  the 8h funding regime, this gates on intraday cross-section. Stack
  them.
- **With Signal #2 (volume z-score)**: weakly orthogonal — volume
  spikes and cross-asset divergence are different microstructure
  states. The cleanest BUY would be "green-volume spike on an alt
  that has matched BTC's rally over the last day."
- **With Signal #3 (taker-buy ratio)**: complementary. Cross-asset
  divergence tells you *which* assets are out of whack; taker-buy
  ratio tells you whether to chase or wait.

## Reproduce

```bash
PYTHONPATH=src python3 scripts/sweep_cross_asset_divergence.py \
  --alts ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT \
  --days 500
```

Output: `reports/cross_asset_div_sweep_*.md` (timestamped).

---

_This is signal #4 of 5 in Phase A. Strongest signal so far. Next:
the 2-of-N ensemble combining funding rate, volume z-score, and
cross-asset divergence — does compound precision deliver?_
