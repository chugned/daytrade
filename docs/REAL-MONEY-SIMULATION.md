# What live trading with daytrade would actually look like

A simulation of the full picture: architectural change, execution
frictions, operational risks, and capital flow. **No live code yet.**
This document is the analysis you wanted before risking a euro.

## 1. Headline numbers (friction-adjusted)

Source: `daytrade.backtest.Backtester` on real Binance history,
90 days × 6 pairs × 1h, €1000 starting equity per pair.

| Scenario | 90-day return | Annualised | €/month per €1000 |
| --- | --- | --- | --- |
| **PAPER** (current backtest reports) | +6.00% | +24.0% | €20 |
| **REALISTIC** live | **+4.58%** | **+18.3%** | **€15** |
| **PESSIMISTIC** live | +2.82% | +11.3% | €9 |

Per-pair, realistic scenario:

| Pair | Trades | Paper | Realistic | Δ |
| --- | --- | --- | --- | --- |
| LINK/USDT | 57 | +10.83% | +8.65% | -2.17% |
| ETH/USDT | 52 | +7.24% | +6.33% | -0.91% |
| SOL/USDT | 53 | +6.97% | +5.53% | -1.44% |
| AVAX/USDT | 53 | +6.76% | +4.46% | -2.30% |
| BNB/USDT | 49 | +3.62% | +2.58% | -1.05% |
| BTC/USDT | 44 | +0.60% | -0.04% | -0.65% |

**6 of 6 pairs profitable in realistic live conditions** except BTC
which barely breaks even — its low volatility makes the bot's
percentage edge too small to overcome spread + latency.

## 2. Architecture — what changes from paper to live

```
                  CURRENT (PAPER ONLY)
  ┌────────────────────────────────────────────────────────────┐
  │  Binance public REST/WS  (read-only OHLCV + orderbook)     │
  └───────────────────────────┬────────────────────────────────┘
                              │ candles
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  daytrade.observatory.observer   (the live engine)         │
  │   ├─ fusion engine (TA + microstructure + macro + ML)      │
  │   ├─ regime gate / calibration / meta-label edge gate       │
  │   ├─ MTF + cascade + funding feature pipeline                │
  │   └─ position sizing (vol-scaled)                            │
  └───────────────────────────┬────────────────────────────────┘
                              │ Decision(action, entry, stop, target)
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  daytrade.paper.PaperBroker   ← IN-MEMORY ONLY              │
  │   (no orders, no fills, no money)                           │
  └───────────────────────────┬────────────────────────────────┘
                              │ TradeRecord
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  SQLite observatory DB  +  FastAPI dashboard                │
  └────────────────────────────────────────────────────────────┘


                  TARGET (LIVE)
  ┌────────────────────────────────────────────────────────────┐
  │  Binance public REST/WS (data)  +  AUTHENTICATED REST/WS    │
  │                              (private orders, balance, fills)│
  └───────────────────────────┬────────────────────────────────┘
                              │ candles + auth state
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  daytrade.observatory.observer                              │
  │   (UNCHANGED — the engine is exchange-agnostic)             │
  └───────────────────────────┬────────────────────────────────┘
                              │ Decision
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  daytrade.live.LiveBroker  ← NEW                            │
  │   ├─ exchange client (ccxt or python-binance)               │
  │   ├─ idempotent order placement (clientOrderId from         │
  │   │   ops.order_ids.generate_client_order_id — already built)│
  │   ├─ fill tracking + partial-fill aggregation                │
  │   ├─ reconciliation loop (ops.reconciliation — already built)│
  │   ├─ kill-switch hook                                        │
  │   └─ disconnects → fail closed (no new orders)               │
  └───────────────────────────┬────────────────────────────────┘
                              │ TradeRecord (real fills)
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  SQLite + dashboard + watchdog + remote log + notifier       │
  │   (the Secure branch pieces are already in place for this)   │
  └────────────────────────────────────────────────────────────┘
```

**What needs to be built:** the `LiveBroker` class. Everything else
already exists.

