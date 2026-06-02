# Claude working notes — daytrade

> Context preserved for future Claude sessions on this codebase. Read this
> first; it captures architectural decisions, conventions, and operational
> facts the repo doesn't otherwise document. Update as you make material
> changes — out-of-date notes are worse than no notes.

## What this is

Crypto / perpetual-futures research and paper-trading platform. Python 3.9,
FastAPI dashboard, SQLite observatory database, scikit-learn meta-model.

**Paper trading only.** `SafetyConfig` (`src/daytrade/config/schema.py`) refuses
to load any config where `live_trading_enabled=true` unless a long human-typed
acknowledgement phrase matches exactly. Live-trading code exists as scaffold in
`src/daytrade/live/` (LiveBroker, BinanceExchange, ShadowExchange) but every
write path is gated behind `LiveConfig.dry_run=True` by default. There is no
single-flag flip to live.

## Tool surface

The `Makefile` is the canonical entry point. Don't invoke `pytest` / `uvicorn` /
the CLI directly when a make target exists. Important targets:

```
make test         # full pytest suite
make observe      # start the long-running learning bot
make dashboard    # FastAPI dashboard on :8000
make research     # historical research lab
make backtest     # one-shot backtest
make watchlist    # symbol screener
make report       # daily report generator
```

The mission-control dashboard (separate from daytrade's own) lives in
`src/daytrade/mission_control/` and serves on port 8002. It monitors both
this repo and `~/nighttrade/` (see *Two-directory split for nighttrade* below).
Start it with `python -m daytrade.mission_control --port 8002`.

## Where state lives — DON'T edit these

| Path | What it holds |
| --- | --- |
| `artifacts/observatory.db` | Live SQLite of the running bot — predictions, trades, equity, snapshots |
| `data/agent_activity.jsonl` | Mission-control activity feed (auto-written) |
| `data/agent_roadmap.json` | Mission-control current roadmap |
| `data/ram_history.jsonl` | RAM samples for mission-control sparkline |
| `data/now.json` | The bot's "what am I doing right now" status (atomic write) |
| `logs/daytrade.log` | RotatingFileHandler (50MB × 5 = 250MB cap) |
| `logs/db-writes.log` | Size-rotated by `db._rotate_writelog_if_needed` (50MB × 3) |

If you need to inspect these, **read-only** (`sqlite3 :ro` URI, `tail`). Never
write. The bot is running 24/7 against them.

## Architecture map

```
src/daytrade/
├── observatory/        # The 24/7 engine
│   ├── observer.py     #   the main loop — START HERE for engine work
│   ├── database.py     #   SQLite + schema + heartbeats + prune_old
│   ├── feed.py         #   LiveMockFeed (for tests)
│   ├── real_feed.py    #   RealMarketFeed (Binance public API, httpx pool)
│   ├── alerts.py       #   AlertManager — writes to alerts table (NOT errors)
│   ├── trading_broker.py  # TradingBroker Protocol + DBPaperBroker + LiveBrokerAdapter
│   └── ...
├── live/               # Live-trading scaffold — gated off by default
│   ├── broker.py       #   LiveBroker (mirrors PaperBroker; daily-loss cap; idempotent orders)
│   ├── exchange.py     #   Exchange Protocol + MockExchange
│   ├── binance.py      #   ccxt-backed adapter; writes_enabled=False default
│   ├── shadow.py       #   Routes reads→real, writes→mock (smoke test layer)
│   └── config.py       #   LiveConfig (paranoid defaults)
├── ops/                # Operational primitives
│   ├── instance_lock.py    # PID-file single-instance enforcement
│   ├── reconciliation.py   # Drift detection
│   ├── api_keys.py         # Trade-only key validator (refuses withdraw permission)
│   ├── order_ids.py        # Idempotent clientOrderId generator
│   ├── notify.py           # Telegram / ntfy / log notifier
│   └── remote_log.py       # Rotating file handler + remote HTTP sink
├── mission_control/    # Unified dashboard for daytrade + nighttrade
├── dashboard/          # daytrade's own dashboard (port 8000)
├── ml/                 # Predictive + meta models
├── features/           # 35 feature columns (pipeline.py is the single
│                       # source of truth — both train + inference call it)
├── backtest/, research/, validation/, risk/, paper/, exchanges/, …
└── safety/             # forbid_real_trading guard
```

## Conventions

**TDD is mandatory** for any new code (see `~/.claude/skills/test-driven-development`).
Write failing test, watch it fail, write minimal code, verify pass.

**No completion claims without `make test` evidence** — even for "obvious" fixes.

**ADRs in `docs/adr/`** for any change to: a strategy, a safety gate, a broker
adapter, an observatory schema, or a public CLI flag.

**Strict pytest markers** are defined in `pyproject.toml`:
- `@pytest.mark.safety` — paper-only enforcement tests
- `@pytest.mark.leakage` — train/test contamination tests
- `@pytest.mark.integration` — multi-component tests
Respect them.

## Recent load-bearing decisions (see `docs/adr/` for full context)

- **0001**: `AlertManager` writes to `alerts` table (not `errors`). The dashboard's
  "errors_last_24h" counter no longer counts informational alerts like
  "VETUSDT illiquid" or "model accuracy collapsed to 36%".
- **0002**: `mark_dangling_runs_crashed` now PID-liveness-checks before
  marking a run crashed. Prevents a sibling-bot startup from clobbering the
  live observer's run row.
- **0002**: `heartbeat()` also restores `status='running'` and clears
  `stopped_ts` — a spuriously-crashed row self-heals on next heartbeat.
- **0003**: `db.recent_errors()` excludes `context LIKE 'alert:%'` by default.
  Pass `include_alerts=True` for the legacy behaviour.
- **0004**: `db.prune_old(days=30)` deletes aged rows from `activity_events`,
  `market_snapshots`, `symbol_health` and runs `PRAGMA wal_checkpoint(TRUNCATE)`.
  Hooked into the daily roll-over.

## Two-directory split for nighttrade

Nighttrade has two directories on the host:
- `~/Desktop/coding/nighttrade/` — dev (source of truth for code, where I edit)
- `~/nighttrade/` — deployed (where launchd runs the bot, source of truth for state)

After editing nighttrade code, run `bash deploy/sync.sh` from nighttrade dev
to rsync into the deployed directory and reload launchd. Mission control reads
nighttrade's DB from the **deployed** directory.

Daytrade has no such split — runs from `~/Desktop/coding/daytrade/`.

## Anti-patterns — DO NOT reintroduce

Bugs that were fixed once and now have regression tests. If a test
named after one of these fires, **read the linked ADR before
"fixing" the test**.

| Anti-pattern | Why it's banned | Reference |
| --- | --- | --- |
| Parallel-thread fetch over the symbol universe (e.g. `yf.download(threads=True)`, `ThreadPoolExecutor(max_workers=None)`) | One OS thread per ticker × 100s of symbols → `RuntimeError: can't start new thread` whenever the host has modest concurrent thread pressure (macOS `ulimit -u` ≈ 2784, easily eaten by browsers + dev tools + other bots). Sibling repo nighttrade tripped this twice. Use sequential fetch, or a hard small cap (≤4) with a pinning test. | nighttrade ADR-0005 |
| `AlertManager` writing to `errors` table | Pollutes `errors_last_24h` counter with informational messages. | ADR-0001 |
| Marking another bot's `bot_run` as crashed without PID liveness check | Spurious "crashed" rows masked real crashes. | ADR-0002 |

If you add new parallelism over a symbol universe (real-time or
research), add a regression test in the same commit that pins the
upper worker count.

## Operational facts

- Daytrade runs as `python -m daytrade learn --days 30 --interval 60 --real-data`.
- Heartbeat: each cycle. If `last_heartbeat_ts` is older than 600s the bot
  is considered unhealthy.
- The mission control dashboard distinguishes `HEALTHY` / `STARTING` /
  `NOT RESPONDING` / `STOPPED` based on (process alive?, heartbeat age,
  process uptime vs heartbeat age).
- Daily prune runs on day rollover and trims rows older than 30 days.

## When you start a session

1. Read this file (you're doing it).
2. `make test` — establish current state. 551 tests at time of writing.
3. Look at mission control (`100.127.143.106:8002`) for the bot's live status.
4. Check `data/agent_roadmap.json` for any in-flight work.
5. Don't restart the bot, don't edit launchd plists, don't touch `artifacts/`.
