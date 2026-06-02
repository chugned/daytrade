# Phase A — edge discovery, summary (2026-06-02)

Five signals researched empirically against 500 days of Binance 1h
data across 6 symbols (BTC, ETH, SOL, BNB, LINK, AVAX). Each got its
own sweep script, report, and findings doc. Net result:

| # | Signal | Verdict | Strongest cell | Cost-clear? |
|--:|---|---|---|:--:|
| 1 | Funding rate gate | **SHIP** | -47 pp lift avoided when funding ≥ +0.0001, fires 13% | ✅ (no cost — it's a filter) |
| 2 | Volume z-score | **SHIP** | green-vol z≥+4 → +18 bp lift fwd 1h, all 6 symbols positive | ✅ on alts at z≥+5 |
| 3 | Taker-buy ratio (orderbook proxy) | **PARTIAL** | streak_buy ≥ 4 → -14 bp lift fwd 4h, but only 3/6 symbols | partial (per-symbol filter) |
| 4 | Cross-asset divergence | **SHIP** | alt-out-ran-BTC → -30 bp lift fwd 24h, n=8232 | ✅ as filter, marginal as entry |
| 5 | 2-of-3 ensemble | **DO NOT SHIP** | k=2 → -21 pp lift (wrong direction!) | ❌ inverts |

## What to do next (concrete, sequenced)

### Step 1 — minimum-risk ship (the filters)

These are pure precision filters: block BUY entries when the condition
holds. No new trade types, no new risk, no behavior change for
correctly-conditioned trades. Three additions:

1. Flip `gating.use_funding_rate_gate = True` at `funding_extreme_positive = 0.0001`. (Already coded in `observatory/funding.py`; just flip the flag.)
2. Add `gating.cross_asset_overext_block` — block BUY when:
   - 12h `btc_ret > +0.5%` and `alt_ret − btc_ret > +1pp` (alt out-ran rally), or
   - 12h `btc_ret < −0.5%` and `alt_ret − btc_ret > +1pp` (alt held up in dump).
3. Add `gating.aggressive_chase_block` — block BUY on **BTC, LINK, AVAX only** when the previous 4 hourly bars all had taker_buy_base/volume > 0.55.

Combined expected effect: a few percent of would-be-losing BUYs
filtered out per year. Zero downside on the trades that pass through.
Code surface: ~150 lines + tests + an ADR each. Estimated 1-2 days of
TDD work.

### Step 2 — moderate-risk ship (the booster)

Add `gating.volume_spike_confirmation` — when the model proposes a
BUY AND the trigger bar has volume z ≥ +4 AND closed green,
*lower* the conviction threshold by 1-1.5×. Symmetric mirror: when
proposing a SELL AND volume z ≥ +5 AND closed red, hold off — the
24h reversion bias kicks in. Code surface: ~50 lines + tests.

### Step 3 — higher-risk ship (the new trade type)

Implement the contrarian *capitulation long*: when an alt over-fell
vs BTC during a BTC dump (4h lookback), schedule a delayed BUY 1h
after the trigger bar with a 24h hold and ±0.5% TP/SL bracket.
Expected +6 bp net per trade × ~330 events/year.

This **is a new trade type** and requires:
- Backtest validation in `research/` with proper triple-barrier labels.
- Risk controls — max concurrent capitulation positions, sizing.
- ADR documenting the strategy and its kill conditions.

Defer until Steps 1+2 are live and showing the expected effect in
the live shadow paper bot.

## Constraints respected

- **Paper trading only.** None of this enables live trading. Every
  proposal extends the existing paper-broker plumbing.
- **All signals were validated on out-of-sample data** (the live bot
  has not been trading these as gates, so the historical sweep is
  not contaminated by the bot's own actions).
- **All sweeps are reproducible** — scripts in `scripts/sweep_*.py`,
  reports time-stamped in `reports/`.

## Files added in Phase A

```
docs/
  FUNDING-RATE-VALIDATED-2026-06-02.md
  VOLUME-ZSCORE-VALIDATED-2026-06-02.md
  TAKER-BUY-RATIO-VALIDATED-2026-06-02.md
  CROSS-ASSET-DIVERGENCE-VALIDATED-2026-06-02.md
  ENSEMBLE-2OF3-VALIDATED-2026-06-02.md
  PHASE-A-EDGE-DISCOVERY-SUMMARY.md   ← you are here

scripts/
  sweep_volume_zscore.py
  sweep_taker_buy_ratio.py
  sweep_cross_asset_divergence.py
  sweep_ensemble_2of3.py
  (sweep_funding_gate.py — pre-existing, re-run with broader universe)

reports/   (gitignored; locally generated)
  funding_sweep_20260602T*.md
  volume_z_sweep_20260602T180033Z.md
  taker_buy_sweep_20260602T180601Z.md
  cross_asset_div_sweep_20260602T180901Z.md
  ensemble_2of3_sweep_20260602T181345Z.md
```

---

_End of Phase A. Phase B = ship the validated signals as gating-layer
code with tests + ADRs (Steps 1-2 above). Phase C = the new
trade type (Step 3)._
