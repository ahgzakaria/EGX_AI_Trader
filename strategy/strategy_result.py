from dataclasses import dataclass, field


@dataclass
class StrategyResult:

    # Score
    score: int = 0
    confidence: int = 0

    # Reasons
    reasons: list = field(default_factory=list)

    # Trend Parts
    trend: int = 0
    volume: int = 0
    momentum: int = 0
    candles: int = 0
    breakout: int = 0

    # Price Levels
    support: float = 0
    resistance: float = 0

    buy_low: float = 0
    buy_high: float = 0

    stop_loss: float = 0

    target1: float = 0
    target2: float = 0

    rr: float = 0

    # Final Signal
    signal: str = ""
    stars: int = 0