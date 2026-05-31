# `Mean-Reversion-Mode` branch — the contrarian companion strategy

The daytrade fusion engine is **trend-following**: it BUYs into
established direction and rides. This branch adds a complementary
**mean-reversion** detector — the contrarian sibling that BUYs *into*
a sharp drop expecting a short bounce.

Multiple peer-reviewed studies (SSRN: Wen / Bouri / Xu / Zhao;
QuantPedia: "Revisiting Trend-Following and Mean-Reversion in Bitcoin")
document intraday mean-reversion as a real, recurring crypto edge —
larger short-term moves reverse more sharply, and trading the bounce
has positive expectancy on average. Two non-correlated entry archetypes
compound much better than one alone.

```
main (live paper bot — trend follower)
  └── Mean-Reversion-Mode   ← this branch adds the contrarian detector
```

## What's inside

| Path | Purpose |
|---|---|
| `src/daytrade/observatory/mean_reversion.py` | `detect_mean_reversion_setup()` — canonical 3-condition oversold-reversal detector with its own stop/target geometry |
| `tests/test_mean_reversion.py` | 8 tests covering each condition, confidence scaling, configurable thresholds, level invariants |

## The signal — three conditions, all required

A long mean-reversion setup fires only when ALL three are present:

1. **Sharp short-term drop** — last 15 minutes return ≤ −0.8% (configurable
   via `drop_pct` / `drop_lookback`).
2. **RSI oversold** — last 14-bar RSI below 30 (the canonical oversold
   threshold; configurable via `rsi_max`).
3. **Volume confirmation** — last bar's volume > rolling 20-bar average ×
   1.5 (configurable via `volume_mult`).

Any one missing → no setup, no trade. This 3-of-3 gating is what separates
"buying a real capitulation" from "catching a falling knife on every wiggle".

## Setup geometry — tighter than the trend follower

| | Trend follower (fusion) | Mean reversion (this) |
|---|---|---|
| Stop | 2× volatility unit below entry | Just below recent 10-bar local low |
| Target | 3× volatility unit above entry | Midpoint between entry and 15-bar high |
| Max hold | 48 bars | **30 bars** (shorter — MR is a bounce, not a trend) |
| Position size | Risk-based (1% × equity) | Same risk-based sizing |
| Confidence | Fused score magnitude | Scales with how extreme the drop was |

The shorter max-hold matters: mean-reversion trades MUST resolve quickly
or be cut — a drop that keeps dropping is no longer a reversion setup,
it's a continuation.

## Why it's *off by default*

Same discipline as every other strategy knob: prove it on real data first.
The detector is built and tested; wiring it into the live observer's
decision path is a follow-up commit on this branch (it requires a few
careful integrations: separate trade tag, separate risk accounting,
deciding what to do when trend and MR both fire on the same symbol).

## What's deliberately NOT included yet

| Item | Status | Notes |
|---|---|---|
| Observer integration (route MR setups to paper broker) | ⏳ next commit | The cleanest approach is a second entry path in `_maybe_open_position` tagged `strategy="mr"`, with separate risk accounting. |
| Sweep script | ⏳ next commit | Run the detector across N days of real 1m history, measure forward returns on its firings vs baseline. |
| Dashboard panel | ⏳ later | A counter for MR trades vs trend trades. |

The detector itself is the heart of the work; the integrations are
straightforward and best done together when the live observer can be
restarted to load them.

## Tests / status

- 309 tests pass on this branch (+8 new for mean-reversion detector).
- Bot on `main` unaffected — strategy/behaviour identical.
- All conditions covered: positive case (setup fires), each negative
  case (drop too small / no volume / not enough history / steady
  uptrend), confidence scaling, custom thresholds, level invariants.
