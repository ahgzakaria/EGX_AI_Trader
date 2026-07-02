from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Trade:

    # ==========================
    # Basic Information
    # ==========================

    symbol: str

    entry_date: str
    exit_date: str

    entry_price: float
    exit_price: float

    stop_loss: float

    target1: float
    target2: float

    rr: float

    result: str
    exit_reason: str

    profit: float

    # ==========================
    # Strategy Scores
    # ==========================

    score: int
    confidence: int

    trend_score: int
    volume_score: int
    momentum_score: int
    candle_score: int
    breakout_score: int

    # ==========================
    # Raw Indicators
    # ==========================

    rsi: float
    adx: float
    atr: float
    macd: float

    # ==========================
    # AI Features
    # ==========================

    ema20_dist: float
    ema50_dist: float
    ema200_dist: float

    volume_ratio: float

    atr_percent: float

    bb_position: float

    obv: float

    # ==========================

    reasons: str

    # ==========================
    # Calculated Fields
    # ==========================

    holding_days: int = field(init=False)

    risk_per_share: float = field(init=False)

    reward_per_share: float = field(init=False)

    profit_percent: float = field(init=False)

    r_multiple: float = field(init=False)

    # ==========================

    def __post_init__(self):

        try:

            entry = datetime.strptime(
                self.entry_date,
                "%Y-%m-%d"
            )

            exit = datetime.strptime(
                self.exit_date,
                "%Y-%m-%d"
            )

            self.holding_days = (
                exit - entry
            ).days

        except Exception:

            self.holding_days = 0

        self.risk_per_share = round(
            abs(
                self.entry_price -
                self.stop_loss
            ),
            2
        )

        self.reward_per_share = round(
            self.exit_price -
            self.entry_price,
            2
        )

        if self.entry_price > 0:

            self.profit_percent = round(
                (
                    self.profit /
                    self.entry_price
                ) * 100,
                2
            )

        else:

            self.profit_percent = 0

        if self.risk_per_share > 0:

            self.r_multiple = round(
                self.reward_per_share /
                self.risk_per_share,
                2
            )

        else:

            self.r_multiple = 0