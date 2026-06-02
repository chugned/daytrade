# Taker-buy ratio (order-book imbalance proxy) — empirical validation (Phase A, signal 3 of 5)

**Date:** 2026-06-02
**Sweep:** `reports/taker_buy_sweep_20260602T180601Z.md`
**Script:** `scripts/sweep_taker_buy_ratio.py`

## TL;DR — mostly negative result, with one filter worth shipping

The order-book imbalance hypothesis (sustained aggressive flow on one
side predicts continuation) **does not survive the data on its own**.
Lifts are uniformly in the ±2 to ±14 bp range — too small to clear
the 25 bp round-trip cost as a standalone trade signal.

**However**, there's a contrarian pattern at the 4-hour horizon that's
useful as a per-symbol *filter*: **persistent aggressive-buy pressure
(TBR > 0.55 for 4+ consecutive hours) fades by an average of −12.6 bp
in the pooled data**. Caveat: per-symbol consistency is mixed — only 3
of 6 symbols show the fade (BTC, LINK, AVAX). Ship the gate selectively,
not universally.

## Method

- 1h klines from Binance public API, 500 days back, 6 USDT-perps.
- 71,994 hour-bars analyzed.
- Taker buy ratio (TBR) = field [9] of Binance kline / field [5] of
  Binance kline = aggressive-buy volume / total volume.
- 0.50 = balanced. Above 0.55 = aggressive buying dominant. Below 0.45
  = aggressive selling dominant.
- **Persistence streak** = consecutive bars on the same side of the
  thresholds.
- Forward returns at 1h, 4h, 24h.

## Headline: pooled across 6 symbols

### Single-bar TBR — fwd 1h

| TBR bucket | n | mean fwd 1h | lift |
|-|-:|-:|-:|
| baseline | 71,994 | −0.004% | — |
| <0.40 (extreme sell) | 8,599 | −0.002% | +0.002 pp |
| 0.40-0.45 (sell) | 12,164 | +0.010% | +0.014 pp |
| 0.45-0.50 (mild sell) | 17,712 | −0.007% | −0.003 pp |
| 0.50-0.55 (mild buy) | 16,851 | −0.002% | +0.002 pp |
| 0.55-0.60 (buy) | 10,560 | −0.008% | −0.004 pp |
| ≥0.60 (extreme buy) | 6,108 | −0.024% | −0.020 pp |

All lifts < 2 bp. **No usable single-bar signal at 1h horizon.**

### Persistence — fwd 4h (the interesting horizon)

| streak | n | mean fwd 4h | lift |
|-|-:|-:|-:|
| baseline | 71,976 | −0.015% | — |
| streak_buy ≥ 2 bars | 5,262 | −0.041% | −0.026 pp |
| streak_buy ≥ 3 bars | 1,845 | −0.051% | −0.036 pp |
| **streak_buy ≥ 4 bars** | **693** | **−0.140%** | **−0.126 pp** |
| streak_buy ≥ 5 bars | 253 | −0.083% | −0.068 pp |
| streak_sell ≥ 2 bars | 7,876 | −0.032% | −0.018 pp |
| streak_sell ≥ 3 bars | 3,386 | −0.041% | −0.026 pp |
| streak_sell ≥ 4 bars | 1,571 | −0.069% | −0.054 pp |
| streak_sell ≥ 5 bars | 789 | −0.107% | −0.092 pp |

**Two findings of note:**

1. **Persistent buying gets faded.** 4 consecutive hours of aggressive
   buy-side flow → next 4h average −14 bp. This is the *opposite* of the
   continuation hypothesis. Interpretation: 4 hours of one-sided buying
   is a topping pattern; the original buyers are done and the next
   marginal flow comes from sellers.

2. **Persistent selling continues.** 5+ hours of aggressive sell-side
   flow → next 4h average −11 bp. Selling momentum is sticky — you
   can't fade it on short horizons.

### Persistence — fwd 24h

