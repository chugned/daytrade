# `Secure` branch — production-readiness engineering

This branch adds the engineering primitives needed before any responsible
real-money deployment, **without** enabling real trading. Every commit is
testable, paper/simulation-safe, and orthogonal to the strategy itself.

`main` keeps the live paper bot untouched. Merge `Secure` only after the
strategy evidence threshold (500+ trades, multiple regimes, positive
walk-forward) is met **and** the freqtrade port (separate sprint) is ready.

## What's inside

The new `daytrade.ops` package collects the primitives:

| Module | Purpose |
|---|---|
| `ops.instance_lock` | PID-file `SingleInstanceLock` — one process per name, period |
| `ops.order_ids` | Deterministic `generate_client_order_id()` + duplicate-rejecting `OrderIDRegistry` |
| `ops.reconciliation` | `reconcile_paper_state()` — refuse to act if local DB diverges from source of truth |
| `ops.notify` | `Notifier` interface + Telegram / ntfy / log backends, env-driven |
| `ops.api_keys` | `inspect_key()` + `assert_trade_only()` — refuse any key with withdrawal permission |
| `ops.remote_log` | Off-host JSON forwarding + daily-rotating local files |

Supporting changes elsewhere:

- `config.schema` — `RuntimeConfig.max_data_age_seconds` for the staleness guard
- `observatory.observer` — staleness guard, startup reconciliation, notifier hooks
- `cli.main` — single-instance lock wraps `learn`, `observe`, `dashboard`
- `deploy/launchd/` — macOS LaunchAgents (keep-alive, throttled)
- `deploy/systemd/` — Linux units (hardened, journal logging)
- `deploy/provision-vps.sh` — idempotent VPS bootstrap (Debian/Ubuntu)

## The ten items — mapped

| # | Item | Files | Tests |
|---|---|---|---|
| 1 | Single-instance lock | `ops/instance_lock.py`, CLI hooks | `tests/test_instance_lock.py` |
| 2 | Watchdog supervisors | `deploy/launchd/*`, `deploy/systemd/*`, `deploy/README.md` | — (system-level) |
| 3 | Kill-switch tests | — (verifies existing risk engine) | `tests/test_kill_switches.py` |
| 4 | Staleness guard | `config.schema`, `observatory.observer` | `tests/test_staleness_guard.py` |
| 5 | Idempotent order IDs | `ops/order_ids.py` | `tests/test_order_ids.py` |
| 6 | Startup reconciliation | `ops/reconciliation.py`, `observatory.observer.start()` | `tests/test_reconciliation.py` |
| 7 | Push notifications | `ops/notify.py`, observer hooks | `tests/test_notify.py` |
| 8 | Trade-only API key validator | `ops/api_keys.py` | `tests/test_api_keys.py` |
| 9 | VPS provisioning script | `deploy/provision-vps.sh` | (shellcheck-ed manually) |
| 10 | Remote logging | `ops/remote_log.py` | `tests/test_remote_log.py` |

358 tests pass across the full suite.

## What is *not* in this branch

The two items that intentionally remain follow-ups, each its own sprint:

- **#11 — freqtrade strategy port.** Re-implementing the 4 gates and the
  meta-labelling model as a `freqtrade.strategy.IStrategy`. 6–8 hours of
  focused work. Will live on its own branch.
- **#12 — freqtrade backtest cross-validation.** Depends on #11.

## Activation checklist (when ready, NOT NOW)

Stages, in order — each gated on the previous:

1. Strategy evidence threshold met on `main` (500+ trades, positive
   walk-forward across regimes).
2. This `Secure` branch reviewed and merged to `main`.
3. Freqtrade port (#11) built on its own branch + merged.
4. VPS provisioned via `deploy/provision-vps.sh`.
5. Trade-only API keys (`ops.api_keys.assert_trade_only`) verified.
6. Freqtrade dry-run on real Binance for 4+ weeks; tracks daytrade paper.
7. Live with trivially small capital (€100–200), kill switches armed,
   notifier active, watchdog on.

Anything that skips a stage is rushing, not engineering.
