#!/usr/bin/env python3
"""Empirically validate the *taker-buy ratio* signal — the closest
historical proxy to order-book imbalance.

Binance's public klines endpoint returns 12 fields per bar; fields [9]
and [10] are ``taker_buy_base_asset_volume`` and ``taker_buy_quote_asset_volume``
respectively: i.e. the fraction of total volume that was *aggressive*
buying (orders that crossed the spread and lifted the offer). The
remainder is aggressive selling.

We don't have historical L2 snapshots, but we have historical aggressive
flow — and aggressive flow is *what causes* book imbalance to deplete
on one side. So this is the right proxy for the order-book persistence
hypothesis: when one side is consistently aggressive (TBR > 0.55 or
< 0.45 for 3+ consecutive 1h bars), is forward price action different?

Usage::

    PYTHONPATH=src python3 scripts/sweep_taker_buy_ratio.py \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT \\
        --days 500

Read-only — touches Binance public endpoints only.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

import httpx  # noqa: E402
import numpy as np  # noqa: E402


_BASE = "https://data-api.binance.vision"
_INTERVAL_MS = 3_600_000  # 1h


def _fetch_page(symbol: str, start_ms: int, end_ms: int, timeout: float = 15.0):
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(
            _BASE + "/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": "1h",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
        )
        resp.raise_for_status()
        return resp.json()


def fetch_klines_full(symbol: str, days: int):
    """Pull raw 12-field klines so we can read fields [9] and [10]."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    cursor = start_ms
    out: List[list] = []
    while cursor < now_ms:
        rows = _fetch_page(symbol, cursor, now_ms)
        if not rows:
            break
        out.extend(rows)
        cursor = int(rows[-1][0]) + _INTERVAL_MS
        if len(rows) < 1000:
            break
        time.sleep(0.12)
    return out


def evaluate_symbol(symbol: str, days: int, fwd_horizons: List[int]):
    print(f"[sweep] {symbol}: fetching {days}d klines (full payload)...", flush=True)
    raw = fetch_klines_full(symbol, days)
    if len(raw) < 100:
        return None

    closes = np.array([float(r[4]) for r in raw], dtype=float)
    volumes = np.array([float(r[5]) for r in raw], dtype=float)
    taker_buy_base = np.array([float(r[9]) for r in raw], dtype=float)
    n = len(raw)

    # Taker buy ratio = aggressive-buy volume / total volume.
    # 0.50 = balanced; 0.65 = strong aggressive buying; 0.35 = strong selling.
    with np.errstate(divide="ignore", invalid="ignore"):
        tbr = np.where(volumes > 0, taker_buy_base / volumes, np.nan)

    # Persistence = consecutive bars with TBR on the same side of 0.50.
    # streak_buy[i] = number of consecutive bars ending at i with TBR>0.55
    # (or 0 if the streak breaks). Same for streak_sell with TBR<0.45.
    BUY_TH = 0.55
    SELL_TH = 0.45
    streak_buy = np.zeros(n, dtype=int)
    streak_sell = np.zeros(n, dtype=int)
    for i in range(n):
        if np.isnan(tbr[i]):
            continue
        streak_buy[i] = streak_buy[i - 1] + 1 if (i > 0 and tbr[i] > BUY_TH and streak_buy[i - 1] > 0) \
                        else (1 if tbr[i] > BUY_TH else 0)
        streak_sell[i] = streak_sell[i - 1] + 1 if (i > 0 and tbr[i] < SELL_TH and streak_sell[i - 1] > 0) \
                         else (1 if tbr[i] < SELL_TH else 0)

    fwd_returns: Dict[int, np.ndarray] = {}
    for h in fwd_horizons:
        ret = np.full(n, np.nan)
        for i in range(n - h):
            if closes[i] > 0:
                ret[i] = (closes[i + h] / closes[i] - 1.0) * 100
        fwd_returns[h] = ret

    return {
        "symbol": symbol,
        "tbr": tbr,
        "streak_buy": streak_buy,
        "streak_sell": streak_sell,
        "fwd_returns": fwd_returns,
    }


def _summary(arr):
    mask = ~np.isnan(arr)
    if not mask.any():
        return 0, float("nan"), float("nan")
    a = arr[mask]
    return int(a.size), float(a.mean()), float(a.std(ddof=1) if a.size > 1 else 0.0)


