"""Prediction vs reality tracking and prediction memory.

Every prediction the observer makes is later checked against what the market
actually did. :func:`evaluate_prediction` samples the deterministic feed at
the 5m / 15m / 1h / 4h horizons, walks the price path to see whether the
stop or target would have been touched, and records directional correctness
and a simulated PnL.

:class:`PredictionMemory` then turns the accumulated outcomes into something
the system can *learn* from: which regimes it predicts well, which it fails
in, and — importantly — when its confidence is "fake" (high confidence, poor
accuracy), which is the signal that it should stop acting on predictions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Horizon label -> minutes ahead.
HORIZONS: Dict[str, int] = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}

# Below this recent accuracy the system flags itself as unreliable.
_UNRELIABLE_ACCURACY = 0.45
# Confidence this high paired with sub-coin-flip accuracy is "fake confidence".
_FAKE_CONFIDENCE_LEVEL = 0.60


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def evaluate_prediction(
    prediction: Dict[str, Any],
    feed,
    now: datetime,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Evaluate one prediction against the realised price path.

    Returns ``(outcome, fully_evaluated)``. ``outcome`` is ``None`` when not
    even the 5-minute horizon has elapsed yet. ``fully_evaluated`` is True once
    the 4-hour horizon is known.
    """
    symbol = prediction["symbol"]
    direction = prediction["direction"]
    entry = prediction.get("entry")
    stop = prediction.get("stop")
    target = prediction.get("target")
    pred_ts = _parse_ts(prediction["ts"])

    prices: Dict[str, float] = {}
    for label, mins in HORIZONS.items():
        horizon_time = pred_ts + timedelta(minutes=mins)
        if now >= horizon_time:
            prices[label] = round(feed.price_at(symbol, horizon_time), 8)

    if not prices:
        return None, False  # nothing has matured yet

    fully_evaluated = "4h" in prices
    longest_label = max(prices, key=lambda k: HORIZONS[k])
    longest_mins = HORIZONS[longest_label]
    final_price = prices[longest_label]

    outcome: Dict[str, Any] = {
        "symbol": symbol,
        "predicted_ts": prediction["ts"],
        "price_5m": prices.get("5m"),
        "price_15m": prices.get("15m"),
        "price_1h": prices.get("1h"),
        "price_4h": prices.get("4h"),
    }

    # Non-directional predictions (HOLD) record prices but no trade result.
    if direction not in ("buy", "sell") or entry is None:
        outcome.update(
            {
                "directionally_correct": None,
                "stop_hit": 0,
                "target_hit": 0,
                "realized_pnl": 0.0,
                "slippage_estimate": 0.0,
            }
        )
        return outcome, fully_evaluated

    # Walk the realised path minute-by-minute for stop / target touches.
    stop_hit = target_hit = False
    exit_price = final_price
    for m in range(1, longest_mins + 1):
        px = feed.price_at(symbol, pred_ts + timedelta(minutes=m))
        if direction == "buy":
            if stop is not None and px <= stop:
                stop_hit, exit_price = True, stop
                break
            if target is not None and px >= target:
                target_hit, exit_price = True, target
                break
        else:  # sell
            if stop is not None and px >= stop:
                stop_hit, exit_price = True, stop
                break
            if target is not None and px <= target:
                target_hit, exit_price = True, target
                break

    sign = 1.0 if direction == "buy" else -1.0
    realized_pnl = round(sign * (exit_price - entry), 8)
    directionally_correct = int(sign * (final_price - entry) > 0)
    # A small, fixed simulated slippage estimate (bps of entry).
    slippage = round(entry * 0.0004, 8)

    outcome.update(
        {
            "directionally_correct": directionally_correct,
            "stop_hit": int(stop_hit),
            "target_hit": int(target_hit),
            "realized_pnl": realized_pnl,
            "slippage_estimate": slippage,
        }
    )
    return outcome, fully_evaluated


