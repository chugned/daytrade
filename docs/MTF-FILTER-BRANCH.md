# `Multi-Timeframe-Filter` branch — Tier-1 of the 10x research roadmap

This branch adds a **multi-timeframe trend alignment gate** — a 5th
decision gate that requires the 15-minute AND 1-hour trends to align
with the 1-minute signal direction before a BUY is allowed. The
academic literature (QuantPedia, QuantStart, multiple Bitcoin-specific
studies) documents **~80% false-signal reduction** from this technique
on intraday strategies; it's flagged as "the single most underused
improvement" for 1-minute bots.

```
main (live paper bot)
  └── Multi-Timeframe-Filter (this branch)      ← off-by-default new gate
```

## What's inside

| Path | Purpose |
|---|---|
| `src/daytrade/observatory/multi_timeframe.py` | `check_higher_tf_alignment()` — resamples 1m → 15m + 1h, returns alignment verdict + slopes |
| `src/daytrade/observatory/observer.py` | Wired as the 5th gate in `_maybe_open_position` (after regime / calibration / meta-model) |
| `src/daytrade/config/schema.py` | `gating.require_higher_tf_alignment` (default `False` — opt-in until sweep-validated) and `gating.higher_tf_min_slope` |
| `tests/test_multi_timeframe.py` | 7 tests: uptrend → BUY allowed / SELL blocked, downtrend opposite, insufficient history permissive, min_slope tightens |
| `scripts/sweep_mtf_filter.py` | Empirical sweep: does requiring HTF alignment select bars with better forward returns? |

## How it works (mechanically)

1. The observer already pulls 240 one-minute candles per symbol per
   cycle — that's **4 hours of context** with no additional fetches.
2. On a `_maybe_open_position` call, the new gate:
   - Resamples those 240 1m bars to **16 fifteen-minute bars** and
     **4 hourly bars**.
   - Computes OLS slopes (price-normalised) on each timeframe.
   - For a BUY: requires both slopes > `higher_tf_min_slope`.
   - For a SELL: requires both slopes < `−higher_tf_min_slope`.
3. Misalignment → trade blocked + activity-feed event tagged
   `higher-TF gate blocked`.

Insufficient HTF history (e.g. a freshly-started bot with < 4 hours of
data) → **allow through**, matching the regime gate's "needs evidence
to judge" policy.

## Why it's *off by default*

Same discipline as every other strategy knob: prove it on real data
before flipping it on. Run:

```
PYTHONPATH=src python3 scripts/sweep_mtf_filter.py \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT --days 30 --fwd-min 60
```

The script reports, for each candidate `min_slope` threshold:
- fire rate (% of bars where the filter agrees)
- forward 60-minute return when aligned vs misaligned
- lift vs the baseline (any-bar return)

If the lift is positive, flip the flag on:

```yaml
# configs/default.yaml
gating:
  require_higher_tf_alignment: true
  higher_tf_min_slope: 0.0001     # or whatever the sweep picks
```

If the lift is negative or marginal, leave it off — the gate would just
shrink trade count without improving precision.

## Initial sweep result (20 days · BTC + ETH · 1m · 60m forward window)

| min_slope | fires % | fwd@aligned | fwd@misaligned | baseline | lift |
|---:|---:|---:|---:|---:|---:|
| 0.00000 | 42.5 | −0.033% | −0.020% | −0.026% | **−0.007%** ⚠ |
| 0.00005 | 38.2 | −0.036% | −0.019% | −0.026% | **−0.011%** ⚠ |
| 0.00010 | 34.7 | −0.040% | −0.018% | −0.026% | **−0.015%** ⚠ |
| 0.00020 | 26.7 | −0.033% | −0.023% | −0.026% | **−0.008%** ⚠ |
| 0.00050 | 10.9 | **+0.009%** | −0.030% | −0.026% | **+0.035%** ✅ |

**Honest reading.** Only the strictest threshold (`min_slope=0.0005`)
selects bars with better-than-baseline forward returns. Looser
thresholds (0.0–0.0002) actually *hurt* — they fire on weak, noisy
"trends" that turn out to be worse than random bars.

This is the same pattern the meta-gate sweep showed: a *relative*
default is not always best; sometimes you need a tight absolute bar.

If you enable this gate, **use the swept value:**

```yaml
gating:
  require_higher_tf_alignment: true
  higher_tf_min_slope: 0.0005     # sweep-best — anything below is worse
```

The gate then fires on only ~11% of bars but picks bars with a +0.035%
hourly lift over baseline. The base rate at which it fires is low — so
the bot trades less, but each trade is from a higher-quality regime.

Re-sweep on different windows + symbols before locking it in.

## How this composes with the other branches

| | Independent of MTF | Composes with MTF |
|---|---|---|
| `Secure` engineering primitives | ✅ — they don't touch decision logic | Merging both is conflict-free |
| `Freqtrade-Port` strategy file | partly | The freqtrade strategy can adopt the same filter — added in its own follow-up |

## Tests / status

- 308 tests pass on this branch (+7 new MTF-filter tests).
- Bot is unaffected: the new gate is opt-in via config; `main` still
  runs without it.
- Empirically: sweep results saved to `reports/mtf_sweep_*.md` —
  honest verdict, same template as previous sweeps.
