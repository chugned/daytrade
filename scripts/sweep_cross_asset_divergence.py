#!/usr/bin/env python3
"""Empirically validate the cross-asset divergence signal.

Hypothesis: BTC is the regime-leader for crypto. When an altcoin's
return over the last K hours diverges materially from BTC's return
over the same window, the alt tends to revert toward its BTC-expected
return on the K-forward window. Specifically:

    "alt lagging" — BTC ran up but the alt didn't → alt should catch
                    up (BUY signal)
    "alt over-running" — BTC flat but alt ran up → alt should fade
                    (avoid BUY / consider SHORT)
    "alt early dive" — alt fell while BTC held → alt may bounce back
                    (potential dip buy)

We do not pre-assume a beta value; instead we compute the simple
difference (alt_return - btc_return) over the lookback, then bucket
forward returns. If the signal is real, the most extreme negative
divergence cells (alt-lagging) should show positive forward returns
above baseline.

Lookbacks tested: 4h, 12h, 24h.
Forward horizons: 4h, 24h.

Usage::

    PYTHONPATH=src python3 scripts/sweep_cross_asset_divergence.py \\
        --alts ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT --days 500

Read-only — uses the existing Binance public-kline cache.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

import numpy as np  # noqa: E402

from daytrade.research.history import download_history  # noqa: E402


def _load_closes(symbol: str, days: int):
    klines = download_history(symbol, interval="1h", days=days)
    if not klines:
        return None
    ts = np.array([int(k.timestamp.timestamp() * 1000) for k in klines])
    closes = np.array([k.close for k in klines], dtype=float)
    return ts, closes


def _align(ts_a, vals_a, ts_b, vals_b):
    """Return (vals_a_aligned, vals_b_aligned) — both indexed by the
    intersection of ts_a and ts_b, in time order."""
    common = sorted(set(ts_a) & set(ts_b))
    if not common:
        return None, None
    idx_a = {t: i for i, t in enumerate(ts_a)}
    idx_b = {t: i for i, t in enumerate(ts_b)}
    va = np.array([vals_a[idx_a[t]] for t in common], dtype=float)
    vb = np.array([vals_b[idx_b[t]] for t in common], dtype=float)
    return va, vb, np.array(common)


def _trailing_return(closes: np.ndarray, lookback: int) -> np.ndarray:
    """Trailing return: (close[i] / close[i - lookback] - 1) * 100"""
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


def _summary(arr):
    mask = ~np.isnan(arr)
    if not mask.any():
        return 0, float("nan"), float("nan")
    a = arr[mask]
    return int(a.size), float(a.mean()), float(a.std(ddof=1) if a.size > 1 else 0.0)


def bucket_by_divergence(divergence, btc_ret, fwd, label) -> str:
    lines = [
        f"### {label}",
        "",
        "| regime                                         |    n |   mean fwd | lift vs base |",
        "|------------------------------------------------|-----:|-----------:|-------------:|",
    ]
    base_n, base_mean, _ = _summary(fwd)
    lines.append(f"| baseline (all bars)                            | {base_n:4d} | {base_mean:+8.3f}% |          —   |")

    # Four scenario buckets, defined by joint signs of (BTC trailing return, divergence)
    btc_up_thr = 0.5    # BTC moved at least 0.5% over the lookback
    btc_down_thr = -0.5
    div_thr = 1.0       # alt diverged by at least 1pp from BTC over the lookback

    scenarios = [
        ("BTC up >+0.5% & alt LAGGED by >1pp",   (btc_ret >  btc_up_thr) & (divergence < -div_thr)),
        ("BTC up >+0.5% & alt MATCHED (±1pp)",   (btc_ret >  btc_up_thr) & (np.abs(divergence) <= div_thr)),
        ("BTC up >+0.5% & alt OUT-RAN by >1pp",  (btc_ret >  btc_up_thr) & (divergence >  div_thr)),
        ("BTC dn <-0.5% & alt LAGGED (fell less) by >1pp", (btc_ret < btc_down_thr) & (divergence >  div_thr)),
        ("BTC dn <-0.5% & alt MATCHED (±1pp)",   (btc_ret < btc_down_thr) & (np.abs(divergence) <= div_thr)),
        ("BTC dn <-0.5% & alt OVER-FELL by >1pp",(btc_ret < btc_down_thr) & (divergence < -div_thr)),
        ("BTC flat ±0.5% & alt up >+1pp",        (np.abs(btc_ret) <= btc_up_thr) & (divergence >  div_thr)),
        ("BTC flat ±0.5% & alt down >-1pp",      (np.abs(btc_ret) <= btc_up_thr) & (divergence < -div_thr)),
    ]
    for name, cond in scenarios:
        mask = cond & ~np.isnan(divergence) & ~np.isnan(btc_ret) & ~np.isnan(fwd)
        n, mu, _ = _summary(fwd[mask])
        if n == 0:
            continue
        lift = mu - base_mean
        lines.append(f"| {name:46s} | {n:4d} | {mu:+8.3f}% |    {lift:+7.3f} pp |")
    lines.append("")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--alts", default="ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT")
    p.add_argument("--anchor", default="BTCUSDT")
    p.add_argument("--days", type=int, default=500)
    p.add_argument("--lookbacks", default="4,12,24")
    p.add_argument("--fwd-hours", default="4,24")
    p.add_argument("--report-dir", default="reports")
    args = p.parse_args()

    alts = [s.strip().upper() for s in args.alts.split(",") if s.strip()]
    lookbacks = [int(x) for x in args.lookbacks.split(",")]
    horizons = [int(x) for x in args.fwd_hours.split(",")]

    print(f"[sweep] loading anchor {args.anchor}...")
    anchor_data = _load_closes(args.anchor, args.days)
    if anchor_data is None:
        print("[sweep] anchor missing"); return 1
    a_ts, a_closes = anchor_data

    out_dir = _REPO / args.report_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = out_dir / f"cross_asset_div_sweep_{ts}.md"

    md: List[str] = []
    md.append(f"# Cross-asset divergence sweep — {ts}")
    md.append("")
    md.append(f"Anchor: `{args.anchor}` · Alts: `{','.join(alts)}` · "
              f"History: {args.days}d · Interval: 1h")
    md.append("")
    md.append("Divergence = `alt_trailing_return - btc_trailing_return` "
              "over each lookback window. The interesting buckets are "
              "the joint-condition cells: e.g. *BTC ran up but the alt "
              "lagged* (catch-up hypothesis) or *BTC flat but alt ran "
              "ahead* (fade hypothesis).")
    md.append("")

    # Accumulate pooled-across-alts arrays per (lookback, horizon)
    pooled: Dict = {}

    for alt in alts:
        print(f"[sweep] {alt}: loading + aligning...", flush=True)
        alt_data = _load_closes(alt, args.days)
        if alt_data is None:
            print(f"  {alt}: no data, skipping")
            continue
        b_ts, b_closes = alt_data
        a_aligned, b_aligned, common_ts = _align(a_ts, a_closes, b_ts, b_closes)
        if a_aligned is None:
            continue

        md.append(f"## {alt}")
        md.append("")
        for lb in lookbacks:
            btc_ret = _trailing_return(a_aligned, lb)
            alt_ret = _trailing_return(b_aligned, lb)
            divergence = alt_ret - btc_ret
            for h in horizons:
                fwd = _forward_return(b_aligned, h)
                md.append(bucket_by_divergence(
                    divergence, btc_ret, fwd,
                    f"{alt} — lookback {lb}h, fwd {h}h",
                ))
                key = (lb, h)
                if key not in pooled:
                    pooled[key] = {"div": [], "btc": [], "fwd": []}
                pooled[key]["div"].append(divergence)
                pooled[key]["btc"].append(btc_ret)
                pooled[key]["fwd"].append(fwd)

    md.append("## Aggregate across all alts")
    md.append("")
    for (lb, h), pools in sorted(pooled.items()):
        div_all = np.concatenate(pools["div"])
        btc_all = np.concatenate(pools["btc"])
        fwd_all = np.concatenate(pools["fwd"])
        md.append(bucket_by_divergence(
            div_all, btc_all, fwd_all,
            f"Pooled (all alts) — lookback {lb}h, fwd {h}h",
        ))

    report.write_text("\n".join(md))
    print(f"[sweep] Wrote {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