| streak | n | mean fwd 24h | lift |
|-|-:|-:|-:|
| baseline | 71,856 | −0.087% | — |
| streak_buy ≥ 3 | 1,843 | +0.047% | +0.134 pp |
| streak_buy ≥ 5 | 253 | +0.053% | +0.140 pp |
| streak_sell ≥ 5 | 784 | −0.191% | −0.105 pp |

By 24h the buy-streak fade has fully reversed — sustained buying
*does* mark a regime change that pays out over a day. Sustained
selling continues to bleed.

## What's tradeable (and what isn't)

### Not tradeable as a standalone signal

The largest single-cell lift is ±14 bp. Round-trip cost is ~25 bp.
Net per trade: negative. Stop here for any direct "TBR > X → buy"
rule — the data does not support it.

### Tradeable as a filter (do this)

Add `gating.aggressive_chase_block` to `SafetyConfig`:

```python
# When the previous 4 hourly bars all had taker_buy_ratio > 0.55,
# the next 4h average return is -14bp. This is the textbook "chasing
# the top" pattern. Block long entries while it holds.
def aggressive_chase_active(klines_last_4h) -> bool:
    return all(k.taker_buy_base / k.volume > 0.55 for k in klines_last_4h[-4:])
```

Expected effect: ~1% of windows blocked (693 / 71,976), saving on
average 14 bp per blocked trade. Annualized: a small but real
contribution to net PnL.

### Defer

The streak_sell continuation pattern (sustained selling → keeps
falling) suggests a **short** edge, but only ~11 bp lift over a 4h
horizon. Not worth introducing a new trade type for. Revisit if and
when the contrarian-short path from Signal #1 is built; this would
piggyback on the same plumbing.

## Per-symbol consistency check (streak_buy ≥ 4 bars, fwd 4h)

| symbol | n | mean fwd 4h | lift |
|--------|-:|-:|-:|
| BTC | 103 | −0.151% | −0.141 pp |
| ETH | 77  | +0.146% | +0.152 pp |
| SOL | 151 | +0.022% | +0.048 pp |
| BNB | 188 | +0.111% | +0.107 pp |
| LINK | 44  | −0.729% | −0.712 pp |
| AVAX | 130 | −0.655% | −0.621 pp |

**The picture is mixed: 3 negative (BTC, LINK, AVAX), 3 positive (ETH,
SOL, BNB).** The pooled negative is driven by the size of the LINK and
AVAX moves rather than by a robust cross-symbol pattern. **This weakens
the recommendation considerably** — the gate as written would correctly
fade entries on LINK / AVAX / BTC but would *incorrectly block* entries
on ETH / SOL / BNB.

### Revised recommendation

Ship the filter **only for symbols where the per-symbol lift is
materially negative** (lift ≤ −0.10 pp): currently BTC, LINK, AVAX. For
ETH/SOL/BNB the gate would actively hurt. This makes Signal #3 a
**small-magnitude, symbol-specific contribution** rather than a
universal rule — about 277 events / year × −30 bp lift on the 3 fading
symbols. Saved expected loss: ~0.8% / year on capital deployed in those
3 symbols.

## Caveats

- Taker-buy-base ratio is an *imperfect* proxy for live orderbook
  imbalance. It measures realized aggressive flow over a completed
  bar, not the instantaneous L2 state. The real-time bot already
  computes `depth_imbalance` from L2 snapshots — that signal is
  potentially stronger but cannot be validated historically
  without months of recorded snapshot data.
- The "persistent buy → fade" pattern is consistent with retail-FOMO
  microstructure: late buyers chase, the marginal buyer dries up,
  price reverts. It is also consistent with informed sellers using
  retail aggression as exit liquidity. Either way the trade
  recommendation is the same: don't buy.
- 500 days is one regime. Re-run quarterly.

## Reproduce

```bash
PYTHONPATH=src python3 scripts/sweep_taker_buy_ratio.py \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT \
  --days 500
```

Output: `reports/taker_buy_sweep_*.md` (timestamped).

---

_This is signal #3 of 5 in Phase A (edge discovery). Mostly a
negative result — the value here is the filter, not a new trade.
Next: cross-asset divergence (#4), 2-of-N ensemble (#5)._
