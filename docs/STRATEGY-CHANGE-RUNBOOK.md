# Strategy-change runbook — IF P5-4 confirms

> **⛔ DO NOT EXECUTE. Result invalidated 2026-06-02.**
>
> The equity-curve simulator (`scripts/simulate_winner.py`) re-ran
> the headline P5-3 winning cell (BNB 240m gate=4.0) on a 90-day
> window — the same window P5-4 was meant to use. Result on 90d:
> **−60.85 bp/trade × 742 trades = −45,147 bp cumulative.** The
> +30.87 bp/trade on the 30-day window matched, so the math is
> right — but the strategy is **regime-dependent**, not a stable
> edge. The recent 9-day held-out window happened to be a good
> regime; older 27 days are bad. The "winner" was sample-window
> luck.
>
> **Do not change the live config based on P5-3.** P5-4 was killed
> early once the simulator surfaced this — a pooled-90d sweep
> would have shown the same negative result more expensively.
>
> See `docs/COST-HORIZON-SWEEP-FINDINGS.md` for the original
> per-cell P5-3 numbers (still accurate for the 30d window).
> See `artifacts/equity_BNBUSDT_240m_g4.0.png` for the 90d
> equity curve that killed the recommendation.
>
> The body of this runbook is preserved below as a **template**
> for the NEXT strategy change that passes validation. Two
> corrections to apply when re-using:
>
> 1. `max_hold_bars` schema default is **48**, not 30 (the table
>    below was wrong).
> 2. Restart procedure (`nohup … & disown`) orphans launchd
>    supervision — write it as `launchctl kickstart -k <label>`
>    instead, and add a `pgrep` verification step.

---

# Template (do not execute as-is — see banner above)

This is the runbook for changing the live paper-bot config in
response to the P5-3 + P5-4 cascade research thread. It is
**only relevant if P5-4 produces a GO verdict** (see
`docs/P5-4-POOLED-VALIDATION-FINDINGS.md`).

> Paper-only. None of these steps enable live trading. The hard
> safety gate in `src/daytrade/config/schema.py::SafetyConfig`
> still requires the long human-typed acknowledgement to flip
> `live_trading_enabled` to true, regardless of any of these
> changes.

---

## The change in plain English

Switch the bot from "30-minute holds on all 6 symbols with gate=2.0"
to "**240-minute holds on BNB primarily, SOL secondary, with gate=4.0**"
because the P5-3 sweep showed that the current config is exactly
the wrong combination for the cost regime we're trading in.

Three knobs change. Everything else stays as-is.

| Knob | Current | Proposed | Why |
| --- | --- | --- | --- |
| `risk.max_hold_bars` | 30 | 240 | At 30m bars rarely resolve to a profitable target; at 240m the rebound has time to develop. |
| `gating.meta_label_edge_multiple` | 2.0 | 4.0 | The current threshold lets through ~1100 trades per symbol per 27d window — almost all losing after cost. ×4.0 selects ~220 trades with +30.87 bp net. |
| `watchlist.symbols` | 6 majors | `["BNBUSDT", "SOLUSDT"]` | BNB clears retail cost at 240m gate=4.0 with n=220. SOL clears at 240m gate=3.0 with n=70. The other 4 symbols don't get to net-positive at any tested horizon. |

---

## Pre-flight checks (run these first)

```bash
# 1. P5-4 has run and the TL;DR is GO (not NO-GO)
head -30 docs/P5-4-POOLED-VALIDATION-FINDINGS.md

# 2. Full suite green on current main
make test

# 3. The bot is currently healthy — don't change config on a sick bot
curl -sS http://127.0.0.1:8002/api/state | python3 -c "
import json, sys
d = json.load(sys.stdin)
for b in d['bots']:
    print(f\"{b['name']}: cycles={b['db']['latest_run']['cycles']}, hb_age={b['heartbeat_age_seconds']:.0f}s, errs24h={b['db']['errors_last_24h']}\")
"
# Expect: daytrade hb < 600s, errs24h == 0
```

If any check fails, **stop** and resolve before touching the config.

---

## The edit

Config lives in `src/daytrade/config/schema.py` defaults + any user
override in `~/.daytrade/config.toml` (if present). Prefer editing
the user override; the schema defaults stay as the "reference" baseline.

### Option A — minimal patch to schema defaults (simple, version-controlled)

Edit `src/daytrade/config/schema.py`:

```python
# RiskConfig (line ~365)
class RiskConfig(BaseModel):
    ...
-   max_hold_bars: int = Field(30, ...)
+   max_hold_bars: int = Field(240, ...)   # P5-3 / P5-4 finding
    ...

# GatingConfig (line ~521)
class GatingConfig(BaseModel):
    ...
-   meta_label_edge_multiple: float = 2.0
+   meta_label_edge_multiple: float = 4.0   # P5-3 / P5-4 finding
    ...
```