def bucket_report_tbr(tbr, fwd, label) -> str:
    """Single-bar TBR buckets vs forward return."""
    lines = [
        f"### {label}",
        "",
        "| regime            |     n |   mean fwd | lift vs base |",
        "|-------------------|------:|-----------:|-------------:|",
    ]
    base_n, base_mean, _ = _summary(fwd)
    lines.append(f"| baseline          | {base_n:5d} | {base_mean:+8.3f}% |          —   |")

    for lo, hi, name in [
        (0.00, 0.40, "TBR < 0.40 (extreme sell)"),
        (0.40, 0.45, "TBR 0.40-0.45 (sell)"),
        (0.45, 0.50, "TBR 0.45-0.50 (mild sell)"),
        (0.50, 0.55, "TBR 0.50-0.55 (mild buy)"),
        (0.55, 0.60, "TBR 0.55-0.60 (buy)"),
        (0.60, 1.01, "TBR ≥ 0.60 (extreme buy)"),
    ]:
        mask = (tbr >= lo) & (tbr < hi) & ~np.isnan(tbr) & ~np.isnan(fwd)
        n, mu, _ = _summary(fwd[mask])
        if n == 0:
            continue
        lift = mu - base_mean
        lines.append(f"| {name:17s} | {n:5d} | {mu:+8.3f}% |    {lift:+7.3f} pp |")
    lines.append("")
    return "\n".join(lines)


def bucket_report_persistence(streak_buy, streak_sell, fwd, label) -> str:
    """Persistence buckets: how do forward returns evolve with consecutive
    extreme TBR streaks?"""
    lines = [
        f"### {label} — persistence test",
        "",
        "| streak                          |     n |   mean fwd | lift vs base |",
        "|---------------------------------|------:|-----------:|-------------:|",
    ]
    base_n, base_mean, _ = _summary(fwd)
    lines.append(f"| baseline (any bar)              | {base_n:5d} | {base_mean:+8.3f}% |          —   |")

    for k in [1, 2, 3, 4, 5]:
        mask = (streak_buy >= k) & ~np.isnan(fwd)
        n, mu, _ = _summary(fwd[mask])
        if n > 0:
            lift = mu - base_mean
            lines.append(f"| streak_buy ≥ {k} bars (TBR>0.55)  | {n:5d} | {mu:+8.3f}% |    {lift:+7.3f} pp |")

    for k in [1, 2, 3, 4, 5]:
        mask = (streak_sell >= k) & ~np.isnan(fwd)
        n, mu, _ = _summary(fwd[mask])
        if n > 0:
            lift = mu - base_mean
            lines.append(f"| streak_sell ≥ {k} bars (TBR<0.45) | {n:5d} | {mu:+8.3f}% |    {lift:+7.3f} pp |")
    lines.append("")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT")
    p.add_argument("--days", type=int, default=500)
    p.add_argument("--fwd-hours", default="1,4,24")
    p.add_argument("--report-dir", default="reports")
    args = p.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    horizons = [int(x) for x in args.fwd_hours.split(",")]

    per_sym: Dict[str, Dict] = {}
    for sym in symbols:
        result = evaluate_symbol(sym, args.days, horizons)
        if result is None:
            print(f"[sweep] {sym}: insufficient data, skipping.")
            continue
        per_sym[sym] = result

    if not per_sym:
        return 1

    out_dir = _REPO / args.report_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = out_dir / f"taker_buy_sweep_{ts}.md"

    md: List[str] = []
    md.append(f"# Taker-buy ratio sweep — {ts}")
    md.append("")
    md.append(f"Symbols: `{','.join(per_sym)}` · History: {args.days}d · Interval: 1h")
    md.append("")
    md.append("Taker buy ratio (TBR) = aggressive-buy volume / total volume on the bar. "
              "0.50 = balanced. TBR > 0.55 = aggressive buying dominant. TBR < 0.45 = "
              "aggressive selling dominant. Persistence streak = consecutive bars on "
              "the same side.")
    md.append("")

    for sym, data in per_sym.items():
        md.append(f"## {sym}")
        md.append("")
        for h in horizons:
            md.append(bucket_report_tbr(data["tbr"], data["fwd_returns"][h],
                                          f"{sym} — single-bar TBR, fwd {h}h"))
            md.append(bucket_report_persistence(data["streak_buy"], data["streak_sell"],
                                                  data["fwd_returns"][h],
                                                  f"{sym} fwd {h}h"))

    md.append("## Aggregate (pooled across all symbols)")
    md.append("")
    for h in horizons:
        tbr_all = np.concatenate([d["tbr"] for d in per_sym.values()])
        sb_all = np.concatenate([d["streak_buy"] for d in per_sym.values()])
        ss_all = np.concatenate([d["streak_sell"] for d in per_sym.values()])
        fwd_all = np.concatenate([d["fwd_returns"][h] for d in per_sym.values()])
        md.append(bucket_report_tbr(tbr_all, fwd_all, f"Pooled — fwd {h}h"))
        md.append(bucket_report_persistence(sb_all, ss_all, fwd_all, f"Pooled fwd {h}h"))

    report.write_text("\n".join(md))
    print(f"[sweep] Wrote {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
