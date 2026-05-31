# Cascade-As-Feature branch

## Why
The 90-day sweep in `docs/RESEARCH-90D-FINDINGS.md` showed something
the 1000-bar sweep on the Liquidation-Cascade branch could not:

> CASCADE_EXHAUSTION carries a real edge — strong on SOL at the 30m
> horizon (+9.7 bp on 101 events vs −0.1 bp baseline) — but the *sign
> flips* on ETH. A blanket gate cannot capture that. A per-symbol
> learned weight can.

So instead of leaving the cascade signal as a disabled gate, this
branch exposes its underlying numbers as **features** of the meta-model.
The model can then learn the per-symbol coefficient empirically — the
same principle that drove the Richer-Meta-Features branch.

## What was added
Five new feature columns in `feature_columns()`, all computed inline
in `compute_features()`:

| Column | Meaning |
| --- | --- |
| `cascade_body_atr` | Bar body in ATR(14) units (signed, < 0 = bearish) |
| `cascade_vol_spike` | Bar volume / shifted 20-bar mean volume |
| `cascade_lower_wick` | Lower wick / total bar range (0–1) |
| `cascade_active` | 0/1 flag: body ≤ −2·ATR AND vol_spike ≥ 3× |
| `cascade_exhaustion` | 0/1 flag: prior bar active AND wick ≥ 55% AND vol_spike ≥ 1.5× |

All five are **causal**: the volume-baseline rolling mean is
shifted by one bar before the ratio is taken, so the value at bar
`t` uses only bars `< t`. Verified by:

1. The general `tests/test_leakage.py::test_feature_pipeline_no_lookahead`
   (passes).
2. The targeted `tests/test_cascade_features.py::test_cascade_columns_are_causal`
   (passes).

## What this branch does NOT do
- The gate added on the Liquidation-Cascade branch (off by default) is
  unchanged. The features and the gate now coexist; the gate stays off.
- No live trading. No wallets. No new external services.

## Verification
```
$ python3 -m pytest -q
373 passed, 1 warning
```
