"""Conversion between OHLCV domain models and pandas frames.

A single, shared converter means indicators, features and the backtester all
agree on column names and index semantics — the index is the candle OPEN time,
sorted ascending.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from ..models import OHLCV

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def ohlcv_to_frame(candles: List[OHLCV]) -> pd.DataFrame:
    """Convert a list of :class:`OHLCV` to a time-indexed DataFrame.

    The result is sorted ascending by timestamp and de-duplicated (last wins),
    so downstream rolling windows are guaranteed causal and monotonic.
    """
    if not candles:
        return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([], name="timestamp"))
    rows = [
        {
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ]
    frame = pd.DataFrame(rows).set_index("timestamp")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frame[OHLCV_COLUMNS]
