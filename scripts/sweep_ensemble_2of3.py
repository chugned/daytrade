#!/usr/bin/env python3
"""Empirically test whether the 3 strongest signals (funding, volume
z-score green spike, cross-asset divergence) stack into a higher-
precision ensemble.

Each signal on its own clears or nearly clears the 25 bp round-trip
cost in its strongest variant. The question this sweep answers:
**when 2 or more fire on the same bar, do the lifts compound?**

We define three boolean conditions per (alt, 1h-bar):

  C1_funding  := most-recent funding rate for the alt is ≤ +0.0001
                 (the avoidance gate from signal #1)
  C2_volspike := the last 1h bar had volume z ≥ +4 AND green close
                 (the momentum cell from signal #2)
  C4_divbuy   := over the last 12h, BTC moved >+0.5% AND the alt
                 was within ±1pp of BTC (signal #4 booster cell);
                 OR BTC moved <-0.5% AND alt over-fell by >1pp
                 (signal #4 contrarian cell)

Each is a BUY-favorable condition (i.e., evidence in support of a
long entry). We bucket forward 24h returns by *count of favorable
signals* (0, 1, 2, 3). If the signals are sufficiently orthogonal,
the 2-of-3 and 3-of-3 cells should show monotonically higher lifts
and acceptable sample counts.

Usage::

    PYTHONPATH=src python3 scripts/sweep_ensemble_2of3.py \\
        --alts ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT --days 500

Read-only.
"""

from __future__ import annotations

import argparse
import sys
import time
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

import httpx  # noqa: E402
import numpy as np  # noqa: E402

from daytrade.observatory.funding import fetch_funding_history  # noqa: E402
from daytrade.research.history import download_history  # noqa: E402


_BASE = "https://data-api.binance.vision"
_INTERVAL_MS = 3_600_000  # 1h


def _fetch_full_klines(symbol: str, days: int):
    """Pull raw 12-field klines so we can read field [9] taker-buy-base."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    cursor = start_ms
    out: List[list] = []
    while cursor < now_ms:
        with httpx.Client(timeout=15.0) as c:
            resp = c.get(_BASE + "/api/v3/klines",
                         params={"symbol": symbol, "interval": "1h",
                                 "startTime": cursor, "endTime": now_ms,
                                 "limit": 1000})
            resp.raise_for_status()
            rows = resp.json()
        if not rows:
            break
        out.extend(rows)
        cursor = int(rows[-1][0]) + _INTERVAL_MS
        if len(rows) < 1000:
            break
        time.sleep(0.12)
    return out


def _load_symbol(symbol: str, days: int):
    """Return aligned arrays for one symbol:
        ts_ms[], open[], close[], volume[], taker_buy_base[]
    """
    raw = _fetch_full_klines(symbol, days)
    if not raw:
        return None
    ts = np.array([int(r[0]) for r in raw], dtype=np.int64)
    op = np.array([float(r[1]) for r in raw], dtype=float)
    cl = np.array([float(r[4]) for r in raw], dtype=float)
    vol = np.array([float(r[5]) for r in raw], dtype=float)
    tbb = np.array([float(r[9]) for r in raw], dtype=float)
    return {
        "ts": ts, "open": op, "close": cl, "volume": vol, "tbb": tbb,
    }


def _rolling_z(values: np.ndarray, window: int) -> np.ndarray:
    n = len(values)
    z = np.full(n, np.nan)
    for i in range(window, n):
        w = values[i - window:i]
        mu = w.mean()
        sd = w.std(ddof=1)
        if sd > 0:
            z[i] = (values[i] - mu) / sd
    return z


def _trailing_return(closes: np.ndarray, lookback: int) -> np.ndarray:
    n = len(closes)
    out = np.full(n, np.nan)
    for i in range(lookback, n):
        if closes[i - lookback] > 0:
            out[i] = (closes[i] / closes[i - lookback] - 1.0) * 100
    return out


def _forward_return(closes: np.ndarray, h: int) -> np.ndarray:
    n = len(closes)
    out = np.full(n, np.nan)
    for i in range(n - h):
        if closes[i] > 0:
            out[i] = (closes[i + h] / closes[i] - 1.0) * 100
    return out


def _funding_aligned(symbol: str, ts_ms: np.ndarray) -> np.ndarray:
    """For each bar in ts_ms, return the most-recent funding rate
    available *at or before* that bar's open."""
    print(f"  fetching funding history for {symbol}...", flush=True)
    hist = fetch_funding_history(symbol, limit=1000)
    if not hist:
        return np.full(len(ts_ms), np.nan)
    fund_ts = np.array(sorted(snap.timestamp_ms for snap in hist), dtype=np.int64)
    fund_rate_by_ts = {snap.timestamp_ms: snap.rate for snap in hist}
    out = np.full(len(ts_ms), np.nan)
    for i, t in enumerate(ts_ms):
        idx = bisect_left(fund_ts, int(t) + 1) - 1
        if idx >= 0:
            out[i] = fund_rate_by_ts[int(fund_ts[idx])]
    return out


