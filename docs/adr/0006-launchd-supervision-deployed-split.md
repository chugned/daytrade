# ADR-0006: launchd supervision + dev/deployed split for daytrade

**Status:** Accepted
**Date:** 2026-06-03

## Context

Daytrade holds the (paper) capital and is the bot the user cares most
about — yet it was the *least* reliable thing on the host. It ran as a
bare foreground process:

```
PYTHONPATH=src python3 -m daytrade learn --days 30 --interval 60 --real-data
```

with **no supervisor**. On 2026-06-02 it received SIGTERM (its parent
shell closed) and shut down cleanly — and nothing restarted it. It sat
dead for ~80 minutes until noticed. This is the classic failure mode of
an unsupervised long-running process: the moment its parent shell exits,
it dies and stays dead. Meanwhile nighttrade — supervised by launchd +
a watchdog — had been bulletproof. The capital-bearing bot had the
weaker setup.

The user's mandate: **"This should never happen again."**

## The macOS TCC constraint (why the naive fix fails)

The obvious fix — a launchd LaunchAgent with `KeepAlive` — does **not**
work while the code lives under `~/Desktop`. macOS TCC (privacy
protection) refuses launchd-spawned processes access to the Desktop
folder:

```
last exit code = 126
getcwd: cannot access parent directories: Operation not permitted
/bin/bash: .../Desktop/coding/daytrade/deploy/_svc-run.sh: Operation not permitted
```

An interactive Terminal has Desktop access (granted to Terminal.app);
launchd does not. This is the same reason nighttrade runs from
`~/nighttrade` and not `~/Desktop/coding/nighttrade`. The dev/deployed
split is therefore **mandatory**, not stylistic, for launchd
supervision.

## Decision

Give daytrade the same architecture nighttrade already has.

1. **Dev/deployed split.**
   - `~/Desktop/coding/daytrade` — dev (edit here).
   - `~/daytrade` — deployed (launchd runs here; owns the live state).
   - `deploy/sync.sh` rsyncs code (excludes `artifacts/`, `data/`,
     `logs/` so the deployed state is never clobbered).
   - One-time state migration on first install: the live 193 MB
     `observatory.db` + `meta_model.pkl` + `model.pkl` were copied
     dev → deployed after a clean WAL checkpoint, fingerprint-verified
     (412 paper_trades / 203,874 predictions / 9 runs preserved).

2. **Four launchd services** (`deploy/install.sh`):
   - `com.daytrade.learn` — the observer. **python runs directly**, NOT
     wrapped in caffeinate (see "caffeinate decoupling" below).
   - `com.daytrade.dashboard` — FastAPI on 0.0.0.0:8000.
   - `com.daytrade.caffeinate` — a standalone `caffeinate -s` so the Mac
     never system-sleeps (crypto is 24/7); decoupled so it can't entangle
     the bot's process tree.
   - `com.daytrade.watchdog` — `StartInterval=300`; every 5 min runs
     `deploy/watchdog.sh`, which `pgrep`s the learn process and the
     dashboard port and `launchctl kickstart`s anything that's down.

3. **Env-pinning wrapper** (`deploy/_svc-run.sh`, generated per-machine,
   never synced): exports `HOME`, absolute `PYTHONPATH=$REPO/src`, and a
   sane `PATH`, then `exec "$@"`. launchd does not pass these reliably;
   the old static plists used bare `/usr/bin/python3` + relative
   `PYTHONPATH=src` and were never loadable.

## Two failure modes found while building this (and their fixes)

- **caffeinate decoupling.** Running the job as `caffeinate -s python …`
  makes caffeinate the parent and python a child. When python dies,
  launchd's job-completion detection + `KeepAlive` get confused by the
  lingering caffeinate and respawn unreliably. Fix: python runs directly
  under the job (launchd tracks the real process), and `caffeinate -s`
  is its own KeepAlive service.

- **RunAtLoad is unreliable in the `gui/` domain.** Bootstrapped jobs sat
  at `runs=0` until explicitly kickstarted. Fix: `install.sh` kickstarts
  the long-running services after bootstrap, and the watchdog
  (RunAtLoad + 5-min interval) is a second path to start them after a
  reboot/login.

## Recovery guarantees ("never again")

Defence in depth, worst-case downtime ~5 minutes:

1. **KeepAlive** — respawns on crash within seconds (when not in
   launchd crash-loop backoff).
2. **Watchdog every 5 min** — `launchctl kickstart` **bypasses backoff**
   (verified), so even a backed-off job is recovered within one tick.
   Proven end-to-end: killing the learn process and running the watchdog
   brings it back.
3. **Reboot** — launchd loads the agents on login; the watchdog's
   RunAtLoad + interval guarantees startup even if RunAtLoad on the main
   jobs doesn't fire.

Note on testing: rapid repeated `kill` of the job (as during this
build-out) triggers launchd's exponential crash-loop backoff, which can
delay `KeepAlive` restarts by minutes. This is a test artifact, not a
production condition — real crashes are isolated, and the watchdog
covers the backed-off window regardless.

## Consequences

- Daytrade now survives shell-close, crash, and reboot. The original
  incident cannot recur silently.
- A code change now requires `deploy/sync.sh` to take effect live (same
  workflow as nighttrade). Editing only the dev tree no longer affects
  the running bot — a feature (controlled deploys), but a gotcha if
  forgotten.
- Mission control (Project Polaris) reads daytrade from `~/daytrade`.
- The Mac stays awake 24/7 (caffeinate). Acceptable for a 24/7 crypto
  bot; the display still sleeps. If overheating becomes an issue, the
  caffeinate service can be unloaded independently.

## References

- `deploy/install.sh`, `deploy/watchdog.sh`, `deploy/sync.sh`,
  `deploy/uninstall.sh`.
- `tests/test_deploy_supervision.py` — pins no-CHANGE_ME, env-pinning,
  caffeinate-decoupled, kickstart-after-bootstrap, watchdog checks,
  all-four-services, stale static plists removed.
- nighttrade ADR (two-directory split) — the prior art this mirrors.
- CLAUDE.md "Two-directory split" section (now covers both bots).
