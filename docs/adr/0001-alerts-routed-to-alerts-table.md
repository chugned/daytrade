# ADR-0001: Route alerts to the `alerts` table, not `errors`

- **Status:** Accepted
- **Date:** 2026-06-02
- **Repos affected:** daytrade, nighttrade

## Context

The dashboard's `errors_last_24h` counter was showing 111 errors on daytrade
and 217 on nighttrade. Inspection showed 100% of them were not code errors:

- 93 × `VETUSDT is illiquid — excluded from paper trading` (market observation)
- 18 × `Model accuracy collapsed to 36%` (bot self-reporting its learning state)
- 216 × `no live data for BK` (yfinance data gap — `ValueError` raised in
  `live_feed.price_at`, caught in observer cycle)

The cause: `AlertManager.emit()` was calling `db.insert_error("alert:<kind>",
message)` to persist alerts. `db.insert_alert(level, kind, message)` already
existed but was unused. Every alert therefore inflated the error count.

## Decision

`AlertManager.emit()` now calls `db.insert_alert()` (with a fallback to
`insert_error` on older schemas without `insert_alert`).

The `errors` table is now reserved for actual code-level errors:
unhandled exceptions in the cycle loop, broker failures, DB write
failures.

## Consequences

**Positive**
- The dashboard's "errors in the last 24 hours" banner now means
  "something is genuinely broken," not "the bot is observing a known
  market condition."
- The alerts table remains the audit trail of every threshold-trip; no
  information is lost — only re-routed.

**Negative**
- One-time cleanup needed: 1,362 legacy mis-routed rows were deleted
  from daytrade's `errors` table and 1,301 from nighttrade's. Future
  rows can never be mis-routed because `AlertManager.emit` now picks
  the right table.

## Implementation

- `src/.../observatory/alerts.py:emit` — uses `insert_alert` when present.
- `src/.../observatory/database.py:recent_errors` — see ADR-0003 for the
  default-filter that complements this fix.
- `src/daytrade/mission_control/app.py:db_summary` — `errors_last_24h`
  SQL also filters `context NOT LIKE 'alert:%'` belt+suspenders.

## Verification

Manual: query `errors` table for `WHERE context LIKE 'alert:%' AND ts >=
datetime('now','-1 day')` should return zero new rows after this change.