def _summary(arr):
    mask = ~np.isnan(arr)
    if not mask.any():
        return 0, float("nan"), float("nan")
    a = arr[mask]
    return int(a.size), float(a.mean()), float(a.std(ddof=1) if a.size > 1 else 0.0)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--alts", default="ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT")
    p.add_argument("--anchor", default="BTCUSDT")
    p.add_argument("--days", type=int, default=500)
    p.add_argument("--fwd-h", type=int, default=24)
    p.add_argument("--report-dir", default="reports")
    args = p.parse_args()

    print(f"[ensemble] loading anchor {args.anchor}...", flush=True)
    anchor_kl = download_history(args.anchor, interval="1h", days=args.days)
    a_ts = np.array([int(k.timestamp.timestamp() * 1000) for k in anchor_kl])
    a_cl = np.array([k.close for k in anchor_kl], dtype=float)

    pooled_c1 = []
    pooled_c2 = []
    pooled_c4 = []
    pooled_fwd = []

    alts = [s.strip().upper() for s in args.alts.split(",") if s.strip()]

    for alt in alts:
        print(f"[ensemble] {alt}: loading klines + funding...", flush=True)
        data = _load_symbol(alt, args.days)
        if data is None:
            continue
        ts = data["ts"]
        op = data["open"]
        cl = data["close"]
        vol = data["volume"]
        tbb = data["tbb"]

        # Align BTC closes to the alt's timestamps (intersection only).
        a_idx = {int(t): i for i, t in enumerate(a_ts)}
        alt_indices = []
        btc_aligned = []
        for i, t in enumerate(ts):
            j = a_idx.get(int(t))
            if j is not None:
                alt_indices.append(i)
                btc_aligned.append(a_cl[j])
        if not alt_indices:
            continue
        sel = np.array(alt_indices, dtype=int)
        ts_a = ts[sel]
        op_a = op[sel]
        cl_a = cl[sel]
        vol_a = vol[sel]
        btc_cl = np.array(btc_aligned, dtype=float)

        # Signal #1: funding ≤ +0.0001 (i.e., not extreme positive)
        funding = _funding_aligned(alt, ts_a)
        c1 = funding <= 0.0001

        # Signal #2: green volume z ≥ +4 (last bar)
        vol_z = _rolling_z(vol_a, window=20)
        green = cl_a > op_a
        c2 = (vol_z >= 4.0) & green

        # Signal #4: BTC-up & alt-matched, OR BTC-dn & alt-over-fell (12h lookback)
        btc_ret_12 = _trailing_return(btc_cl, 12)
        alt_ret_12 = _trailing_return(cl_a, 12)
        divergence_12 = alt_ret_12 - btc_ret_12
        c4 = (
            ((btc_ret_12 > 0.5) & (np.abs(divergence_12) <= 1.0))
            | ((btc_ret_12 < -0.5) & (divergence_12 < -1.0))
        )

        fwd = _forward_return(cl_a, args.fwd_h)

        # Filter to bars where all three conditions are computable.
        valid = ~np.isnan(funding) & ~np.isnan(vol_z) & ~np.isnan(btc_ret_12) & ~np.isnan(fwd)
        pooled_c1.append(c1[valid].astype(int))
        pooled_c2.append(c2[valid].astype(int))
        pooled_c4.append(c4[valid].astype(int))
        pooled_fwd.append(fwd[valid])

    c1 = np.concatenate(pooled_c1)
    c2 = np.concatenate(pooled_c2)
    c4 = np.concatenate(pooled_c4)
    fwd = np.concatenate(pooled_fwd)

    out_dir = _REPO / args.report_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_label = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = out_dir / f"ensemble_2of3_sweep_{ts_label}.md"

    md: List[str] = []
    md.append(f"# 2-of-3 ensemble sweep — {ts_label}")
    md.append("")
    md.append(f"Alts: `{','.join(alts)}` · Anchor: `{args.anchor}` · "
              f"History: {args.days}d · Forward horizon: {args.fwd_h}h")
    md.append("")
    md.append("**Conditions** (all BUY-favorable):")
    md.append("- **C1 funding ok**: most-recent funding rate ≤ +0.0001 (not extreme positive)")
    md.append("- **C2 vol spike**: last bar had volume z≥+4 AND closed green")
    md.append("- **C4 divergence buy**: 12h BTC up & alt matched, OR 12h BTC down & alt over-fell")
    md.append("")
    md.append("## Single-condition baselines (BUY-favorable on this signal alone)")
    md.append("")

    n_total, mean_total, _ = _summary(fwd)
    md.append(f"baseline (all bars, n={n_total:,}): mean fwd {args.fwd_h}h = {mean_total:+.3f}%")
    md.append("")

    for name, cond in [("C1 funding ok", c1==1),
                       ("C2 vol spike", c2==1),
                       ("C4 divergence buy", c4==1)]:
        n, mu, _ = _summary(fwd[cond])
        lift = mu - mean_total
        fire = 100 * n / n_total
        md.append(f"- **{name}**: n={n:,} ({fire:.1f}% fire), "
                  f"mean fwd {mu:+.3f}%, lift {lift:+.3f} pp")
    md.append("")

    md.append("## Joint cells")
    md.append("")
    md.append("| C1 | C2 | C4 | n | fire % | mean fwd | lift vs base |")
    md.append("|:--:|:--:|:--:|--:|------:|---------:|-------------:|")
    for v1 in (0, 1):
        for v2 in (0, 1):
            for v4 in (0, 1):
                cond = (c1 == v1) & (c2 == v2) & (c4 == v4)
                n, mu, _ = _summary(fwd[cond])
                lift = mu - mean_total if n > 0 else float("nan")
                fire = 100 * n / n_total if n > 0 else 0.0
                sym1 = "✓" if v1 else "✗"
                sym2 = "✓" if v2 else "✗"
                sym4 = "✓" if v4 else "✗"
                md.append(f"| {sym1} | {sym2} | {sym4} | {n:5d} | "
                          f"{fire:5.2f} | {mu:+8.3f}% | {lift:+8.3f} pp |")
    md.append("")

    md.append("## Count-based summary (k-of-3)")
    md.append("")
    md.append("| k favorable | n | fire % | mean fwd | lift vs base |")
    md.append("|------------:|--:|------:|---------:|-------------:|")
    score = c1 + c2 + c4
    for k in (0, 1, 2, 3):
        cond = score == k
        n, mu, _ = _summary(fwd[cond])
        lift = mu - mean_total if n > 0 else float("nan")
        fire = 100 * n / n_total if n > 0 else 0.0
        md.append(f"| {k} | {n:5d} | {fire:5.2f} | {mu:+8.3f}% | {lift:+8.3f} pp |")
    md.append("")

    report.write_text("\n".join(md))
    print(f"[ensemble] Wrote {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
