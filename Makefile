# daytrade — Market Safety Observatory
# Paper / simulation only. No real trading, wallets, or money movement.

PY ?= python3

.PHONY: help install learn research observe dashboard report status watchlist test demo backtest clean \
        research-cascade research-cascade-fresh research-cascade-all \
        research-cost-horizon research-p5-4-validate simulate-winner

help:
	@echo "daytrade — make targets"
	@echo "  make install     install the package (editable, with dev extras)"
	@echo "  make learn       run the 30-day Paper Trading Learning Observatory"
	@echo "  make research    historical research lab — backtest over real history"
	@echo "  make observe     run the 24/7 Market Safety Observer (Ctrl+C to stop)"
	@echo "  make dashboard   launch the visual dashboard at http://127.0.0.1:8000"
	@echo "  make report      generate today's daily observatory report"
	@echo "  make status      show observatory status"
	@echo "  make watchlist   screen the watchlist for liquidity / quality"
	@echo "  make test        run the full test suite"
	@echo "  make demo        run the canonical decision demo"
	@echo "  make backtest    run a backtest"
	@echo ""
	@echo "  cascade × meta-gate research (P4-1, P5-2):"
	@echo "  make research-cascade        per_symbol 30d + pooled 30d, parallel + cached"
	@echo "  make research-cascade-all    + 90d per_symbol + 90d pooled (heavier)"
	@echo "  make research-cascade-fresh  same but --no-cache (rebuild parquet frames)"
	@echo ""
	@echo "  cost × horizon × gate sensitivity (P5-3, P5-4):"
	@echo "  make research-cost-horizon   full 1800-cell sweep, ~8min on 6 cores"
	@echo "  make research-p5-4-validate  pooled 90d validation of P5-3 winners"
	@echo "  make simulate-winner         equity curve for the headline cell (PNG to artifacts/)"
	@echo "                               override: SIM_SYM=SOLUSDT SIM_HZ=240 SIM_GATE=3.0"

install:
	$(PY) -m pip install -e ".[dev]"

learn:
	$(PY) -m daytrade learn --days 30 --interval 300

research:
	$(PY) -m daytrade research --symbol BTCUSDT --interval 1h --days 365

observe:
	$(PY) -m daytrade observe --interval 300

dashboard:
	$(PY) -m daytrade dashboard

report:
	$(PY) -m daytrade report-daily

status:
	$(PY) -m daytrade status

watchlist:
	$(PY) -m daytrade watchlist-check

test:
	$(PY) -m pytest -q

demo:
	$(PY) -m daytrade demo

backtest:
	$(PY) -m daytrade backtest

clean:
	rm -rf .pytest_cache __pycache__ src/**/__pycache__ build dist *.egg-info

# ----- research sweeps --------------------------------------------------------
# All sweeps write to docs/. The parquet feature cache at
# artifacts/cache/cascade_meta_frames/ makes re-runs ~20x faster.
# Symbols are the 6 majors we have cached on the host.

_CASCADE_SYMBOLS := BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT
_CASCADE_SCRIPT  := PYTHONPATH=src $(PY) scripts/sweep_cascade_meta_interaction.py \
                    --symbols "$(_CASCADE_SYMBOLS)" --gate-multiple 2.0 --cost-bps 24.0

research-cascade:
	$(_CASCADE_SCRIPT) --days 30 --training per_symbol --jobs -1 \
	    --out docs/CASCADE-META-30D-PER-SYMBOL.md
	$(_CASCADE_SCRIPT) --days 30 --training pooled \
	    --out docs/CASCADE-META-30D-POOLED.md
	@echo "  wrote docs/CASCADE-META-30D-{PER-SYMBOL,POOLED}.md"

research-cascade-all: research-cascade
	$(_CASCADE_SCRIPT) --days 90 --training per_symbol --jobs -1 \
	    --out docs/CASCADE-META-90D-PER-SYMBOL.md
	$(_CASCADE_SCRIPT) --days 90 --training pooled \
	    --out docs/CASCADE-META-90D-POOLED.md
	@echo "  wrote docs/CASCADE-META-90D-{PER-SYMBOL,POOLED}.md"

research-cascade-fresh:
	$(_CASCADE_SCRIPT) --days 30 --training per_symbol --jobs -1 --no-cache \
	    --out docs/CASCADE-META-30D-PER-SYMBOL.md
	$(_CASCADE_SCRIPT) --days 30 --training pooled --no-cache \
	    --out docs/CASCADE-META-30D-POOLED.md

# ----- cost × horizon × gate sensitivity (P5-3, P5-4) ------------------------

research-cost-horizon:
	PYTHONPATH=src $(PY) scripts/sweep_cost_horizon.py \
	    --symbols "$(_CASCADE_SYMBOLS)" \
	    --horizons "15,30,60,120,240" \
	    --gate-multiples "2.0,3.0,4.0,5.0" \
	    --cost-tiers "6,14,24" \
	    --days 30 --jobs -1 \
	    --out docs/COST-HORIZON-SWEEP-FINDINGS.md

research-p5-4-validate:
	PYTHONPATH=src $(PY) scripts/sweep_p5_4_validate.py \
	    --symbols "$(_CASCADE_SYMBOLS)" \
	    --horizons "120,240" \
	    --gate-multiples "3.0,4.0,5.0" \
	    --days 90 --cost-bps 24.0 \
	    --out docs/P5-4-POOLED-VALIDATION-FINDINGS.md

# Equity-curve simulator for the headline P5-3 winner cell.
# Override knobs: make simulate-winner SIM_SYM=SOLUSDT SIM_HZ=240 SIM_GATE=3.0
SIM_SYM ?= BNBUSDT
SIM_HZ  ?= 240
SIM_GATE ?= 4.0
SIM_DAYS ?= 90
simulate-winner:
	PYTHONPATH=src $(PY) scripts/simulate_winner.py \
	    --symbol $(SIM_SYM) --horizon $(SIM_HZ) \
	    --gate-multiple $(SIM_GATE) --days $(SIM_DAYS) --cost-bps 24.0
