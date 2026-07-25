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
    # Original Indicators
    # ==========================

    rsi: float
    adx: float
    atr: float
    macd: float

    # ==========================
    # AI Features V1
    # ==========================

    ema20_dist: float
    ema50_dist: float
    ema200_dist: float

    volume_ratio: float

    atr_percent: float

    bb_position: float

    obv: float

    # ==========================
    # AI Features V2
    # ==========================

    rsi7: float

    ema20_slope: float
    ema50_slope: float

    rsi_slope: float

    adx_rising: float

    bb_width: float

    obv_slope: float

    dist_high20: float
    dist_low20: float

    # ==========================
    # AI Features V3
    # ==========================

    macd_cross_age: int

    # ==========================

    reasons: str

    # Final-decision audit fields.  They let comparison reports trace every
    # trade to the exact signal date, regime and out-of-sample AI decision.
    signal_date: str = ""
    regime: str = ""
    ai_probability: float | None = None
    ai_approved: bool = True
    ai_multiplier: float = 1.0
    ai_rank: float = 0.0
    ai_rejection_reason: str = ""
    ai_mode: str = "STRATEGY_ONLY"

    # ==========================
    # Calculated
    # ==========================

    holding_days: int = field(init=False)

    risk_per_share: float = field(init=False)

    reward_per_share: float = field(init=False)

    profit_percent: float = field(init=False)

    r_multiple: float = field(init=False)

    # ==========================
    # Portfolio Simulation
    # ==========================
    # الحقول دي بتتملى لاحقًا بمعرفة PortfolioSimulator
    # (backtesting/trade.py نفسه معندوش أي فكرة عن رأس المال
    # أو باقي الصفقات المتزامنة، فبتفضل بالقيم الافتراضية دي
    # لحد ما الـ simulator يشتغل عليها).
    # ==========================

    shares: int = field(init=False, default=0)

    portfolio_profit: float = field(init=False, default=0.0)

    executed: bool = field(init=False, default=False)

    risk_amount: float = field(init=False, default=0.0)

    # Explicit final size makes every overlay decision auditable, including
    # portfolio-rejected candidates whose final size remains zero.
    final_position_size: int = field(init=False, default=0)
    portfolio_rejection_reason: str = field(init=False, default="")

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
