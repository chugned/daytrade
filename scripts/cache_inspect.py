#!/usr/bin/env python3
"""Show what's in the local historical-market-data cache.

Lists every (symbol, interval) pair stored in ``data/market_history.db``
with the time span covered and the row count. Useful for:

- noticing that a sweep is re-downloading data you already have,
- spotting time gaps that would invalidate a backtest,
- estimating disk usage before pulling more history.

Read-only — never deletes or modifies anything. Paper-research aid.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from daytrade.research.history import HISTORY_DB_PATH, HistoryStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(HISTORY_DB_PATH),
                        help="Path to the cache SQLite file")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Cache does not exist yet: {db_path}")
        return 0

    store = HistoryStore(db_path)
    try:
        rows = store._conn.execute(
            "SELECT symbol, interval, MIN(open_time), MAX(open_time), "
            "COUNT(*) FROM klines GROUP BY symbol, interval "
            "ORDER BY symbol, interval"
        ).fetchall()
    finally:
        store.close()

    size_mb = db_path.stat().st_size / (1024 * 1024)
    print(f"Cache: {db_path} ({size_mb:.2f} MB)")
    if not rows:
        print("  (empty)")
        return 0

    print(f"  {'symbol':<10}  {'iv':<4}  {'from':<19}  {'to':<19}  "
          f"{'bars':>8}  {'span':>9}")
    print("  " + "-" * 75)
    for sym, iv, t_lo, t_hi, n in rows:
        lo = datetime.fromtimestamp(t_lo / 1000, tz=timezone.utc)
        hi = datetime.fromtimestamp(t_hi / 1000, tz=timezone.utc)
        span_h = (t_hi - t_lo) / 3_600_000
        span_label = (f"{span_h / 24:>5.1f}d"
                      if span_h >= 48 else f"{span_h:>5.1f}h")
        print(f"  {sym:<10}  {iv:<4}  {lo:%Y-%m-%d %H:%M}  "
              f"{hi:%Y-%m-%d %H:%M}  {n:>8d}  {span_label:>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