@dataclass(frozen=True)
class GroupAccuracy:
    """Accuracy stats for one group (a regime, a symbol, ...)."""

    label: str
    samples: int
    correct: int
    mean_confidence: float

    @property
    def accuracy(self) -> float:
        return self.correct / self.samples if self.samples else 0.0

    @property
    def is_fake_confidence(self) -> bool:
        """High stated confidence but accuracy no better than a coin flip."""
        return (
            self.samples >= 5
            and self.mean_confidence >= _FAKE_CONFIDENCE_LEVEL
            and self.accuracy < 0.5
        )


@dataclass
class PredictionMemory:
    """Learned performance summary built from accumulated outcomes."""

    total: int = 0
    correct: int = 0
    by_condition: Dict[str, GroupAccuracy] = field(default_factory=dict)
    by_symbol: Dict[str, GroupAccuracy] = field(default_factory=dict)
    confidence_buckets: Dict[str, GroupAccuracy] = field(default_factory=dict)

    @property
    def overall_accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def is_reliable(self) -> bool:
        return self.total < 10 or self.overall_accuracy >= _UNRELIABLE_ACCURACY

    def false_confidence_warnings(self) -> List[str]:
        """Groups where the model is confidently wrong."""
        warnings: List[str] = []
        for group in list(self.by_condition.values()) + list(self.by_symbol.values()):
            if group.is_fake_confidence:
                warnings.append(
                    f"{group.label}: {group.mean_confidence:.0%} stated "
                    f"confidence but only {group.accuracy:.0%} accurate "
                    f"({group.samples} samples) — treat as fake confidence"
                )
        return warnings

    def should_stop_trading(self) -> bool:
        """True when recent accuracy says predictions are not worth acting on."""
        return self.total >= 10 and self.overall_accuracy < _UNRELIABLE_ACCURACY

    def best_regimes(self) -> List[str]:
        ranked = sorted(
            (g for g in self.by_condition.values() if g.samples >= 3),
            key=lambda g: g.accuracy,
            reverse=True,
        )
        return [g.label for g in ranked]

    def worst_regimes(self) -> List[str]:
        return list(reversed(self.best_regimes()))


def _confidence_bucket(confidence: float) -> str:
    if confidence < 0.4:
        return "low (<40%)"
    if confidence < 0.6:
        return "medium (40-60%)"
    if confidence < 0.8:
        return "high (60-80%)"
    return "very high (80%+)"


def build_prediction_memory(outcomes: List[Dict[str, Any]]) -> PredictionMemory:
    """Build a :class:`PredictionMemory` from joined outcome+prediction rows.

    Each row must carry ``directionally_correct``, ``confidence``,
    ``market_condition`` and ``symbol`` (see ``ObservatoryDB.outcomes``).
    """
    memory = PredictionMemory()
    # group key -> [correct_count, total, confidence_sum]
    cond: Dict[str, List[float]] = {}
    sym: Dict[str, List[float]] = {}
    buck: Dict[str, List[float]] = {}

    for row in outcomes:
        correct = row.get("directionally_correct")
        if correct is None:  # HOLD predictions are not scored for direction
            continue
        confidence = float(row.get("confidence") or 0.0)
        memory.total += 1
        memory.correct += int(correct)
        for store, key in (
            (cond, row.get("market_condition") or "UNKNOWN"),
            (sym, row.get("symbol") or "UNKNOWN"),
            (buck, _confidence_bucket(confidence)),
        ):
            agg = store.setdefault(key, [0.0, 0.0, 0.0])
            agg[0] += int(correct)
            agg[1] += 1.0
            agg[2] += confidence

    def _finish(store: Dict[str, List[float]]) -> Dict[str, GroupAccuracy]:
        return {
            label: GroupAccuracy(
                label=label, samples=int(t), correct=int(c), mean_confidence=(s / t if t else 0.0)
            )
            for label, (c, t, s) in store.items()
        }

    memory.by_condition = _finish(cond)
    memory.by_symbol = _finish(sym)
    memory.confidence_buckets = _finish(buck)
    return memory
