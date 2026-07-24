"""Independent adaptive selector consuming final strategy outputs only."""

from __future__ import annotations

from dataclasses import dataclass, fields
import json
from pathlib import Path

from strategy_selector.market_classifier import MarketClassifier
from strategy_selector.selector_score import (
    WalkForwardPerformanceLedger,
    output_quality,
    strategy_edge_score,
)


SETTINGS_PATH = Path(__file__).with_name("selector_settings.json")
PERFORMANCE_PATH = Path(__file__).resolve().parents[1] / "reports" / "phase11_selector_probabilities.json"


def load_selector_settings(path=SETTINGS_PATH):
    settings = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not settings.get("decision_support_only", False):
        raise ValueError("Adaptive selector must remain Decision Support Only")
    return settings


class AdaptiveStrategySelector:
    def __init__(self, settings=None, ledger=None):
        self.settings = settings or load_selector_settings()
        self.classifier = MarketClassifier(self.settings)
        self.ledger = ledger or WalkForwardPerformanceLedger.from_json(
            self.settings, PERFORMANCE_PATH
        )

    def classify_scan(self, rows):
        return self.classifier.from_scan_results(rows)

    def select_symbol(self, classic, breakout, classification):
        regime = classification.regime
        classic_probability = self.ledger.probability("CLASSIC", regime)
        breakout_probability = self.ledger.probability("BREAKOUT_SWING", regime)
        classic_quality = output_quality("CLASSIC", classic)
        breakout_quality = output_quality("BREAKOUT_SWING", breakout)
        classic_edge = strategy_edge_score(
            classic_probability.probability, classic_quality, self.settings
        )
        breakout_edge = strategy_edge_score(
            breakout_probability.probability, breakout_quality, self.settings
        )
        if breakout_edge > classic_edge:
            preferred = "BREAKOUT_SWING"
            preferred_probability = breakout_probability.probability
            other_probability = classic_probability.probability
        else:
            preferred = "CLASSIC"
            preferred_probability = classic_probability.probability
            other_probability = breakout_probability.probability

        total_samples = classic_probability.samples + breakout_probability.samples
        sample_coverage = min(
            total_samples / max(int(self.settings["minimum_regime_samples"]) * 2, 1), 1.0
        )
        probability_separation = abs(
            classic_probability.probability - breakout_probability.probability
        )
        confidence = round(
            classification.confidence * 0.40
            + sample_coverage * 100 * 0.35
            + probability_separation * 100 * 0.25,
            2,
        )
        final = self._final_recommendation(
            classic, breakout, regime, preferred, preferred_probability,
            other_probability, confidence, total_samples,
        )
        preferred_signal = (
            breakout.get("Signal", breakout.get("BreakoutDecision"))
            if preferred == "BREAKOUT_SWING"
            else classic.get("Signal", classic.get("ClassicDecision"))
        )
        other = "CLASSIC" if preferred == "BREAKOUT_SWING" else "BREAKOUT_SWING"
        reasons = [
            *classification.reasons,
            f"{preferred} edge={max(classic_edge, breakout_edge):.2f}",
            f"{preferred} walk-forward success probability={preferred_probability:.1%}",
            f"{other} probability={other_probability:.1%}",
            f"Preferred output={preferred_signal}",
        ]
        return {
            "MarketRegime": regime,
            "MarketRegimeConfidence": classification.confidence,
            "PreferredStrategy": preferred,
            "SelectorConfidence": confidence,
            "StrategyEdgeScore": max(classic_edge, breakout_edge),
            "ClassicEdgeScore": classic_edge,
            "BreakoutSelectorEdgeScore": breakout_edge,
            "ClassicSuccessProbability": round(classic_probability.probability * 100, 2),
            "BreakoutSuccessProbability": round(breakout_probability.probability * 100, 2),
            "ClassicRegimeSamples": classic_probability.samples,
            "BreakoutRegimeSamples": breakout_probability.samples,
            "FinalRecommendation": final,
            "SelectorReason": " | ".join(reasons),
            "WhyPreferred": f"Higher current edge/probability under {regime}",
            "WhyNotOther": (
                f"{other} had lower edge or no eligible final BUY output"
            ),
            "DecisionSupportOnly": True,
        }

    def _final_recommendation(
        self, classic, breakout, regime, preferred, preferred_probability,
        other_probability, confidence, total_samples,
    ):
        classic_signal = classic.get("Signal", classic.get("ClassicDecision", "AVOID"))
        breakout_signal = breakout.get("Signal", breakout.get("BreakoutDecision", "AVOID"))
        if regime in set(self.settings["blocked_buy_regimes"]):
            return "NO_TRADE"
        if regime == "HIGH_VOLATILITY" and self.settings["high_volatility_policy"] == "WATCH_ONLY":
            return "WATCH"
        if total_samples < int(self.settings["minimum_regime_samples"]):
            return "WATCH" if "WATCH" in {classic_signal, breakout_signal} or "BUY" in {classic_signal, breakout_signal} else "AVOID"
        if confidence < float(self.settings["minimum_selector_confidence"]):
            return "WATCH"
        if preferred_probability < float(self.settings["minimum_success_probability"]):
            return "WATCH"
        if preferred_probability - other_probability < float(self.settings["minimum_probability_advantage"]):
            return "WATCH"
        preferred_signal = breakout_signal if preferred == "BREAKOUT_SWING" else classic_signal
        if preferred_signal == "BUY":
            return "BUY_BREAKOUT" if preferred == "BREAKOUT_SWING" else "BUY_CLASSIC"
        if classic_signal == "AVOID" and breakout_signal == "AVOID":
            return "AVOID"
        return "WATCH"


def unavailable_selector_result(reason):
    return {
        "MarketRegime": "UNKNOWN", "MarketRegimeConfidence": 0.0,
        "PreferredStrategy": "NONE", "SelectorConfidence": 0.0,
        "StrategyEdgeScore": 0.0, "ClassicEdgeScore": 0.0,
        "BreakoutSelectorEdgeScore": 0.0, "ClassicSuccessProbability": 50.0,
        "BreakoutSuccessProbability": 50.0, "ClassicRegimeSamples": 0,
        "BreakoutRegimeSamples": 0, "FinalRecommendation": "NO_TRADE",
        "SelectorReason": str(reason), "WhyPreferred": "Selector unavailable",
        "WhyNotOther": "No adaptive decision was made", "DecisionSupportOnly": True,
    }
