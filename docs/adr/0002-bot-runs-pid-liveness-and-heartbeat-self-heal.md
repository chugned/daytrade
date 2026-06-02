# ADR-0002: `bot_runs` PID-liveness check + heartbeat self-heal

- **Status:** Accepted
- **Date:** 2026-06-02
- **Repos affected:** daytrade, nighttrade

## Context

Two related bugs took the dashboard's "BOT OFFLINE — crashed" banner
into a state that didn't reflect reality:

1. **`mark_dangling_runs_crashed` clobbered live siblings.** The old
   implementation marked every `status='running'` row as crashed at
   startup, regardless of whether the row's PID was actually alive.
   When the user spawned a sibling daytrade command (e.g. a 10-second
   `daytrade shadow` smoke test), the sibling's startup wrongly marked
   the still-running long-lived bot's row as crashed.

2. **`heartbeat()` could not self-heal.** Once a row was marked
   `crashed`, the live bot kept calling `heartbeat()` every cycle, but
   `heartbeat` only updated `last_heartbeat_ts` and `cycles` — never
   `status`. So the dashboard reported "BOT OFFLINE" forever even
   though the process was alive and heartbeating.

## Decision

**`mark_dangling_runs_crashed`** now liveness-probes each `running`
row's PID with `os.kill(pid, 0)`. Only rows whose PID raises
`ProcessLookupError` are marked crashed. PIDs we don't have permission
to signal (`PermissionError`) are treated as alive — we don't have
authority to declare them dead.

**`heartbeat()`** now also restores `status='running'` and clears
`stopped_ts`. A heartbeat is, by definition, "I am alive" — so it
must be authoritative.

## Consequences

**Positive**
- A sibling bot, smoke test, CLI tool, or transient process can no
  longer falsely declare the main observer dead.
- A spuriously-crashed row self-heals on the next heartbeat cycle (~5
  min default), so the dashboard recovers automatically rather than
  requiring a manual DB UPDATE.

**Negative**
- PID reuse on POSIX is theoretically possible: if the original
  process died and the OS reused its PID for an unrelated process,
  the liveness check would still see "alive" and miss the death.
  Mitigation: combined with the heartbeat-age check on the dashboard
  side, a truly-dead bot is detected within 600s anyway.

## Implementation

- `src/.../observatory/database.py:mark_dangling_runs_crashed` —
  PID-liveness probe.
- `src/.../observatory/database.py:heartbeat` — also writes
  `status='running'`, `stopped_ts=NULL`.
- `src/.../observatory/database.py:current_bot_run` — prefers
  `status='running'` rows whose PID is alive (so a short-lived sibling
  test process can't push the real bot off the dashboard).

## Verification

- `tests/test_observatory.py::test_db_recovers_dangling_runs` — uses a
  guaranteed-dead PID (>4_000_000); confirms it gets marked crashed.
- `tests/test_observatory.py::test_db_keeps_alive_runs_running` —
  uses `os.getpid()`; confirms it is NOT marked crashed.
- `tests/test_observatory.py::test_heartbeat_resurrects_spuriously_crashed_row` —
  flips a row to `crashed`, calls `heartbeat`, confirms `status='running'` again.
