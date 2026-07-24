"""Explainable market classifier for the independent adaptive selector.

The classifier consumes already-calculated strategy outputs and indicator
fields.  It never invokes Classic, BREAKOUT_SWING, or indicator calculators.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Iterable

import pandas as pd


REGIMES = (
    "STRONG_BULL",
    "BULL",
    "WEAK_BULL",
    "SIDEWAYS",
    "HIGH_VOLATILITY",
    "BEAR",
    "PANIC",
    "UNKNOWN",
)


def _number(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _last_feature(row, name, default=0.0):
    features = row.get("AIFeatures")
    if features is not None:
        try:
            return _number(features.get(name), default)
        except AttributeError:
            pass
    return _number(row.get(name), default)


@dataclass(frozen=True)
class MarketSnapshot:
    timestamp: str
    symbols: int
    case30_trend: str
    ema_alignment_ratio: float
    average_adx: float
    average_atr_percent: float
    index_momentum_percent: float
    index_volume_ratio: float
    breadth: float
    advancers: int
    decliners: int
    unchanged: int
    new_highs: int
    new_lows: int
    breakout_frequency: float
    average_rr: float
    average_volume_ratio: float
    gap_frequency: float
    average_trend_score: float
    average_momentum_score: float
    average_confidence: float
    average_edge_score: float
    volatility_expansion: float
    sector_strength: float | None = None
    index_data_available: bool = False


@dataclass(frozen=True)
class MarketClassification:
    regime: str
    confidence: float
    reasons: tuple[str, ...]
    snapshot: MarketSnapshot
    robustness_tags: tuple[str, ...]

    def as_dict(self):
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        value["robustness_tags"] = list(self.robustness_tags)
        return value


class MarketClassifier:
    def __init__(self, settings):
        self.settings = settings

    def from_scan_results(self, rows: Iterable[dict]) -> MarketClassification:
        rows = list(rows)
        valid = [row for row in rows if row.get("Data") is not None and len(row["Data"])]
        if not valid:
            return self.classify(self._empty_snapshot("UNKNOWN"))

        advancers = decliners = unchanged = new_highs = new_lows = 0
        gaps = []
        ema_aligned = []
        adx = []
        atr_percent = []
        volume_ratio = []
        index_regimes = []
        timestamps = []
        for row in valid:
            frame = row["Data"]
            last = frame.iloc[-1]
            previous = frame.iloc[-2] if len(frame) > 1 else last
            close = _number(last.get("Close"))
            previous_close = _number(previous.get("Close"))
            if close > previous_close:
                advancers += 1
            elif close < previous_close:
                decliners += 1
            else:
                unchanged += 1
            prior = frame.iloc[:-1].tail(252)
            if not prior.empty and close > _number(prior["High"].max()):
                new_highs += 1
            if not prior.empty and close < _number(prior["Low"].min()):
                new_lows += 1
            gap = (
                abs(_number(last.get("Open")) / previous_close - 1) * 100
                if previous_close > 0 else 0.0
            )
            gaps.append(gap)
            ema_aligned.append(bool(
                _number(last.get("EMA20")) > _number(last.get("EMA50"))
                > _number(last.get("EMA200"))
            ))
            adx.append(_number(last.get("ADX")))
            atr_percent.append(_number(last.get("ATR_PERCENT")))
            volume_ratio.append(_number(last.get("VOLUME_RATIO")))
            index_regimes.append(str(row.get("IndexRegime") or "UNKNOWN").upper())
            timestamps.append(pd.Timestamp(frame.index[-1]))

        total = len(valid)
        breadth = (advancers - decliners) / total if total else 0.0
        dominant_index = pd.Series(index_regimes).mode()
        case30_trend = str(dominant_index.iloc[0]) if not dominant_index.empty else "UNKNOWN"
        rr_values = [_number(row.get("ClassicRR", row.get("RR"))) for row in rows]
        edge_values = [_number(row.get("BreakoutEdgeScore")) for row in rows]
        breakout_buy = sum(row.get("BreakoutDecision") == "BUY" for row in rows)
        snapshot = MarketSnapshot(
            timestamp=str(max(timestamps)),
            symbols=total,
            case30_trend=case30_trend,
            ema_alignment_ratio=sum(ema_aligned) / total,
            average_adx=sum(adx) / total,
            average_atr_percent=sum(atr_percent) / total,
            index_momentum_percent=0.0,
            index_volume_ratio=0.0,
            breadth=breadth,
            advancers=advancers,
            decliners=decliners,
            unchanged=unchanged,
            new_highs=new_highs,
            new_lows=new_lows,
            breakout_frequency=breakout_buy / total,
            average_rr=sum(rr_values) / len(rr_values) if rr_values else 0.0,
            average_volume_ratio=sum(volume_ratio) / total,
            gap_frequency=sum(
                gap >= self.settings["gap_threshold_percent"] for gap in gaps
            ) / total,
            # Frozen Classic components use 50/20 maxima. Normalize only the
            # already-final component outputs for selector presentation.
            average_trend_score=sum(min(100, _number(row.get("Trend")) * 2) for row in rows) / len(rows),
            average_momentum_score=sum(min(100, _number(row.get("Momentum")) * 5) for row in rows) / len(rows),
            average_confidence=sum(_number(row.get("Confidence")) for row in rows) / len(rows),
            average_edge_score=sum(edge_values) / len(edge_values) if edge_values else 0.0,
            volatility_expansion=1.0,
            sector_strength=None,
            index_data_available=case30_trend not in {"UNKNOWN", "N/A", ""},
        )
        return self.classify(snapshot)

    def from_historical_rows(
        self,
        timestamp,
        rows: Iterable[dict],
        classic_outputs=(),
        breakout_outputs=(),
        index_row=None,
    ) -> MarketClassification:
        """Build one snapshot using only values supplied for that timestamp."""

        rows = list(rows)
        if len(rows) < int(self.settings["minimum_market_symbols"]):
            return self.classify(self._empty_snapshot(str(timestamp), len(rows)))
        advancers = sum(_number(row.get("change_percent")) > 0 for row in rows)
        decliners = sum(_number(row.get("change_percent")) < 0 for row in rows)
        unchanged = len(rows) - advancers - decliners
        classic_outputs = list(classic_outputs)
        breakout_outputs = list(breakout_outputs)
        all_rr = [
            _number(getattr(item, "rr", 0))
            for item in [*classic_outputs, *breakout_outputs]
        ]
        trend_scores = [
            min(100, _number(getattr(item, "trend_score", 0)) * 2)
            for item in classic_outputs
        ]
        momentum_scores = [
            min(100, _number(getattr(item, "momentum_score", 0)) * 5)
            for item in classic_outputs
        ]
        index_available = index_row is not None
        case30_trend = "UNKNOWN"
        index_momentum = index_volume = 0.0
        if index_available:
            case30_trend = str(index_row.get("trend", "UNKNOWN"))
            index_momentum = _number(index_row.get("momentum_percent"))
            index_volume = _number(index_row.get("volume_ratio"))
        else:
            aligned = sum(bool(row.get("ema_aligned")) for row in rows) / len(rows)
            case30_trend = "BULL" if aligned >= 0.5 else (
                "BEAR" if aligned <= 0.25 else "SIDEWAYS"
            )
        snapshot = MarketSnapshot(
            timestamp=str(pd.Timestamp(timestamp)),
            symbols=len(rows),
            case30_trend=case30_trend,
            ema_alignment_ratio=sum(bool(row.get("ema_aligned")) for row in rows) / len(rows),
            average_adx=sum(_number(row.get("adx")) for row in rows) / len(rows),
            average_atr_percent=sum(_number(row.get("atr_percent")) for row in rows) / len(rows),
            index_momentum_percent=index_momentum,
            index_volume_ratio=index_volume,
            breadth=(advancers - decliners) / len(rows),
            advancers=advancers,
            decliners=decliners,
            unchanged=unchanged,
            new_highs=sum(bool(row.get("new_high")) for row in rows),
            new_lows=sum(bool(row.get("new_low")) for row in rows),
            breakout_frequency=len(breakout_outputs) / len(rows),
            average_rr=sum(all_rr) / len(all_rr) if all_rr else 0.0,
            average_volume_ratio=sum(_number(row.get("volume_ratio")) for row in rows) / len(rows),
            gap_frequency=sum(bool(row.get("large_gap")) for row in rows) / len(rows),
            average_trend_score=(
                sum(trend_scores) / len(trend_scores) if trend_scores else 0.0
            ),
            average_momentum_score=(
                sum(momentum_scores) / len(momentum_scores) if momentum_scores else 0.0
            ),
            average_confidence=(
                sum(_number(getattr(item, "confidence", 0)) for item in [*classic_outputs, *breakout_outputs])
                / max(len(classic_outputs) + len(breakout_outputs), 1)
            ),
            average_edge_score=(
                sum(_number(getattr(item, "ai_rank", 0)) for item in breakout_outputs)
                / max(len(breakout_outputs), 1)
            ),
            volatility_expansion=sum(_number(row.get("volatility_expansion"), 1.0) for row in rows) / len(rows),
            sector_strength=None,
            index_data_available=index_available,
        )
        return self.classify(snapshot)

    def classify(self, snapshot: MarketSnapshot) -> MarketClassification:
        s = self.settings
        reasons = []
        if snapshot.symbols < int(s["minimum_market_symbols"]):
            regime = "UNKNOWN"
            confidence = 0.0
            reasons.append(
                f"Only {snapshot.symbols} symbols; minimum is {s['minimum_market_symbols']}"
            )
        elif (
            snapshot.breadth <= s["panic_breadth"]
            or snapshot.index_momentum_percent <= s["panic_index_momentum_percent"]
        ) and snapshot.volatility_expansion >= s["high_volatility_expansion"]:
            regime = "PANIC"
            confidence = 90.0
            reasons.extend(["Severely negative breadth/momentum", "Volatility expansion"])
        elif (
            snapshot.volatility_expansion >= s["high_volatility_expansion"]
            or snapshot.gap_frequency >= s["high_gap_frequency"]
        ):
            regime = "HIGH_VOLATILITY"
            confidence = 80.0
            reasons.append("Volatility or gap frequency is above the declared baseline")
        elif snapshot.case30_trend == "BEAR" or snapshot.breadth <= s["bear_breadth"]:
            regime = "BEAR"
            confidence = 80.0
            reasons.append("CASE30/market proxy is bearish or breadth is materially negative")
        elif (
            snapshot.case30_trend == "BULL"
            and snapshot.breadth >= s["strong_bull_breadth"]
            and snapshot.ema_alignment_ratio >= s["strong_ema_alignment_ratio"]
            and snapshot.average_adx >= s["strong_adx"]
            and snapshot.average_momentum_score >= s["strong_momentum_score"]
        ):
            regime = "STRONG_BULL"
            confidence = 90.0
            reasons.extend(["Broad advance", "Strong EMA alignment", "Strong ADX and momentum"])
        elif (
            snapshot.case30_trend == "BULL"
            and snapshot.breadth >= s["bull_breadth"]
            and snapshot.ema_alignment_ratio >= s["bull_ema_alignment_ratio"]
        ):
            regime = "BULL"
            confidence = 80.0
            reasons.extend(["Positive breadth", "Bullish market/index alignment"])
        elif snapshot.case30_trend == "BULL" or snapshot.breadth > s["weak_bull_breadth"]:
            regime = "WEAK_BULL"
            confidence = 65.0
            reasons.append("Positive conditions lack broad/strong confirmation")
        else:
            regime = "SIDEWAYS"
            confidence = 70.0
            reasons.append("No confirmed directional breadth and trend alignment")

        if not snapshot.index_data_available:
            reasons.append("CASE30 history unavailable; cross-sectional proxy used")
            confidence = max(0.0, confidence - 10.0)
        tags = self._robustness_tags(snapshot, regime)
        return MarketClassification(regime, confidence, tuple(reasons), snapshot, tags)

    def _robustness_tags(self, snapshot, regime):
        tags = [regime]
        if snapshot.volatility_expansion >= self.settings["high_volatility_expansion"]:
            tags.append("HIGH_VOLATILITY")
        elif snapshot.volatility_expansion <= self.settings["low_volatility_expansion"]:
            tags.append("LOW_VOLATILITY")
        if regime == "STRONG_BULL":
            tags.append("STRONG_RALLY")
        if regime in {"PANIC", "BEAR"} and snapshot.breadth <= self.settings["bear_breadth"]:
            tags.append("SHARP_CORRECTION")
        date = pd.Timestamp(snapshot.timestamp)
        for period in self.settings.get("election_periods", []):
            if pd.Timestamp(period["start"]) <= date <= pd.Timestamp(period["end"]):
                tags.append("ELECTION_PERIOD")
        return tuple(dict.fromkeys(tags))

    @staticmethod
    def _empty_snapshot(timestamp, symbols=0):
        return MarketSnapshot(
            timestamp=str(timestamp), symbols=symbols, case30_trend="UNKNOWN",
            ema_alignment_ratio=0, average_adx=0, average_atr_percent=0,
            index_momentum_percent=0, index_volume_ratio=0, breadth=0,
            advancers=0, decliners=0, unchanged=0, new_highs=0, new_lows=0,
            breakout_frequency=0, average_rr=0, average_volume_ratio=0,
            gap_frequency=0, average_trend_score=0, average_momentum_score=0,
            average_confidence=0, average_edge_score=0,
            volatility_expansion=1, sector_strength=None,
            index_data_available=False,
        )
