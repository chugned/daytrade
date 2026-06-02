# Research index

Pointer index to every research finding shipped to `docs/`. Read
this first if you're trying to figure out which knob to turn. Each
row links to the deep-dive doc + summarises the verdict in one line.

## Sessions

- **Session 1** (pre-2026-06-02) — covered by `docs/SESSION-INDEX.md`.
  Six research branches: meta-features, F&G sentiment, liquidation
  cascade, cross-asset pairs, cascade-as-feature. Headline:
  CASCADE_EXHAUSTION direction signal on SOL is real (+9.7 bp at 30m,
  n=101). Wired in as a feature, not a gate.
- **Session 2** (2026-06-02 onwards) — this index covers it. Picks
  up the SOL cascade finding and asks "is it tradeable?".

---

## Session 2 — Tradability of the cascade signal

### P3-1 — Cross-asset validation of CASCADE_EXHAUSTION

| File | `docs/CASCADE-VALIDATION-FINDINGS.md` |
| --- | --- |
| Asks | Does the SOL cascade edge generalise across symbols? |
| Method | Detector → forward returns, 30d × 6 majors |
| Verdict | **Direction holds**: 4/6 symbols positive at 30m. SOL +18.87, BNB +9.15, AVAX +10.46, BTC +10.04. ETH/LINK negative. **None clear 24 bp retail cost on raw direction alone.** |

### P4-1 — Cascade × meta-gate interaction (per-symbol, 30d)

| File | `docs/CASCADE-META-INTERACTION-FINDINGS.md` |
| --- | --- |
| Asks | Does combining cascade-exhaustion with the meta-gate lift precision past cost? |
| Method | Per-symbol meta-model 70/30 split, 5 slice analyser |
| Verdict | **Intersection fails** — n=0-2 overlap; the gate is anti-selecting cascade bars. **No slice clears cost.** Per-symbol meta-model overfits 30d. Recommends UNION test next. |

### P5-2 — Cascade UNION/override, 90d both training modes

| File | `docs/CASCADE-META-90D-PER-SYMBOL.md` + `docs/CASCADE-META-90D-POOLED.md` |
| --- | --- |
| Asks | Does the UNION (cascade OR meta-gated) lift over meta-gate alone hold on a longer window with both training modes? |
| Method | per_symbol AND pooled training, 90d, 30m horizon, gate=2.0 |
| Verdict | **UNION lift is +0.06 to +1.11 bps** across 5/6 symbols, consistent but **too small to flip net-positive on its own at 30m**. Pooled rescues `meta_gated` from per-symbol overfit but doesn't reach break-even either. |

### P5-3 — Cost × horizon × gate sensitivity sweep ⭐

| File | `docs/COST-HORIZON-SWEEP-FINDINGS.md` |
| --- | --- |
| Asks | Is there ANY combination of (symbol, horizon, gate, cost) that gets to net-positive on held-out data? |
| Method | 6 symbols × 5 horizons × 4 gates × 3 costs = 1800-cell matrix, 8 min on 12 cores |
| Verdict | **YES — 18 cells clear retail cost.** **BNB 240m gate=4.0 = +30.87 bp net on n=220** is the best edge×events product. **SOL 240m gate=3.0 = +58.17 bp net on n=70**. The prior "all slices negative" results were a **horizon problem, not a strategy problem** — 30-minute holds capture too little of the rebound. |

### P5-4 — Pooled-90d validation of P5-3 winners (validation gate before any config change)

| File | `docs/P5-4-POOLED-VALIDATION-FINDINGS.md` (in progress) |
| --- | --- |
| Asks | Do the P5-3 winners survive pooled training + 90d window? |
| Method | Pooled training on all 6 symbols × 90d × focused matrix (BNB/SOL × {120, 240} × gate {3, 4, 5}) |
| Verdict | _Running — TL;DR at top of doc will say GO or NO-GO_ |

---

## Operational findings (not algo research)

### ADR-0001..0005 — observatory operational fixes

| ADR | What |
| --- | --- |
| 0001 | AlertManager → `alerts` table, not `errors` (dashboard counter clean) |
| 0002 | bot_runs PID liveness check + heartbeat self-heal (no spurious crashed rows) |
| 0003 | `db.recent_errors()` excludes `alert:*` by default (public API change) |
| 0004 | `db.prune_old(days=30)` + day-rollover hook (bounded DB growth) |
| 0005 | `_RetryingConnection` proxy retries SQLite `locked`/`busy` errors |

### Mission control improvements (session 2)

| File / commit | What |
| --- | --- |
| `src/daytrade/mission_control/cpu_history.py` | CPU sparkline alongside RAM, host + per-bot |
| `src/daytrade/mission_control/app.py::find_bot_processes` | Caffeinate wrapper filtered out (no double-counting; doesn't mask dead bots) |
| `src/daytrade/mission_control/ram_history.py` | Pre-existing — RAM sparkline + leak detection |

### Nighttrade-specific (deployed via `bash deploy/sync.sh`)

| Doc / commit | What |
| --- | --- |
| nighttrade ADR-0005 | `yfinance.threads=False` — bulletproof against host thread-budget pressure |
| nighttrade ADR-0006 | `_RetryingConnection` — same as daytrade ADR-0005, ported |
| `src/nighttrade/dashboard/tailnet_middleware.py` | Dashboard binds 0.0.0.0 + tailnet-only middleware (decouples from Tailscale flaps) |
| `deploy/sync.sh` | Adds explicit `launchctl kickstart` + post-deploy health check |

---

## Roadmap state

The mission control roadmap at `data/agent_roadmap.json` is the
live record. Current session-2 in-progress / queued items:

- **P5-4** (in progress) — pooled-90d validation of P5-3 winners
- **P5-5** (blocked on P5-4) — wire `max_hold_bars=240, meta_label_edge_multiple=4.0, watchlist=[BNB, SOL]` if P5-4 confirms

---

## Reproduce everything

All sweeps are Makefile targets:

```bash
make research-cascade        # P3-1 + P4-1 baseline (30d, 8s cached)
make research-cascade-all    # + P5-2 90d sweeps (heavier)
make research-cost-horizon   # P5-3 — the big strategic sweep (~8 min on 12 cores)
make research-p5-4-validate  # P5-4 — pooled validation of winners
```

Parquet feature cache at `artifacts/cache/cascade_meta_frames/`
makes re-runs ~20x faster than first-time builds. Cache key
includes `(symbol, days, max_hold)` so horizon changes invalidate
cleanly.

---

## Hard rules — unchanged by any of this

- **Paper / simulation only**. `SafetyConfig` in
  `src/daytrade/config/schema.py` refuses to load any config with
  `live_trading_enabled=true` unless a long human-typed acknowledgement
  matches exactly.
- **No config change goes live without forward-test**. Even when P5-4
  confirms P5-3, the path is: change paper config → watch 1-2 weeks
  of forward-test → only then consider live (which requires user
  action beyond this codebase).
