"""Walk-forward success probabilities and independent Strategy Edge Score."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path


STRATEGIES = ("CLASSIC", "BREAKOUT_SWING")


def _number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class StrategyProbability:
    strategy: str
    regime: str
    probability: float
    samples: int
    wins: int
    losses: int
    average_return_percent: float


class WalkForwardPerformanceLedger:
    """Expanding performance memory; only completed earlier trades are added."""

    def __init__(self, settings, initial=None):
        self.settings = settings
        self.stats = defaultdict(lambda: {
            "samples": 0, "wins": 0, "losses": 0, "return_sum": 0.0,
        })
        for key, value in (initial or {}).items():
            strategy, regime = key.split("|", 1)
            self.stats[(strategy, regime)].update(value)

    def observe(self, strategy, regime, return_percent):
        strategy = str(strategy).upper()
        regime = str(regime).upper()
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown selector strategy: {strategy}")
        value = self.stats[(strategy, regime)]
        value["samples"] += 1
        value["return_sum"] += float(return_percent)
        if float(return_percent) > 0:
            value["wins"] += 1
        else:
            value["losses"] += 1

    def probability(self, strategy, regime):
        strategy = str(strategy).upper()
        regime = str(regime).upper()
        direct = self.stats[(strategy, regime)]
        # Sparse regimes use only neutral Beta priors; no future or global
        # result is substituted into an earlier selection.
        successes = float(self.settings["prior_successes"]) + direct["wins"]
        failures = float(self.settings["prior_failures"]) + direct["losses"]
        probability = successes / (successes + failures)
        return StrategyProbability(
            strategy=strategy,
            regime=regime,
            probability=round(probability, 6),
            samples=int(direct["samples"]),
            wins=int(direct["wins"]),
            losses=int(direct["losses"]),
            average_return_percent=round(
                direct["return_sum"] / direct["samples"], 6
            ) if direct["samples"] else 0.0,
        )

    def export(self):
        return {
            f"{strategy}|{regime}": dict(values)
            for (strategy, regime), values in sorted(self.stats.items())
            if values["samples"]
        }

    @classmethod
    def from_json(cls, settings, path):
        path = Path(path)
        if not path.is_file():
            return cls(settings)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(settings, payload.get("performance", payload))


def output_quality(strategy, output):
    if strategy == "CLASSIC":
        score = _number(output.get("Score", output.get("ClassicScore")))
        confidence = _number(output.get("Confidence", output.get("ClassicConfidence")))
        rr = _number(output.get("RR", output.get("ClassicRR")))
        return min(100.0, score * 0.55 + confidence * 0.25 + min(rr / 3, 1) * 20)
    score = _number(output.get("Score", output.get("BreakoutScore")))
    confidence = _number(output.get("Confidence", output.get("BreakoutConfidence")))
    rr = _number(output.get("RR", output.get("BreakoutRR")))
    edge = _number(output.get("EdgeScore", output.get("BreakoutEdgeScore")))
    return min(100.0, score * 0.35 + confidence * 0.15 + edge * 0.30 + min(rr / 3, 1) * 20)


def strategy_edge_score(probability, quality, settings):
    """0-100 selection score; it never changes either strategy output."""

    value = (
        float(probability) * 100 * float(settings["probability_edge_weight"])
        + float(quality) * float(settings["output_quality_weight"])
    )
    return round(max(0.0, min(100.0, value)), 2)


def probability_records(ledger):
    records = []
    regimes = sorted({regime for _, regime in ledger.stats})
    for regime in regimes:
        for strategy in STRATEGIES:
            records.append(asdict(ledger.probability(strategy, regime)))
    return records
