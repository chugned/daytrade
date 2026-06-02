#!/usr/bin/env python3
"""Empirically validate the volume-z-score signal on real history.

The hypothesis: when 1-hour volume prints N standard deviations above its
trailing mean, the next K hours of price action are systematically different
from the unconditional distribution. We don't pre-commit to a direction
(mean-reverting vs continuation) — the sweep buckets returns by both the
size of the z-spike AND the sign of the bar that produced it (green vs red),
so each cell isolates a distinct microstructure regime:

    * green-volume-spike (high vol on an up bar) — was that informed
      buying, or panic FOMO that fades?
    * red-volume-spike (high vol on a down bar) — capitulation low, or
      first leg of a cascade?

Forward horizons tested: 1h, 4h, 24h. The signal is only useful if it
clears trading cost at SOME horizon, so we report all three.

Usage::

    PYTHONPATH=src python3 scripts/sweep_volume_zscore.py \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT \\
        --days 500

Read-only — uses the existing Binance public-kline cache only. Never
touches the live bot.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

import numpy as np  # noqa: E402

from daytrade.research.history import download_history  # noqa: E402


# --------------------------------------------------------------------------- #
# Per-symbol sweep                                                            #
# --------------------------------------------------------------------------- #

def evaluate_symbol(
    symbol: str,
    days: int,
    z_window: int,
    fwd_horizons_h: List[int],
) -> Dict | None:
    """Return arrays of (z, sign, fwd-returns@each-horizon) for the symbol.

    ``z_window`` is the rolling window for the z-score baseline (in 1h bars).
    20 is a sensible default — matches the existing pipeline's
    ``volume_ratio_20``.
    """
    klines = download_history(symbol, interval="1h", days=days)
    if len(klines) < z_window + max(fwd_horizons_h) + 10:
        return None

    closes = np.array([k.close for k in klines], dtype=float)
    volumes = np.array([k.volume for k in klines], dtype=float)
    opens = np.array([k.open for k in klines], dtype=float)
    n = len(klines)

    # Rolling z-score of volume — strict trailing (no peek): use bars
    # [i-z_window, i) to compute mean/std, evaluate at i.
    z = np.full(n, np.nan)
    for i in range(z_window, n):
        window = volumes[i - z_window : i]
        mu = window.mean()
        sd = window.std(ddof=1)
        if sd > 0:
            z[i] = (volumes[i] - mu) / sd

    # Bar sign: +1 if close > open ("green"), -1 if close < open ("red"),
    # 0 if doji. We use this to separate up-volume from down-volume spikes.
    bar_sign = np.where(closes > opens, 1, np.where(closes < opens, -1, 0))

    # Forward return at each horizon (% from this bar's close).
    fwd_returns: Dict[int, np.ndarray] = {}
    for h in fwd_horizons_h:
        ret = np.full(n, np.nan)
        for i in range(n - h):
            if closes[i] > 0:
                ret[i] = (closes[i + h] / closes[i] - 1.0) * 100
        fwd_returns[h] = ret

    return {
        "symbol": symbol,
        "z": z,
        "bar_sign": bar_sign,
        "fwd_returns": fwd_returns,
    }


# --------------------------------------------------------------------------- #
# Bucketing                                                                   #
# --------------------------------------------------------------------------- #

def _summary(arr: np.ndarray) -> Tuple[int, float, float]:
    """(n, mean, std) ignoring NaN, return (0, nan, nan) if all NaN."""
    mask = ~np.isnan(arr)
    if not mask.any():
        return 0, float("nan"), float("nan")
    a = arr[mask]
    return int(a.size), float(a.mean()), float(a.std(ddof=1) if a.size > 1 else 0.0)


def bucket_by_z(
    z: np.ndarray,
    bar_sign: np.ndarray,
    fwd: np.ndarray,
    z_thresholds: List[float],
) -> str:
    """For one (symbol, horizon), produce a markdown table:

      regime          | n   | mean fwd return | std | lift vs baseline
    """
    lines = [
        "| regime                          |     n |  mean fwd | std    | lift vs base |",
        "|---------------------------------|------:|----------:|-------:|-------------:|",
    ]
    base_n, base_mean, _ = _summary(fwd)
    lines.append(
        f"| baseline (all bars)             | {base_n:5d} | "
        f"{base_mean:+8.3f}% | {_summary(fwd)[2]:6.3f} |              — |"
    )

    for thr in z_thresholds:
        for sign_label, sign_filter in (
            ("green", bar_sign == 1),
            ("red",   bar_sign == -1),
            ("any",   np.ones_like(bar_sign, dtype=bool)),
        ):
            mask = (z >= thr) & sign_filter & ~np.isnan(z) & ~np.isnan(fwd)
            n, mu, sd = _summary(fwd[mask])
            if n == 0:
                continue
            lift = mu - base_mean
            label = f"vol z≥{thr:+.1f}, {sign_label}-bar"
            lines.append(
                f"| {label:31s} | {n:5d} | {mu:+8.3f}% | {sd:6.3f} | "
                f"{lift:+10.3f} pp |"
            )

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,LINKUSDT,AVAXUSDT")
    p.add_argument("--days", type=int, default=500)
    p.add_argument("--z-window", type=int, default=20)
    p.add_argument("--z-thresholds", default="1.0,2.0,3.0,4.0,5.0")
    p.add_argument("--fwd-hours", default="1,4,24")
    p.add_argument("--report-dir", default="reports")
    args = p.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    z_thresholds = [float(x) for x in args.z_thresholds.split(",")]
    horizons = [int(x) for x in args.fwd_hours.split(",")]

    # Collect arrays per (symbol).
    per_sym: Dict[str, Dict] = {}
    for sym in symbols:
        print(f"[sweep] {sym}: downloading {args.days}d 1h klines...", flush=True)
        result = evaluate_symbol(sym, args.days, args.z_window, horizons)
        if result is None:
            print(f"[sweep] {sym}: insufficient data, skipping.", flush=True)
            continue
        per_sym[sym] = result

    if not per_sym:
        print("[sweep] No usable data for any symbol.")
        return 1

    # Build markdown report.
    out_dir = _REPO / args.report_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = out_dir / f"volume_z_sweep_{ts}.md"

    md: List[str] = []
    md.append(f"# Volume z-score sweep — {ts}")
    md.append("")
    md.append(f"Symbols: `{','.join(per_sym)}`  ·  History: {args.days} days  ·  "
              f"Bar interval: 1h  ·  z-window: {args.z_window} bars")
    md.append("")
    md.append("> *Reading guide*: rows tagged `z≥X` mean the 1-hour volume on the "
              "trigger bar was X standard deviations above its trailing 20-bar mean. "
              "`green-bar` = close>open (buying-led spike), `red-bar` = close<open "
              "(selling-led spike). Forward return is `(close[t+h] / close[t] - 1)`.")
    md.append("")

    # 1. Per-symbol, per-horizon detail (folds out)
    for sym, data in per_sym.items():
        md.append(f"## {sym}")
        md.append("")
        for h in horizons:
            md.append(f"### {sym} — forward horizon {h}h")
            md.append("")
            md.append(bucket_by_z(
                data["z"], data["bar_sign"], data["fwd_returns"][h], z_thresholds,
            ))
            md.append("")

    # 2. Aggregate across symbols (pool all observations)
    md.append("## Aggregate across all symbols")
    md.append("")
    for h in horizons:
        z_all = np.concatenate([d["z"] for d in per_sym.values()])
        sign_all = np.concatenate([d["bar_sign"] for d in per_sym.values()])
        fwd_all = np.concatenate([d["fwd_returns"][h] for d in per_sym.values()])
        md.append(f"### Pooled — horizon {h}h")
        md.append("")
        md.append(bucket_by_z(z_all, sign_all, fwd_all, z_thresholds))
        md.append("")

    report.write_text("\n".join(md))
    print(f"[sweep] Wrote {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
