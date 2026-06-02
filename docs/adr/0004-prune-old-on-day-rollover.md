# ADR-0004: `db.prune_old(days=30)` + day-rollover hook

- **Status:** Accepted
- **Date:** 2026-06-02
- **Repos affected:** daytrade, nighttrade

## Context

The observatory DB had no eviction. Three tables grew without bound:

| Table | Daytrade (11 days) | Nighttrade (15 days) |
| --- | --- | --- |
| `activity_events` | 195k | 720k |
| `market_snapshots` | 173k | 712k |
| `symbol_health` | 173k | 1,397k |

At nighttrade's growth rate (~16 MB/day, ~50k rows/day across the three
tables) the DB would exceed several GB within a year, slow down every
`recent_*` query, and inflate WAL checkpoint times.

These three tables are pure observational logs. They have no
historical-record value beyond ~30 days — daily metrics and aggregates
are computed and persisted to `daily_metrics` long before the row-
level rows could be useful.

## Decision

Add `ObservatoryDB.prune_old(days=30) → Dict[str, int]` that:

1. Deletes rows from `activity_events`, `market_snapshots`, and
   `symbol_health` whose `ts < datetime('now', '-N days')`.
2. Tolerates missing tables (older schemas without one or more of
   these tables don't break).
3. Runs `PRAGMA wal_checkpoint(TRUNCATE)` and `PRAGMA optimize` to
   reclaim WAL space and refresh query plans.
4. Returns a dict of `{table: rows_deleted}` for logging.

The observer's `_day_rollover` calls `prune_old` after the daily
report is written. Failure is best-effort (logged, doesn't crash
the loop).

## Consequences

**Positive**
- DB stays bounded at ~30 days of observation rows × ~50k rows/day =
  ~1.5M rows total per repo, comfortably manageable in SQLite.
- WAL stays small (checkpoint truncates it).
- `recent_*` queries stay fast.

**Negative**
- 30 days of detail is gone permanently. If a forensic question
  requires older row-level detail, it's not recoverable.
  Mitigation: daily aggregates are still in `daily_metrics` and the
  raw log files (`logs/daytrade.log`) keep their own rotated history.

## Implementation

- `src/.../observatory/database.py:prune_old` — the method itself.
- `src/.../observatory/observer.py:_day_rollover` — calls `prune_old`
  after writing the daily report.

## Verification

- `tests/test_qa_high_misc.py::test_prune_old_drops_aged_activity_rows`
  — inserts an old + recent row, confirms only the old is deleted.
- `tests/test_qa_high_misc.py::test_prune_old_handles_missing_tables`
  — confirms the call doesn't raise on older schemas.

One-time backlog cleanup on both DBs was done manually after this fix
landed (deleted 232,837 rows on daytrade, 2,140,992 on nighttrade).
