# Freqtrade port — production execution wrapper

This directory packages the daytrade strategy as a [freqtrade](https://github.com/freqtrade/freqtrade)
strategy, so the *execution* side (orders, fills, partials, reconnects)
runs on a battle-tested platform while the *intellectual property* (the
four gates, the ATR stops, the meta-labelling) stays in daytrade.

It does NOT install freqtrade or enable live trading. It is a deployment
artifact that lives in this repo so the strategy and config travel
together.

```
freqtrade-port/
├── strategies/DaytradeStrategy.py    # the IStrategy class
├── config.json                       # dry-run config (live blanked)
└── README.md                         # you are here
```

## 1. Install freqtrade (one-time)

```bash
# In its own venv, anywhere on disk:
python3.11 -m venv ~/.venvs/ft && source ~/.venvs/ft/bin/activate
pip install --upgrade pip
pip install freqtrade

# Initialize a user_data dir somewhere:
freqtrade create-userdir --userdir /opt/freqtrade-data
```

## 2. Drop the strategy in

```bash
cp freqtrade-port/strategies/DaytradeStrategy.py \
   /opt/freqtrade-data/strategies/

cp freqtrade-port/config.json /opt/freqtrade-data/config.json
```

## 3. Cross-validation backtest (item #12 of the engineering plan)

Run **the same period** through freqtrade and daytrade; the results
should track each other within noise. If they diverge sharply, the port
has a bug — find it BEFORE going further.

```bash
# Freqtrade side:
freqtrade download-data --exchange binance --timeframe 1m \
    --days 30 --userdir /opt/freqtrade-data
freqtrade backtesting --strategy DaytradeStrategy \
    --userdir /opt/freqtrade-data --config /opt/freqtrade-data/config.json \
    --timerange 20260418-20260518

# Daytrade side (already proven):
PYTHONPATH=src python3 -m daytrade research \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT --interval 1m --days 30

# Compare: scripts/cross_validate.py (see repo root)
```

Acceptance criterion: same direction-of-effect on win-rate, Sharpe,
total return; magnitude within ±20%. (Exact numerical equivalence is
unrealistic — fills, candle alignment, and ROI semantics differ
between engines.)

## 4. Dry-run on real Binance

```bash
freqtrade trade --strategy DaytradeStrategy \
    --userdir /opt/freqtrade-data --config /opt/freqtrade-data/config.json
```

With `dry_run: true` in config.json, freqtrade pulls real market data
but never sends orders. Let this run **alongside the daytrade observer**
for at least four weeks. Their equity curves should track closely; any
sustained divergence is the live-vs-paper gap revealing itself and
must be understood before going live.

## 5. Live execution — the absolute prerequisites

Before flipping `dry_run` to `false`, ALL of these must be true:

- [ ] **Strategy edge** — 500+ paper trades on daytrade with positive
      out-of-sample expectancy across multiple regimes
- [ ] **Engineering primitives** — Secure branch merged; single-instance
      lock, watchdog supervisor, staleness guard, idempotent client-IDs,
      startup reconciliation, push notifications, remote logging — all
      live (`docs/SECURE-BRANCH.md`)
- [ ] **Cross-validation** — freqtrade backtest tracks daytrade backtest
- [ ] **Dry-run validation** — 4+ weeks of freqtrade dry-run tracking
      daytrade paper closely
- [ ] **Trade-only API key** — verified by `daytrade.ops.api_keys.assert_trade_only`;
      withdrawal permission DISABLED; IP-allowlisted to the VPS
- [ ] **VPS provisioned** — `deploy/provision-vps.sh` ran cleanly,
      Tailscale set up, dashboard NOT public
- [ ] **Trivially small initial capital** — €100–200, not your savings
- [ ] **Pre-committed scaling rule written down** — no capital increase
      until X live trades / Y months of positive evidence

`docs/REAL-MONEY-RISKS.md` has the full inventory and mitigation matrix.

## Known port limitations

| Daytrade feature | Freqtrade port status |
|---|---|
| Technical layer (RSI, MACD, ATR) | ✅ ported |
| Microstructure regime (chop_zone) | ⚠️ proxy via trend-slope vs vol — true orderbook layer needs informative pair |
| Macro layer | ❌ not yet ported (requires external feed; documented gap) |
| Fusion engine | ✅ ported (technical + microstructure weights) |
| Regime gate | 🔄 stub — needs accumulated freqtrade trade history (low-priority follow-up) |
| Calibration gate | 🔄 stub — same |
| Meta-labelling gate | 🔄 stub — production needs either pre-trained .pkl OR FreqAI integration |
| ATR-width stops | ✅ ported via `custom_stoploss` |
| Triple-barrier time-stop | ✅ ported via `custom_exit` |

The three "stub" gates pass through by default — matching daytrade's
own behaviour during its initial "gathering evidence" period. Promoting
each to a production gate is a follow-up sprint once freqtrade has
accumulated its own trade history.

## Why not just enable live in daytrade?

Daytrade is structurally paper-only by design — there is no live-order
code path, and the test suite enforces that invariant. We did this on
purpose: the research microscope and the execution platform should be
SEPARATE systems. This separation is one of the strongest safety
guarantees in the architecture. Don't undo it.
