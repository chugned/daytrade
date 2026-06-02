# ADR-0005: Retry-with-backoff for SQLite `database is locked` on writes

- **Status:** Accepted
- **Date:** 2026-06-02
- **Repo:** daytrade
- **Symmetric with:** nighttrade ADR-0006

## Context

On 2026-06-02 the sibling repo (nighttrade) hit
`sqlite3.OperationalError: database is locked` during a write —
single symbol dropped, cycle continued, but a write that should have
succeeded was lost. Root cause: WAL + `timeout=10s` on connect
absorbs most contention, but residual cases (concurrent
`PRAGMA wal_checkpoint(TRUNCATE)` from the daily prune; long-held
read snapshots from the dashboard) still surface to Python.

Daytrade has the same `ObservatoryDB` shape and the same write
surface. The bug has **not** been observed on daytrade (its 60s
cycle gives a narrower collision window with the prune job than
nighttrade's 300s), but the protection is cheap and the failure
mode would be identical.

## Decision

Port the `_RetryingConnection` proxy from nighttrade verbatim:

- Transparent wrapper over `sqlite3.Connection` — same shape, same
  attribute pass-through.
- Intercepts `execute` and `commit`.
- On `OperationalError` containing `locked` or `busy`, sleeps
  `base_delay` (0.05s), retries, doubles the delay each time up to
  `max_delay` (2.0s).
- Re-raises after `max_retries` (5) so the outer error path still
  runs on genuine pathology.
- Other `OperationalError`s re-raise immediately — silent retry on
  syntax / schema bugs would mask real problems.

Also bumped connection `timeout=10s` → `timeout=30s` for additional
headroom at the VFS layer.

## Why not just bump the timeout further?

Bumping `timeout` to 60+ seconds defends the same scenarios but
blocks the entire cycle on one stuck write. The retry proxy gives
up after ~6s worst-case AND yields control between retries
(`time.sleep`), so a SIGTERM during the retry window is processed
promptly.

## Consequences

**Positive**
- Transient lock contention (the common case) is invisible to
  callers — no more single-symbol drops on prune-job timing.
- Real lock pathology (stuck writer) still surfaces, just after a
  ~6s retry window instead of immediately.
- Zero call-site changes — every existing `self._conn.execute(...)`
  and `self._conn.commit()` benefits transparently.
- Symmetric with nighttrade: same proxy, same defaults, same tests.

**Negative**
- One extra layer of indirection in stack traces.
- A single slow write can take up to ~6s under contention.
  Acceptable: cycle interval is 60s, observer is single-threaded
  per cycle.

## Implementation

- `src/daytrade/observatory/database.py`:
  - `_is_locked_error(exc)` helper.
  - `_RetryingConnection` proxy class.
  - `ObservatoryDB.__init__` wraps the raw connection.
  - `timeout=10.0` → `timeout=30.0`.

## Verification

- `tests/test_db_retry_on_locked.py` — 8 unit + integration tests
  ported from nighttrade (same contract):
  - retries on `database is locked`,
  - retries on `database is busy`,
  - does NOT retry on unrelated `OperationalError`,
  - exhausts retries and re-raises,
  - backoff genuinely sleeps,
  - passthrough for non-wrapped attributes,
  - end-to-end against a real `ObservatoryDB` with concurrent lock.
- Full daytrade test suite passes (run prior to merge).
