# Overnight research synthesis — 2026-06-01

## TL;DR

**The strategy in its current shape does not have a tradeable edge —
and the ML approach loses to a one-line momentum heuristic.**

Across 23+ iterations testing every reasonable combination of
timeframe, model, label, symbol, history window, volatility regime,
confidence threshold, and feature subset, no configuration produced a
walk-forward accuracy that:

1. Was statistically above the 50% chance baseline, AND
2. Held up across a 2-year history window, AND
3. Generated positive PnL after realistic round-trip costs (24 bps).

Two findings stand above the rest:

- **Every "WEAK SIGNAL" result was a recent-regime artifact.** When the
  same setup was extended back in time, it collapsed to chance or
  swung to *below* chance.
- **A one-line momentum rule beats the trained RandomForest on every
  test** (z=+4.02 on BNB 730d, z=+3.33 on ETH 365d, z=+2.43 on BTC
  730d). The ML pipeline isn't even learning what a child could
  write in 10 seconds — and even THAT one-line rule loses money
  after costs.

This is the most valuable possible negative finding: the bot would
lose real money if deployed as-is. The lab earned its keep tonight.

## What we tested

| Knob | Values tried |
| --- | --- |
| Timeframe | 5m, 1h, 2h, 4h |
| Model | Logistic, RandomForest, GradientBoosting |
| Symbols | BTC, ETH, BNB, XRP, ADA, SOL, DOGE, LTC, AVAX, LINK, DOT, MATIC |
| History window | 30d, 90d, 180d, 365d, 730d |
| Labels | scalp (3-bar 0.2%), default (5-bar 0.4%), swing (20-bar 1%) |
| Feature subset | all 35, top-12 importance-ranked |
| Volatility filter | none, hi-vol only, lo-vol only |
| Walk-forward folds | 5 (default), 20 (variance-controlled) |
| Confidence threshold | 0.55, 0.60, 0.65, 0.70 |
| Stacked gates | confidence × lo-vol |
| Trivial benchmarks | majority class, momentum-follow, mean-revert |
| PnL realism | round-trip 24 bps (10 bps fee × 2 + 2 bps slippage × 2) |

## What we learned

### 1. The walk-forward estimate is high-variance
With 5 folds (the default) the WF accuracy estimate has stdev ~21% per
fold on swing labels — a 20% to 85% range across folds. Even at 20 folds
the stdev is ~16% on swing labels, ~9% on scalp labels.

The implication: **most "WEAK SIGNAL" verdicts in the lab are likely
fold-positioning luck**, not real edge. Treat any single-experiment WF
accuracy below 55% on 5 folds as indistinguishable from noise.

### 2. The "365d shows edge, 730d doesn't" pattern repeats endlessly
Every model × label × symbol that scored 55-67% WF accuracy on 365d
fell to 34-50% at 730d. This is a clear sign the model is learning
patterns specific to one regime that don't generalise.

Examples:
- ETH 1h × swing × RF: **67%** at 365d → **40%** at 730d
- BNB 1h × swing × RF: **60%** at 365d → **34%** at 730d
- BNB 1h × default × RF: **57%** at 365d → **49%** at 730d
- BNB 1h × lo-vol gate: **z=+3.04** at 365d → **z=-0.47** at 730d

### 3. Position-in-range features genuinely add signal
Feature importance on RF (iterations 13a/13b):

ETH top 10 includes 3 NEW features (all position-in-range).
BNB top 10 includes 2 NEW features (position-in-range).

So 2-3 of the 14 features merged this session are genuinely
informative. The cascade family (5 features) ranks #20+ on both
symbols. HTF slopes (2 features) rank #18-27. **The merge work was
about 20% directional, 80% decorative**.

### 4. The signal — such as it exists — lives in LOW-volatility bars
Iteration 16 showed BNB lo-vol bars score z=+3.04 (vs all-bars z=+1.98).
ETH lo-vol z=+1.44 vs all-bars z=-0.62. This was the strongest single
positive of the night.

But: it also died at 730d (iteration 17), so it's another regime artifact.

### 5. Backtest equity curves are misleading
Backtests reported +43-89% returns even when WF accuracy was at noise.
The strategy generates trades regardless of model quality; gains in
backtest can come from sizing/timing luck. **The lab's
`Sharpe-like > 4.0 = OVERFIT` heuristic correctly flagged every such
result.**

