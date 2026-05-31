# `Freqtrade-Port` branch — execution platform for the daytrade strategy

This branch (off `Secure`) packages the daytrade strategy as a freqtrade
strategy file, plus the supporting scripts to cross-validate and
pre-flight the live path. **No live trading is enabled** — the artifacts
here are deployment-ready, not deployment-active.

`Freqtrade-Port` builds on `Secure`, which builds on `main`. The chain:

```
main (live paper bot)
  └── Secure (10 engineering primitives)
        └── Freqtrade-Port (execution layer)  ← you are here
```

## What's inside

| Path | Purpose |
|---|---|
| `freqtrade-port/strategies/DaytradeStrategy.py` | The `IStrategy` class — the 4-gate strategy in freqtrade's lifecycle |
| `freqtrade-port/config.json` | Dry-run config; exchange credentials blank by default |
| `freqtrade-port/README.md` | Install → cross-validate → dry-run → live checklist |
| `scripts/cross_validate.py` | Item #12: side-by-side daytrade vs freqtrade backtest comparison |
| `scripts/preflight.py` | Run before any live step: validates all engineering primitives are present and functional |
| `tests/test_freqtrade_port_syntax.py` | Light guards that the strategy file and config stay valid without forcing freqtrade as a test-time dep |

## How the daytrade gates map onto freqtrade

| daytrade concept | freqtrade hook |
|---|---|
| Phase 1: ATR-width stops | `custom_stoploss` |
| Phase 1: triple-barrier time-stop | `custom_exit` |
| Phase 2: regime gate | `_regime_gate_passes` (called from `confirm_trade_entry`) |
| Phase 3: confidence calibration | `_calibration_gate_passes` (same) |
| Phase 4: meta-labelling | `_meta_gate_passes` (same) |
| Fusion engine signal | `populate_indicators` + `populate_entry_trend` |
| Trend-reversal exit | `populate_exit_trend` |

The three feedback-driven gates (regime / calibration / meta) ship as
honest pass-through stubs that match daytrade's own behaviour during
its early evidence-gathering days. Promoting each to an active filter
is a follow-up — either by reading freqtrade's `Trade` history through
`Trade.query`, or by integrating FreqAI for the meta-model.

## How to use it (when you're ready)

```bash
# 1. Install freqtrade in its own venv on the deployment host
python3.11 -m venv ~/.venvs/ft && source ~/.venvs/ft/bin/activate
pip install --upgrade pip && pip install freqtrade

# 2. Drop the strategy + config into a freqtrade user_data dir
freqtrade create-userdir --userdir /opt/freqtrade-data
cp freqtrade-port/strategies/DaytradeStrategy.py /opt/freqtrade-data/strategies/
cp freqtrade-port/config.json                    /opt/freqtrade-data/config.json

# 3. Backtest. Then run the cross-validation against daytrade's backtest:
freqtrade download-data --exchange binance --timeframe 1m --days 30 \
    --userdir /opt/freqtrade-data
freqtrade backtesting --strategy DaytradeStrategy \
    --userdir /opt/freqtrade-data --config /opt/freqtrade-data/config.json
PYTHONPATH=src python3 scripts/cross_validate.py \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT --days 30 \
    --freqtrade-result /opt/freqtrade-data/backtest_results/.last_result.json

# 4. Pre-flight before any live step:
PYTHONPATH=src python3 scripts/preflight.py     # must report 10/10 pass

# 5. Long dry-run on real Binance data (no orders):
freqtrade trade --strategy DaytradeStrategy \
    --userdir /opt/freqtrade-data --config /opt/freqtrade-data/config.json
```

## What is *still* not done after this branch

- **Strategy edge gate** — only time on `main` fixes this. Continue the
  paper run, accumulate trades, watch the win-rate + walk-forward.
- **FreqAI integration / meta-model loading** — the three gates inside
  `DaytradeStrategy` are stubs. Activating them is the next sprint.
- **Live execution prerequisites checklist** — fully documented in
  `freqtrade-port/README.md`; the operator's job to satisfy each item
  before flipping `dry_run` off.

Until those are done, this branch ships a *correct execution wrapper for
an unproven strategy*. That is the right thing to have ready, in
exactly that order.
