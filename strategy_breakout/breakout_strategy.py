"""Top-level BREAKOUT_SWING decision engine.

The class is additive decision support.  It has no broker, order, Classic
strategy, AI, portfolio, or global-settings dependency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path

import pandas as pd

from strategy_breakout.breakout_entry import evaluate_entry, select_stop
from strategy_breakout.breakout_scoring import breakout_score, edge_score
from strategy_breakout.breakout_targets import select_targets


CONFIG_PATH = Path(__file__).with_name("settings.json")


@dataclass(frozen=True)
class BreakoutConfig:
    strategy_name: str = "BREAKOUT_SWING"
    enabled_by_default: bool = False
    decision_support_only: bool = True
    entry_mode: str = "BREAKOUT_CLOSE"
    stop_model: str = "ATR_STOP"
    target_model: str = "RISK_MULTIPLE"
    breakout_lookback: int = 20
    retest_lookback: int = 5
    retest_tolerance_percent: float = 1.0
    consolidation_window: int = 10
    max_consolidation_width_percent: float = 8.0
    #: Raised from 1.5 on 2026-08-18. Splitting breakouts into disjoint volume
    #: bands over 166,173 stock-days showed the old threshold straddled the
    #: line where the edge actually is. Twenty-day forward return, net of the
    #: 0.80% round trip, validation era after 2024-01-01:
    #:
    #:   no breakout at all        +2.91%   (53.0% win)
    #:   breakout, 1.0-1.5x vol    -0.11%   (46.0% win)  <- worse than nothing
    #:   breakout, 1.5-2.5x vol    +2.26%   (49.4% win)  <- no better than none
    #:   breakout, >= 2.5x vol     +5.73%   (59.0% win)
    #:
    #: The old 1.5 threshold admitted the middle band, which does not beat
    #: sitting out. Consistent across 5, 10 and 20-day holds and across both
    #: eras; n = 837 validation days above 2.5x.
    minimum_volume_ratio: float = 2.5
    atr_expansion_multiple: float = 1.2
    ema20_continuation_tolerance_percent: float = 2.0
    minimum_score: int = 65
    minimum_confidence: int = 60
    watch_score: int = 45
    minimum_rr: float = 1.5
    maximum_rr: float = 10.0
    atr_stop_multiple: float = 1.5
    ema20_stop_buffer_atr: float = 0.25
    recent_swing_window: int = 10
    support_window: int = 20
    support_buffer_atr: float = 0.3
    measured_move_window: int = 10
    atr_target_multiple: float = 3.0
    risk_target_multiple: float = 2.0
    weekly_resistance_lookback: int = 52
    trailing_atr_multiple: float = 2.0
    partial_first_r: float = 1.0
    partial_second_r: float = 2.0
    partial_percent: float = 0.5
    max_holding_days: int = 20
    entry_delay_bars: int = 1
    price_precision: int = 3
    commission: float = 0.003
    slippage: float = 0.0005


def load_breakout_config(path: Path | str = CONFIG_PATH) -> BreakoutConfig:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    allowed = {field.name for field in fields(BreakoutConfig)}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"Unknown BREAKOUT_SWING settings: {unknown}")
    config = BreakoutConfig(**value)
    if config.entry_mode not in {"BREAKOUT_CLOSE", "RETEST_ENTRY", "FIRST_PULLBACK"}:
        raise ValueError("Invalid BREAKOUT_SWING entry_mode")
    if not config.decision_support_only:
        raise ValueError("BREAKOUT_SWING must remain decision-support only")
    return config


class BreakoutSwingStrategy:
    def __init__(self, config: BreakoutConfig | None = None):
        self.config = config or load_breakout_config()

    def evaluate(self, df: pd.DataFrame, i: int | None = None) -> dict:
        i = len(df) - 1 if i is None else int(i)
        required = {"Open", "High", "Low", "Close", "Volume", "ATR", "EMA20", "EMA50", "EMA200"}
        missing = sorted(required - set(df.columns))
        if missing:
            raise ValueError(f"BREAKOUT_SWING missing columns: {missing}")
        entry = evaluate_entry(df, i, self.config)
        if not entry["eligible"]:
            score_info = breakout_score(df, i, entry, self.config) if i >= 200 else {
                "score": 0, "confidence": 0, "reasons": [], "features": {}
            }
            signal = "WATCH" if score_info["score"] >= self.config.watch_score else "AVOID"
            return self._empty_geometry(signal, entry, score_info)

        stop = select_stop(df, i, entry, self.config)
        if not stop["valid"]:
            score_info = breakout_score(df, i, entry, self.config)
            return self._empty_geometry("WATCH", entry, score_info, "INVALID_BREAKOUT_STOP", stop)
        targets = select_targets(df, i, entry, stop["stop"], self.config)
        score_info = breakout_score(df, i, entry, self.config)
        rr = float(targets["rr"])
        gates = {
            "Entry": bool(entry["eligible"]),
            "Stop": bool(stop["valid"]),
            "Target": bool(targets["valid"]),
            "RiskReward": self.config.minimum_rr <= rr <= self.config.maximum_rr,
            "Score": score_info["score"] >= self.config.minimum_score,
            "Confidence": score_info["confidence"] >= self.config.minimum_confidence,
        }
        signal = "BUY" if all(gates.values()) else (
            "WATCH" if score_info["score"] >= self.config.watch_score else "AVOID"
        )
        failed = next((name for name, passed in gates.items() if not passed), None)
        result = {
            "Strategy": self.config.strategy_name,
            "Signal": signal,
            "Score": score_info["score"],
            "Confidence": score_info["confidence"],
            "EdgeScore": edge_score(score_info["score"], score_info["confidence"], rr),
            "RR": rr,
            "Entry": round(float(entry["entry_price"]), self.config.price_precision),
            "StopLoss": stop["stop"],
            "Target1": targets["target1"],
            "Target2": targets["target2"],
            "BreakoutLevel": entry["breakout_level"],
            "EntryMode": entry["mode"],
            "StopModel": stop["model"],
            "TargetModel": targets["model"],
            "SetupTypes": [
                key for key, passed in entry["features"].items()
                if isinstance(passed, bool) and passed
            ],
            "Reasons": [entry["reason"], *score_info["reasons"]],
            "DecisionTrace": gates,
            "RejectReason": failed,
            "StopCandidates": stop["candidates"],
            "TargetCandidates": targets["candidates"],
            "PartialLevels": targets["partial_levels"],
            "TargetFallbackUsed": targets["fallback_used"],
            "BreakoutIndex": entry["breakout_index"],
            "Config": asdict(self.config),
            "DecisionSupportOnly": True,
        }
        return result

    def _empty_geometry(self, signal, entry, score_info, reason=None, stop=None):
        return {
            "Strategy": self.config.strategy_name,
            "Signal": signal,
            "Score": score_info["score"],
            "Confidence": score_info["confidence"],
            "EdgeScore": edge_score(score_info["score"], score_info["confidence"], 0),
            "RR": 0.0,
            "Entry": entry.get("entry_price"),
            "StopLoss": None,
            "Target1": None,
            "Target2": None,
            "BreakoutLevel": entry.get("breakout_level"),
            "EntryMode": entry.get("mode"),
            "StopModel": self.config.stop_model,
            "TargetModel": self.config.target_model,
            "SetupTypes": [
                key for key, passed in (entry.get("features") or {}).items()
                if isinstance(passed, bool) and passed
            ],
            "Reasons": [reason or entry.get("reason", "NO_BREAKOUT_ENTRY"), *score_info["reasons"]],
            "DecisionTrace": {"Entry": bool(entry.get("eligible")), "Stop": False, "Target": False, "RiskReward": False, "Score": score_info["score"] >= self.config.minimum_score, "Confidence": score_info["confidence"] >= self.config.minimum_confidence},
            "RejectReason": reason or entry.get("reason"),
            "StopCandidates": (stop or {}).get("candidates", {}),
            "TargetCandidates": {},
            "PartialLevels": [],
            "TargetFallbackUsed": False,
            "BreakoutIndex": entry.get("breakout_index"),
            "Config": asdict(self.config),
            "DecisionSupportOnly": True,
        }