### 6. A one-line momentum rule beats the ML pipeline
Iteration 22 compared the trained RF against three trivial benchmarks
on swing labels. Result on the subset of bars with a defined label:

| Sym × window | RF z | Momentum z |
| --- | --- | --- |
| BNB 730d | -0.80 | **+4.02** |
| ETH 365d | -0.66 | **+3.33** |
| BTC 730d | +0.36 | **+2.43** |
| BNB 365d | +1.74 | +2.80 |

Momentum is a one-line heuristic: "predict same direction as last
bar's return". It surfaced a real and statistically significant
directional signal on the actionable subset; the trained ML model did
not. The ML approach isn't even learning the simplest possible thing.

### 7. …but momentum still loses money after costs
Iteration 23 ran the momentum rule as a paper strategy (enter long
when last bar was up, hold horizon bars, exit, pay 24 bps round-trip
cost). Result: **every (symbol × window × horizon) cell loses money**:

- Mean PnL per trade: -16 to -30 bps (cost > edge)
- Win rates: 33-47%
- Total PnL across 8 symbols × 2 windows × 2 horizons: all negative

So even the "best simple rule" tonight is undeployable. The 53-54%
directional accuracy on the actionable subset doesn't translate to
PnL because (a) you can't tell in advance which bars are
"actionable" and (b) the 24 bps cost dominates the per-trade edge.

## What doesn't work, definitively

- 5m timeframe (overfit gap +0.50 on every symbol)
- BTC predictions at any TF/model/label tested
- SOL predictions at any config (always overfit)
- Cross-asset stat-arb (already known from research-90d sweep)
- Cascade-active gate (already known)
- Fear & Greed contrarian gate (already known)
- The full merged feature set evaluated at 730d
- The default 5-bar 0.4% label
- Volatility-conditional gating across 2 years
- Tight 3-bar scalp labels

## What might work — candidates for tomorrow

These are *untested* directions — every one would need its own
multi-iteration validation. None are recommendations to deploy.

1. **Fundamentally different features**: orderbook microstructure
   (depth imbalance, queue analytics), funding-rate-derived features,
   cross-exchange basis. The current feature set is all technical and
   all 1m-bar-derived; that may be a dead zone.

2. **Online learning with very short windows**: if the 365d "edge"
   reflects regime-specific structure, a model retrained every N days
   on the last N days might capture it. The cost is high-variance
   estimation in production — exactly what 5-fold WF showed.

3. **Different label semantics**: e.g., "will price drop > X% in next
   Y bars THEN recover" (catch-the-bottom labels). The current
   directional and breakout labels reward catching trends; cryptos
   often reward catching reversals.

4. **Sentiment data with proper validation**: not the F&G contrarian
   gate (falsified), but properly-trained feature use of social/news
   data. Requires a real data source.

5. **Accept that no edge exists at 1h on technicals alone** and pivot
   to higher-TF (daily) swing trading with a totally different
   strategy template. The 1m / 1h technical-features-only approach
   has been falsified by tonight's work.

## What NOT to do

- **Do not flip any gate on**. Every gate in `GatingConfig` is `off`
  for empirically-defensible reasons.
- **Do not enable live trading.** `SafetyConfig` will refuse — by
  design. The strategy hasn't earned the right to live capital.
- **Do not chase the 67% number from iteration 9.** It's a regime
  artifact; it does not generalise.

## Files produced tonight

- `.iterations/STATE.json` — iteration tracker
- `.iterations/FINDINGS.md` — per-iteration narrative log
- `.iterations/it6_*.py` through `.iterations/it18_*.py` — the
  experiment scripts. Each is self-contained and rerunnable.
- `docs/SESSION-INDEX.md` (on Secure branch) — covers the 10 research
  branches merged in tonight, separate from these iterations.

## Hard safety guarantees

Throughout tonight's 18 iterations:
- Zero live-trading code was modified.
- Zero credentials were used (every fetch was the public Binance
  read-only endpoint).
- The `SafetyConfig` validator remains intact and will raise on any
  attempt to flip `live_trading_enabled` or `allow_real_orders`.
- All iteration scripts are research-only; they call the model
  evaluation pipeline, not the order pipeline.
