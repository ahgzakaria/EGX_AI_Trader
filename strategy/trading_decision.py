"""The single application-facing trading decision service."""

from ai.predictor import AIPredictor
from ai.risk_overlay import AIRiskOverlay
import pandas as pd
from config.settings_manager import settings
from strategy.decision_engine import evaluate as evaluate_technical_signal


REJECTION_REASONS = (
    "MarketAnalyzer", "MarketFilter", "Score", "Confidence", "RR",
    "MaxRR", "Trend", "Momentum", "Volume", "QualityFilter",
    "CandleConfirmation", "AIProbability", "EntryTimeout",
)


class TradingDecisionService:
    """Return one final BUY/WATCH/AVOID decision for every entry point."""

    STRATEGY_ONLY = "STRATEGY_ONLY"
    LIVE_MODEL = "LIVE_MODEL"
    LIVE_ADVISORY = "LIVE_ADVISORY"
    AI_HARD_FILTER = "AI_HARD_FILTER"
    AI_POSITION_SIZING = "AI_POSITION_SIZING"
    AI_RANKING_ONLY = "AI_RANKING_ONLY"
    AI_HYBRID = "AI_HYBRID"
    # Kept as a backwards-compatible name for the Phase 2/3 hard filter.
    WALK_FORWARD_AI = "WALK_FORWARD_AI"

    def __init__(
        self,
        predictor=None,
        base_evaluator=evaluate_technical_signal,
        mode=LIVE_MODEL,
        historical_ai_filter=None,
        strict_historical_predictions=False,
    ):
        if mode not in {
            self.STRATEGY_ONLY,
            self.LIVE_MODEL,
            self.LIVE_ADVISORY,
            self.AI_HARD_FILTER,
            self.AI_POSITION_SIZING,
            self.AI_RANKING_ONLY,
            self.AI_HYBRID,
            self.WALK_FORWARD_AI,
        }:
            raise ValueError(f"Unknown decision mode: {mode}")

        self._base_evaluator = base_evaluator
        self.mode = mode
        self.historical_ai_filter = historical_ai_filter
        self.strict_historical_predictions = strict_historical_predictions
        ai_settings = settings.get("ai")
        self.ai_enabled = bool(ai_settings.get("enabled", False))
        self.min_probability = float(ai_settings.get("min_probability", 0))
        self.risk_overlay = AIRiskOverlay(
            settings.get("ai_risk_overlay"), self.min_probability
        )
        self.predictor = predictor

        # A historical backtest must never silently construct today's global
        # model: it could contain labels from the future.
        if (
            self.mode in {self.LIVE_MODEL, self.LIVE_ADVISORY}
            and self.ai_enabled
            and self.predictor is None
        ):
            try:
                self.predictor = AIPredictor()
            except (FileNotFoundError, ValueError, OSError) as error:
                print(f"AI Model Not Loaded -> {error}")

    def evaluate(self, df, index):
        result = dict(self._base_evaluator(df, index))
        trace = dict(result.get("DecisionTrace", {}))
        trace["AIProbability"] = "N/A"

        result["TechnicalSignal"] = result.get("Signal")
        result["AIMode"] = self.mode
        result["AIProbability"] = None
        result["AILevel"] = "Disabled" if not self.ai_enabled else "N/A"
        result["AIApproved"] = self.mode == self.STRATEGY_ONLY or not self.ai_enabled
        result["AIPositionMultiplier"] = 1.0
        result["AIRank"] = 0.0
        result["AIRejectionReason"] = ""

        if result.get("Signal") != "BUY":
            result["DecisionTrace"] = trace
            return result

        if self.mode == self.STRATEGY_ONLY:
            trace["AIProbability"] = "DISABLED"
            result["DecisionTrace"] = trace
            return result

        if not self.ai_enabled:
            result["DecisionTrace"] = trace
            return result

        features = df.iloc[index].to_dict()
        features["rr"] = result["RR"]

        if self.mode in {
            self.WALK_FORWARD_AI,
            self.AI_HARD_FILTER,
            self.AI_POSITION_SIZING,
            self.AI_RANKING_ONLY,
            self.AI_HYBRID,
        }:
            if self.historical_ai_filter is None:
                raise RuntimeError("AI risk-overlay mode requires a historical AI filter")
            prediction = self.historical_ai_filter.predict(features, df.index[index])
            if prediction is None:
                if self.strict_historical_predictions:
                    raise RuntimeError(
                        "Missing out-of-sample AI prediction for "
                        f"{pd.Timestamp(df.index[index]).date()}"
                    )
                # The model did not exist yet; retain the technical result.
                trace["AIProbability"] = "WARMUP"
                result["DecisionTrace"] = trace
                return result
            overlay_mode = (
                self.AI_HARD_FILTER
                if self.mode == self.WALK_FORWARD_AI
                else self.mode
            )
            return self._apply_overlay_prediction(
                result, trace, prediction, overlay_mode
            )

        if self.predictor is None:
            trace["AIProbability"] = "UNAVAILABLE"
            result["AIApproved"] = True
            result["DecisionTrace"] = trace
            return result

        prediction = self.predictor.predict(features)
        if self.mode == self.LIVE_ADVISORY:
            return self._apply_advisory_prediction(result, trace, prediction)
        return self._apply_prediction(result, trace, prediction)

    def _apply_advisory_prediction(self, result, trace, prediction):
        """Attach live AI evidence without overriding the frozen strategy."""
        probability = float(prediction["Probability"])
        approved = probability >= self.min_probability
        result["AIProbability"] = probability
        result["AILevel"] = prediction["AILevel"]
        result["AIApproved"] = approved
        result["AIRejectionReason"] = ""
        trace["AIProbability"] = "ADVISORY_PASS" if approved else "ADVISORY_LOW"
        result["DecisionTrace"] = trace
        # Signal, stars, reasons, and rejection fields remain exactly as the
        # unified technical strategy produced them.
        return result

    def _apply_prediction(self, result, trace, prediction):
        probability = float(prediction["Probability"])
        approved = probability >= self.min_probability

        result["AIProbability"] = probability
        result["AILevel"] = prediction["AILevel"]
        result["AIApproved"] = approved
        trace["AIProbability"] = "PASS" if approved else "FAIL"

        if not approved:
            result["Signal"] = "WATCH"
            result["Stars"] = min(int(result.get("Stars", 4)), 4)
            result["RejectReason"] = "AIProbability"
            result["Reasons"] = list(result.get("Reasons", [])) + [
                "AI probability "
                f"{probability}% below minimum {self.min_probability}%"
            ]

        result["DecisionTrace"] = trace
        return result

    def _apply_overlay_prediction(self, result, trace, prediction, overlay_mode):
        """Apply only the configured AI risk policy to a technical BUY."""
        probability = float(prediction["Probability"])
        decision = self.risk_overlay.apply(overlay_mode, result, probability)

        result["AIProbability"] = probability
        result["AILevel"] = prediction["AILevel"]
        result["AIApproved"] = decision.approved
        result["AIPositionMultiplier"] = decision.multiplier
        result["AIRank"] = decision.rank
        result["AIRejectionReason"] = decision.rejection_reason
        trace["AIProbability"] = "PASS" if decision.approved else "FAIL"

        if not decision.approved:
            result["Signal"] = "WATCH"
            result["Stars"] = min(int(result.get("Stars", 4)), 4)
            result["RejectReason"] = decision.rejection_reason
            result["Reasons"] = list(result.get("Reasons", [])) + [
                f"AI risk overlay rejected probability {probability}% "
                f"({decision.rejection_reason})"
            ]

        result["DecisionTrace"] = trace
        return result
