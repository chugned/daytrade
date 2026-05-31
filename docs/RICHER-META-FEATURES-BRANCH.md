# Richer-Meta-Features branch

## Why
Recent research branches (MTF filter, mean-reversion mode, funding gate)
introduced new signals as **hard gates** in the strategy code. A hard gate
can only ever subtract trades — it cannot teach the meta-model anything. To
let the meta-classifier learn whether a higher-TF slope, an oversold RSI, or a
position-in-range value is genuinely informative for *this* setup, the same
underlying numbers must reach the feature matrix as **continuous columns**.

This branch ships those columns. No gates change. No code path is bypassed.
The model simply sees more of the truth.

## What was added
Nine new feature columns, in `feature_columns()` order:

| Column                  | Meaning                                          |
| ----------------------- | ------------------------------------------------ |
| `slope_15m`             | OLS slope of 15-min close, **previous closed bar** |
| `slope_1h`              | OLS slope of 1-h   close, **previous closed bar** |
| `ret_15`                | 15-minute return                                 |
| `rsi_dist_oversold`     | `rsi - 30` (negative ⇒ oversold)                 |
| `rsi_dist_overbought`   | `rsi - 70` (positive ⇒ overbought)               |
| `volume_ratio_20`       | `volume / rolling_mean(volume, 20)`              |
| `pct_from_60_high`      | `(close - high_60) / close`                      |
| `pct_from_60_low`       | `(close - low_60)  / close`                      |
| `pos_in_60_range`       | `(close - low_60) / (high_60 - low_60)`          |

All nine are **causal**. The leakage test
(`tests/test_leakage.py::test_feature_pipeline_no_lookahead`) verifies that
truncating the candle series leaves every feature at every retained timestamp
exactly unchanged.

### The non-obvious causality fix
`resample("15min").last()` aggregates bars *within* a 15-min bucket. The bucket
*containing* a 1-min bar `t` extends beyond `t` — using its `.last()` would
peek at the future. The slope series is therefore **shifted by one HTF bar**
before being forward-filled back onto the 1-min index, so every 1-min bar
sees the slope of the **previous, fully closed** HTF bar.

### Walk-forward robustness
A wider feature warmup (60-bar rolling for `pos_in_60_range`) shrinks the
useful sample count. Some folds become single-class, and `roc_auc_score`
returns NaN (not an exception) in that case. `walk_forward_validate` now
treats a non-finite AUC as the 0.5 "no-info" baseline, matching how it
already handled the `ValueError` path.

## What was *not* changed
- No gating logic moved. The MTF / funding / MR gates remain optional in
  `GatingConfig`; the new columns are visible to the model irrespective of
  those flags.
- No live trading. No wallets. No order routing. Paper / simulation only.

## Verification
```
$ python3 -m pytest -q
302 passed, 1 warning
```
