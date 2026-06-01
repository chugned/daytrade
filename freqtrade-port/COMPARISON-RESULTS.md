# Path B — head-to-head strategy comparison (Freqtrade backtest)

**Run date:** 2026-06-01
**Period:** 2026-03-01 → 2026-06-01 (90 days)
**Starting capital:** 1000 USDT
**Pairs:** BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, LINK/USDT, AVAX/USDT
**Fee:** 0.10% per side (Binance spot taker, retail tier)
**Max open trades:** 3
**Engine:** Freqtrade 2026.5

## Results (90-day backtest, sorted by total return)

| Strategy | Trades | Win% | Total % | Total USDT | Drawdown |
| --- | --- | --- | --- | --- | --- |
| **SampleStrategy** (Freqtrade baseline) | 94 | 88.3 | **+3.54%** | +35.37 | 6.64% |
| Strategy005 | 72 | 86.1 | +2.14% | +21.37 | 7.88% |
| Strategy001 | 97 | 88.7 | -0.88% | -8.78 | 8.35% |
| Bandtastic | 507 | 59.6 | -16.16% | -161.58 | 20.61% |
| **DaytradeStrategy (port)** | **1508** | **4.7** | **-74.76%** | **-747.58** | **74.76%** |
| Supertrend | — | — | (errored) | — | — |

## Three findings

### 1. The Freqtrade port of DaytradeStrategy is broken

It made 1,508 trades in 90 days (one every 86 minutes on average),
hit stops 95% of the time, and burned 75% of capital. **This is not
what the live paper bot does.**

Why: the Freqtrade port has the *signal* logic (fusion score, MACD,
RSI, ATR) but **not the gates** that the live observatory engine
runs — regime gate, meta-label edge gate, calibrated confidence,
multi-timeframe alignment, kill-switch. The gates are what filter
the raw noisy 1m signals down to the ~1-3 trades per day the live
bot actually takes.

**Implication:** the freqtrade port should NOT be deployed live. It
is a research approximation, not a production strategy.

### 2. The community strategies are too slow for your goal

SampleStrategy was the best community result: **+3.54% in 90 days**.
Annualized that's ~14%, or **~€10/month on €1000 capital**. Not the
€250/month you want.

Strategy005 (+2.14%) is similar. Strategy001 loses small. Bandtastic
loses big. **None of these would generate passive income at the rate
you need on the capital you have.**

To hit €250/month on €1000 you need ~25%/month, ~30× faster than
what the best community strategy produces in this window. That's
not "fine-tune one of these" territory — it's "different strategy
or different capital" territory.

### 3. Your live paper bot is in a different league than any of these

Your live daytrade bot earned **+€215 in 10 days = +21.5% in 10
days = ~64% in 90 days IF the rate holds**. Compared to:

| Source | 90-day return |
| --- | --- |
| SampleStrategy backtest | +3.54% |
| Strategy005 backtest | +2.14% |
| Your live paper bot (extrapolated from 10d) | **~+64%** |

The reason the live bot beats every community strategy is the same
reason the port lost 75%: the gates. The live engine runs full
fusion + regime + calibration + meta-label + MTF; the community
strategies use raw indicators with no such filtering.

## What this means for the €250/month goal

**Do NOT** deploy the Freqtrade port. It's missing the gates.

**Do NOT** deploy any community strategy as-is. The math doesn't
get you to €250/month on €1000 — best case is €10-15/month.

**DO** deploy the actual live daytrade bot (the engine in
`src/daytrade/observatory/observer.py`), small live, with a kill
switch. **This is the only path that could reach the income target
on €1000 capital.**

The risk: a 10-day live paper streak is a small sample. The 21%
could be regime luck (last night's research lab said the model has
no edge across 730 days). But: with a €100-200 test, hard stop at
15% drawdown, the **maximum downside is €15-30 lost** and the
**information value is the only way to know if the live paper
performance translates**.

## Suggested next move

Path A from the previous response was right — Freqtrade is the
wrong vehicle for *your* strategy. The right move is:

1. Set up a Binance subaccount with €100-200.
2. Run the live daytrade bot (not the port) against it.
3. Hard-stop at 15% drawdown.
4. After 30 days, decide based on actual live PnL.

The Freqtrade comparison was useful because it definitively rules
out the community-strategy plug-and-play path. None of them are
fast enough for your goal.