Watchlist is configured separately — check where the live
observer reads it from:

```bash
grep -rn "watchlist_config\|WatchlistConfig\|symbols=" src/daytrade/observatory/observer.py | head -5
```

Then update the symbol list to `["BNBUSDT", "SOLUSDT"]` at the
canonical source.

### Option B — config override file (revertible, no code change)

If `~/.daytrade/config.toml` exists (or a similar override path
that the AppConfig loader honours), add:

```toml
[risk]
max_hold_bars = 240

[gating]
meta_label_edge_multiple = 4.0

[watchlist]
symbols = ["BNBUSDT", "SOLUSDT"]
```

This keeps the change reversible without `git checkout`.

---

## Restart the bot

```bash
DAYTRADE_PID=$(pgrep -f "daytrade learn" | head -1)
echo "current daytrade pid: $DAYTRADE_PID"

kill -TERM $DAYTRADE_PID
for i in $(seq 1 30); do
  kill -0 $DAYTRADE_PID 2>/dev/null || { echo "exited after ${i}s"; break; }
done

cd /Users/nedimvejo/Desktop/coding/daytrade
nohup /usr/bin/env python3 -m daytrade learn --days 30 --interval 60 --real-data \
  > logs/daytrade.out.log 2> logs/daytrade.err.log < /dev/null &
disown
echo "spawned new daytrade pid=$!"
```

Wait for the first cycle to log: `tail -f logs/daytrade.log` — expect
"cycle 1: ..." within ~60s and a meta-model retraining log line
showing the new config in effect.

---

## What to watch in the first 2-3 hours

Three signals tell you whether the change took:

```bash
# 1. The bot's actual config — should show max_hold_bars=240
sqlite3 'file:artifacts/observatory.db?mode=ro' \
  "SELECT * FROM predictions ORDER BY id DESC LIMIT 3" | head -3
# expect: NOT thousands of predictions per hour like before — far fewer

# 2. Cycle count vs trade count — gate=4.0 should be 5x more selective
curl -sS http://127.0.0.1:8002/api/state | python3 -c "
import json, sys
d = json.load(sys.stdin)
db = next(b['db'] for b in d['bots'] if b['name'] == 'daytrade')
lr = db['latest_run']
ratio = db['closed_trades'] / max(lr['cycles'], 1)
print(f\"cycles={lr['cycles']}, closed_trades={db['closed_trades']}, trades/cycle={ratio:.4f}\")
"
# expect: trades/cycle drops from ~0.08 (current) to ~0.01-0.02

# 3. Errors — bumping max_hold_bars touches the triple-barrier label
#    horizon and the broker's auto-close timer. Watch for related errors.
sqlite3 'file:artifacts/observatory.db?mode=ro' \
  "SELECT ts, context, substr(message, 1, 100) FROM errors ORDER BY id DESC LIMIT 5"
```

---

## Forward-test phase (~1-2 weeks)

DO NOT change anything else for 1-2 weeks. The point of the
forward-test is to see whether the held-out backtest result
generalises to truly out-of-sample paper trades. Daily check-ins:

- Cumulative paper PnL trajectory (mission control already shows this)
- Per-symbol PnL split (BNB vs SOL — do both contribute?)
- Win rate vs the backtested expectation (~65%)
- Mean trade duration (should be ~4 hours if gate is firing at 240m)

If the forward-test diverges materially from the backtest, **stop and
investigate** before continuing — divergence is the signal that either
the backtest was overfit or a regime shift has happened.

---

## Rollback

If the change goes badly:

```bash
# Option A (if you used the schema edit): git revert
git revert <commit-sha>
# Then restart the bot per the "Restart the bot" section above.

# Option B (if you used the config override): remove or comment out
# the override lines in ~/.daytrade/config.toml and restart the bot.
```

The observatory DB stays intact — paper trade history isn't lost
on restart. The meta-model will retrain on the next cycle.

---

## What NOT to do

- **Don't enable live trading.** That requires the `SafetyConfig`
  acknowledgement phrase. Even with a confirmed strategy, going
  live is a separate decision involving real capital + a real
  exchange account.
- **Don't change multiple knobs after the initial 3-knob change.**
  If forward-test reveals an issue, you want to isolate the cause.
- **Don't shrink the forward-test window below 1 week.** Crypto
  regimes shift on day-to-day scales; one good day isn't a signal.
- **Don't touch nighttrade.** This research is on crypto only —
  the equity bot has its own surface and shouldn't move in sympathy.

---

## Reference

- `docs/COST-HORIZON-SWEEP-FINDINGS.md` — full P5-3 winners table
- `docs/P5-4-POOLED-VALIDATION-FINDINGS.md` — pooled validation
- `docs/RESEARCH-INDEX.md` — pointer index to all research docs
- `data/agent_roadmap.json` — P5-4 + P5-5 task tracking