**LOC estimate:** 400-600 Python lines for a defensively-coded
LiveBroker + integration tests + a "live mode" config flag whose
default raises in `SafetyConfig` (you'd flip the rail explicitly when
ready). Realistic build time: 1-2 weeks careful work.

## 3. Execution-friction model (what each row in §1 means)

Per-trade extra costs applied on top of the modelled 24 bps round-trip:

| Friction | Realistic | Pessimistic |
| --- | --- | --- |
| Spread (BTC/ETH) | 0.5-1 bps each side | same |
| Spread (BNB/SOL) | 2-3 bps each side | same |
| Spread (LINK/AVAX) | 5-6 bps each side | same |
| Latency slippage | 2 bps each side | 8 bps each side |
| Partial fill probability | 10% → +5 bps | 20% → +5 bps |
| Stop-loss gap risk | not modelled | 2% of stops → +50 bps |
| Outage / missed exit | not modelled | 1% of trades → +30 bps |

Verified per trade against the actual 308-trade ledger. The
simulation script is `.iterations/live_simulation.py` — rerunnable.

## 4. Capital flow (one round-trip)

```
                                       Loss at each step
  Your bank account
       │
       ▼ SEPA bank transfer  →  Binance EUR wallet     -€1.00
       │
       ▼ Convert EUR → USDT  →  Binance spot wallet    -10 bps (~€1.00)
       │
       ▼ Trading capital deployed
       │
       │   (90 days, ~308 trades, +€45 realistic mean)
       │
       ▼ Convert USDT → EUR  →  Binance EUR wallet     -10 bps (~€1.00)
       │
       ▼ SEPA withdrawal     →  Your bank account      -€1.00
       │
       ▼
  Your bank account
```

**One-shot cost: €4 on €1000** (0.4%). Negligible relative to the
trading PnL. Worth doing once per quarter, not once per week — the
fees scale linearly per round-trip.

## 5. Operational risk register

| Risk | Likelihood | Impact | Mitigation status |
| --- | --- | --- | --- |
| API key compromise | low (cold storage) | catastrophic (drained) | `ops/api_keys.assert_trade_only` raises if key has WITHDRAW permission. Use trade-only API key, **never** withdraw-enabled. **READY** |
| Bot crashes / hangs | medium | high (open position stuck) | `ops/instance_lock` prevents double-spawn; launchd/systemd watchdog auto-restarts. **READY** for macOS + VPS |
| Exchange outage | medium | medium (no exits possible) | RuntimeConfig.max_data_age_seconds skips decisions on stale feeds. Manual kill-switch on dashboard. **READY** |
| Network loss on host | medium | medium | Same as above + watchdog restart. **READY** |
| Power loss on host | low (with VPS) | medium | VPS = uptime > local. Provisioning script ready in `deploy/provision-vps.sh`. **READY** |
| Disk full → can't log | low | low | log rotation in `ops.remote_log.attach_rotating_file_handler`. **READY** |
| Wrong-side fill / bot bug | low | high | Reconciliation loop (`ops.reconciliation`) compares expected vs exchange state every cycle, alerts on drift. **READY** |
| Flash crash / gap through stop | medium (annually) | medium (extra -50 bps) | Already modelled in pessimistic friction. **ACCEPTED** |
| KYC frozen / withdrawal hold | low | high (capital trapped) | Use a *trading* exchange you've already KYC-passed on; keep withdrawals small/regular. **ACCEPTED** |
| Tax reporting | certain | medium | Track trades; EU crypto-to-EUR conversions are taxable events. **NOT YET BUILT** |
| Strategy regime shift | medium (monthly?) | high (returns flip to negative) | Daily monitoring; hard drawdown stop. **NEEDS YOU** to watch |

**Three risks have no automated mitigation:** strategy-regime shift,
tax accounting, KYC freezes. These are real but human-managed.

## 6. What a typical live day actually looks like

Drawn from the engine code paths in `observer.py`:

