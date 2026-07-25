"""Configurable, auditable AI risk overlays for historical Walk-Forward use."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverlayDecision:
    """The AI-only changes layered on top of an already valid technical BUY."""

    approved: bool
    multiplier: float
    rank: float
    rejection_reason: str = ""


class AIRiskOverlay:
    """Apply sizing/ranking policy without changing technical strategy rules."""

    AI_HARD_FILTER = "AI_HARD_FILTER"
    AI_POSITION_SIZING = "AI_POSITION_SIZING"
    AI_RANKING_ONLY = "AI_RANKING_ONLY"
    AI_HYBRID = "AI_HYBRID"

    AI_MODES = {
        AI_HARD_FILTER,
        AI_POSITION_SIZING,
        AI_RANKING_ONLY,
        AI_HYBRID,
    }

    def __init__(self, config, hard_filter_probability):
        self.config = config or {}
        self.hard_filter_probability = float(hard_filter_probability)
        self.sizing = self.config.get("position_sizing", {})
        self.ranking = self.config.get("ranking", {})
        self.hybrid = self.config.get("hybrid", {})

    def apply(self, mode, signal, probability):
        """Return the overlay decision for a technical BUY and OOS probability."""
        probability = float(probability)

        if mode == self.AI_HARD_FILTER:
            if probability < self.hard_filter_probability:
                return OverlayDecision(False, 0.0, 0.0, "AIProbability")
            return OverlayDecision(True, 1.0, 0.0)

        if mode == self.AI_POSITION_SIZING:
            multiplier, rejected = self.position_multiplier(probability)
            return OverlayDecision(not rejected, multiplier, 0.0,
                                   "AIPositionSizing" if rejected else "")

        if mode == self.AI_RANKING_ONLY:
            return OverlayDecision(True, 1.0, self.rank(signal, probability))

        if mode == self.AI_HYBRID:
            emergency = float(self.hybrid.get("emergency_min_probability", 0))
            if probability < emergency:
                return OverlayDecision(False, 0.0, 0.0, "AIEmergencyProbability")
            multiplier, rejected = self.position_multiplier(probability)
            if rejected:
                return OverlayDecision(False, 0.0, 0.0, "AIPositionSizing")
            return OverlayDecision(True, multiplier, self.rank(signal, probability))

        raise ValueError(f"Unsupported AI risk overlay mode: {mode}")

    def position_multiplier(self, probability):
        """Find the configured sizing band; below-band action is explicit."""
        bands = sorted(
            self.sizing.get("bands", []),
            key=lambda band: float(band.get("min_probability", 0)),
            reverse=True,
        )
        for band in bands:
            if probability >= float(band.get("min_probability", 0)):
                return float(band.get("multiplier", 1.0)), False

        action = str(self.sizing.get("below_band_action", "size")).lower()
        if action == "reject":
            return 0.0, True
        return float(self.sizing.get("below_band_multiplier", 0.25)), False

    def rank(self, signal, probability):
        """Score only breaks portfolio-capacity ties; it never creates a BUY."""
        weights = self.ranking.get("weights", {})
        quality = _unit(signal.get("Score", 0), 100)
        ai_probability = _unit(probability, 100)
        risk_reward = _unit(
            signal.get("RR", 0),
            float(self.ranking.get("rr_cap", 5.0)),
        )
        confidence = _unit(signal.get("Confidence", 0), 100)
        regime_scores = self.ranking.get("regime_scores", {})
        regime = str(signal.get("Regime", "")).upper()
        regime_score = _unit(regime_scores.get(regime, 0), 1)

        score = (
            float(weights.get("strategy_quality", 0.40)) * quality
            + float(weights.get("ai_probability", 0.35)) * ai_probability
            + float(weights.get("risk_reward", 0.15)) * risk_reward
            + float(weights.get("confidence", 0.05)) * confidence
            + float(weights.get("market_regime", 0.05)) * regime_score
        )
        return round(score * 100, 2)


def _unit(value, cap):
    try:
        return max(0.0, min(float(value) / float(cap), 1.0)) if cap else 0.0
    except (TypeError, ValueError):
        return 0.0
