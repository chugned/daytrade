# daytrade — Market Safety Observatory
# Paper / simulation only. No real trading, wallets, or money movement.

PY ?= python3

.PHONY: help install learn research observe dashboard report status watchlist test demo backtest clean \
        research-cascade research-cascade-fresh research-cascade-all

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
