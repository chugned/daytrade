# ADR-0003: `recent_errors()` filters `alert:*` rows by default

- **Status:** Accepted
- **Date:** 2026-06-02
- **Repos affected:** daytrade, nighttrade

## Context

ADR-0001 fixed the source — `AlertManager` no longer writes to the
`errors` table. But legacy rows from before the fix still polluted
every read path that queried `errors`. The dashboard, mission control,
and any future reporting tool would continue to see thousands of
`alert:*` rows for the rest of the rolling window.

A simple one-time `DELETE` cleared the backlog (1,362 rows on
daytrade, 1,301 on nighttrade), but that's a one-shot. The longer-
term fix is to make the read API correct by default.

## Decision

`db.recent_errors(limit, include_alerts=False)` filters
`context NOT LIKE 'alert:%'` by default. Pass `include_alerts=True`
to get the legacy behaviour (e.g. for forensic queries that want to
see *everything* the bot has ever logged).

The default is `False` because:
- All known production callers want real errors, not alerts.
- The dashboard's "errors_last_24h" badge must show only actionable
  failures.
- Backwards-compat is preserved via the explicit opt-in flag.

## Consequences

**Positive**
- Callers don't have to remember to add `WHERE context NOT LIKE
  'alert:%'` to every query.
- The default behaviour matches the dashboard's semantic.

**Negative**
- Any caller that *did* depend on seeing `alert:*` rows in
  `recent_errors` is silently affected. We searched the codebase for
  such callers and found none — but if a future caller relies on
  legacy behaviour, they must pass `include_alerts=True` explicitly.

## Implementation

- `src/.../observatory/database.py:recent_errors` — new `include_alerts`
  parameter, default `False`.
- `src/daytrade/mission_control/app.py:db_summary` — `errors_last_24h`
  SQL also filters `alert:*` (defence in depth — even if a caller
  bypassed `recent_errors`).

## Verification

Manual: `db.recent_errors(limit=100)` over a DB that contains both
real errors and `alert:*` rows should return only real errors.
`db.recent_errors(limit=100, include_alerts=True)` should return both.