```
00:00 UTC  Cycle starts. Fetch fresh OHLCV for each of 6 pairs.
00:00:02   Run features through pipeline (35 features × 6 pairs).
00:00:03   ML model + fusion engine score each pair.
00:00:03   Gates evaluate: regime gate, meta-label edge, MTF alignment,
           cascade-active block, funding extreme, calibrated confidence.
00:00:04   Decision: BUY ETH @ 3500.50, stop 3478, target 3545.

           → LiveBroker generates clientOrderId, posts market order
           → exchange confirms in ~200ms
           → fill at 3501.20 (70 bps slippage from signal price)
           → SQLite logs Trade(open)

00:00 to next signal: position is open. Cycle still runs every 5s,
           re-checking exit conditions:
              - hit target (3545)? → exit
              - hit stop (3478)?   → exit
              - max hold reached?  → exit
              - regime flip?       → exit

           Typical bar: 60-90 minutes hold time.

  Day rolls. Bot generates ~5-8 entry signals per day across 6 pairs.
  ~3-4 of those pass all gates and become real trades.
  Net result over the day: usually +0 to +€2 on €1000 capital.
  Some days: -€5. Some days: +€8.
  Over 90 days: +€45-60 realistic, mean +€15/month.
```

## 7. The disagreement to resolve before going live

Your live paper bot earned +€215 in 10 days = ~21.5%. This simulation
says realistic live should be ~4.6% per 90 days = ~0.5% per 10 days.
**The live paper bot is performing 40× better than the backtest
suggests.**

Three explanations:

1. **The current regime is exceptionally favourable** (recent
   research showed 365d works, 730d doesn't — we may be in the lucky
   slice).
2. **The live paper bot adapts** (the calibration and regime-gate
   accuracy estimates update as predictions resolve; the backtest
   uses static parameters).
3. **The live paper sample is too short** (10 days, ~30-50 trades on
   €1000). 21% in 10 days happens at noise level too.

**Test it in live with €100-200, hard-stop at €30 loss, 30 days.**
That's the *only* way to resolve the 40× discrepancy.

## 8. Recommended live-deployment checklist

These need to be done in order, none skippable:

- [ ] **Open a Binance subaccount** (separate from your main wallet)
- [ ] **Fund it with €100-200 only** — the kill-switch budget
- [ ] **Generate a trade-only API key** (no withdraw, no margin)
- [ ] **Build LiveBroker** (1-2 weeks)
- [ ] **Run paper-on-live-data for 7 days** (compares paper vs what the
      real exchange would have done — catches edge cases)
- [ ] **Flip `live_trading_enabled = true` in a config override**
      (the `SafetyConfig` validator currently refuses this; you'd
      add a `live_explicit_acknowledgement = true` field and patch
      the validator to allow it only with that opt-in)
- [ ] **Day 1: €10 stake-amount limit** — test the plumbing with
      tiny size
- [ ] **Day 2-7: €25 stake** — verify operationally
- [ ] **Day 8-30: full €100-200 deployment** — real signal data
- [ ] **Daily monitoring of:**
  - Drawdown vs hard stop (€30)
  - Reconciliation drift (any expected-vs-actual delta)
  - Win rate vs paper baseline
- [ ] **End-of-30-days decision:** did realistic live PnL track
      simulation §1, exceed it (like paper), or fail?

## 9. Hard safety rails you should preserve

- **Never disable the kill-switch.** Even when going live.
- **API key permissions = trade only.** No withdraw, ever. The
  `assert_trade_only` check should run at startup and refuse to
  start if the key can withdraw.
- **Funded amount = max acceptable loss.** Treat the €100-200 as
  fully at risk; don't put rent money in.
- **No leverage.** Spot only. Perpetuals add liquidation risk on
  top of strategy risk.
- **The Secure branch's safety code is already there.** Use it.

## 10. Where this leaves you

Realistic monthly numbers per €1000 deployed:

- **Conservative (this simulation):** €15/month
- **Optimistic (if live tracks live-paper):** €100-200/month
- **Target (yours):** €250/month
- **What you'd need:** ~€2-3k deployed at the realistic rate, OR
  ~€1k deployed if the live-paper rate sustains

To start: deploy €100-200, prove it works for 30 days, scale up
**only if PnL matches expectations**. Anything sooner is gambling.
