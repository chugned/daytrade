"""AI decision-fusion engine.

Blends the four analysis layers — technical, microstructure, macro, ML — into
one explainable :class:`TradingDecision`. Two ideas drive the design:

1. **Confidence-weighted fusion.** Each layer's vote is weighted by both its
   configured importance *and* its own confidence, so an uncertain layer
   quietly steps back rather than dragging the consensus around.
2. **The kill switch has the last word.** No matter how strong the score, an
   active kill switch forces ``HOLD``. Analysis proposes; safety disposes.

Price levels (entry/stop/target) are placed in *volatility units* so they
adapt to the instrument and the regime, with a floor that stops calm markets
from producing unrealistically tight stops.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..config.schema import FusionConfig
from ..models import (
    Action,
    MacroSignal,
    MicrostructureSignal,
    MLSignal,
    TechnicalSignal,
    TradingDecision,
)
from ..safety.killswitch import KillSwitchResult


def _clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _sign(v: float, dead: float = 1e-9) -> int:
    if v > dead:
        return 1
    if v < -dead:
        return -1
    return 0


@dataclass(frozen=True)
class _Layer:
    """Internal: one analysis layer's vote."""

    name: str
    score: float
    confidence: float
    weight: float

    @property
    def effective_weight(self) -> float:
        """Configured weight scaled by the layer's own confidence."""
        return self.weight * self.confidence


class FusionEngine:
    """Fuses analysis signals into a trading decision."""

    def __init__(self, config: FusionConfig | None = None) -> None:
        self.config = config or FusionConfig()

    def decide(
        self,
        symbol: str,
        timestamp,
        technical: TechnicalSignal,
        microstructure: MicrostructureSignal,
        macro: MacroSignal,
        ml: MLSignal,
        reference_price: float,
        kill_switch: KillSwitchResult,
        atr: Optional[float] = None,
    ) -> TradingDecision:
        """Produce a :class:`TradingDecision`.

        Args:
            reference_price: the consensus/market price to anchor levels to.
            atr: absolute ATR in price units; drives the volatility unit. If
                None, only the volatility floor is used.
        """
        cfg = self.config
        w = cfg.weights
        layers = [
            _Layer("technical", technical.score, technical.confidence, w.technical),
            _Layer(
                "microstructure", microstructure.score, microstructure.confidence, w.microstructure
            ),
            _Layer("macro", macro.score, macro.confidence, w.macro),
            _Layer("ml", ml.score, ml.confidence, w.ml),
        ]

        fused_score, weighted_conf, agreement = self._fuse(layers)
        confidence = self._confidence(fused_score, weighted_conf, agreement)

        reasoning: List[str] = []
        for layer in layers:
            reasoning.append(
                f"{layer.name}: score={layer.score:+.2f} "
                f"conf={layer.confidence:.2f} weight={layer.weight:.2f}"
            )
        reasoning.append(f"Fused score {fused_score:+.3f} | decision confidence {confidence:.3f}")

        action = self._resolve_action(fused_score, confidence, kill_switch, reasoning)

        component_scores: Dict[str, float] = {
            "technical": technical.score,
            "microstructure": microstructure.score,
            "macro": macro.score,
            "ml": ml.score,
        }

        entry = stop = target = None
        if action is not Action.HOLD:
            entry, stop, target = self._levels(action, reference_price, atr)
            reasoning.append(
                f"Levels — entry {entry:,.2f} / stop {stop:,.2f} / target {target:,.2f}"
            )

        return TradingDecision(
            symbol=symbol,
            timestamp=timestamp,
            action=action,
            confidence=confidence,
            entry=entry,
            stop=stop,
            target=target,
            reference_price=reference_price,
            component_scores=component_scores,
            fused_score=fused_score,
            kill_switch_active=kill_switch.active,
            kill_switch_reasons=list(kill_switch.reasons),
            reasoning=reasoning,
        )

    # -- internals -----------------------------------------------------------

    def _fuse(self, layers: List[_Layer]) -> tuple[float, float, float]:
        """Return ``(fused_score, weighted_confidence, agreement)``."""
        eff_total = sum(layer.effective_weight for layer in layers)
        if eff_total <= 0:
            fused = 0.0
        else:
            fused = sum(layer.effective_weight * layer.score for layer in layers)
            fused = _clip(fused / eff_total)

        weight_total = sum(layer.weight for layer in layers) or 1.0
        weighted_conf = sum(layer.weight * layer.confidence for layer in layers) / weight_total

        # Agreement: weighted share of layers voting with the fused direction.
        fused_sign = _sign(fused)
        agree_num = 0.0
        for layer in layers:
            s = _sign(layer.score)
            if fused_sign == 0 or s == 0:
                agree_num += 0.5 * layer.weight
            elif s == fused_sign:
                agree_num += layer.weight
        agreement = agree_num / weight_total
        return fused, weighted_conf, agreement

    def _confidence(self, fused: float, weighted_conf: float, agreement: float) -> float:
        """Blend layer confidence, score magnitude and agreement into [0,1].

        Layer confidence dominates by design: if no analysis layer is
        confident, the decision cannot be confident — however strong the
        score or however unanimous the (low-conviction) layers are.
        """
        magnitude = min(1.0, abs(fused) / 0.5)
        raw = 0.73 * weighted_conf + 0.15 * magnitude + 0.10 * agreement
        return _clip(raw, 0.0, 1.0)

    def _resolve_action(
        self,
        fused: float,
        confidence: float,
        kill_switch: KillSwitchResult,
        reasoning: List[str],
    ) -> Action:
        """Apply the kill switch and the score/confidence gates."""
        if kill_switch.active:
            reasoning.append(f"HOLD — {kill_switch.summary}")
            return Action.HOLD
        if abs(fused) < self.config.action_threshold:
            reasoning.append(
                f"HOLD — fused score {fused:+.3f} within +/-"
                f"{self.config.action_threshold} dead zone"
            )
            return Action.HOLD
        if confidence < self.config.min_confidence:
            reasoning.append(
                f"HOLD — confidence {confidence:.3f} below minimum " f"{self.config.min_confidence}"
            )
            return Action.HOLD
        return Action.BUY if fused > 0 else Action.SELL

    def _volatility_unit(self, reference_price: float, atr: Optional[float]) -> float:
        """Volatility unit U = price * clip(ATR/price, floor, cap)."""
        cfg = self.config
        frac = 0.0 if atr is None else atr / reference_price
        frac = max(cfg.min_volatility_fraction, min(cfg.max_volatility_fraction, frac))
        return reference_price * frac

    def _levels(
        self, action: Action, reference_price: float, atr: Optional[float]
    ) -> tuple[float, float, float]:
        """Compute (entry, stop, target) for a directional action."""
        cfg = self.config
        unit = self._volatility_unit(reference_price, atr)
        offset = cfg.entry_offset_vol_mult * unit
        stop_dist = cfg.stop_vol_mult * unit
        target_dist = cfg.target_vol_mult * unit

        if action is Action.BUY:
            # Enter slightly below market (better fill), stop below, target above.
            entry = reference_price - offset
            stop = entry - stop_dist
            target = entry + target_dist
        else:  # SELL
            entry = reference_price + offset
            stop = entry + stop_dist
            target = entry - target_dist
        # Round to a precision appropriate for the asset's price magnitude —
        # a fixed 2 dp would collapse the levels of a low-priced alt-coin.
        dp = 2 if reference_price >= 100 else (4 if reference_price >= 1 else 8)
        return round(entry, dp), round(stop, dp), round(target, dp)
