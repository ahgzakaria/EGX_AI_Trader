import math
from datetime import datetime

from backtesting.equity import EquityCurve


class BacktestStatistics:

    def __init__(
        self,
        trades,
        initial_capital=100000,
        profit_field="portfolio_profit"
    ):

        # ==================================
        # هنا بنستقبل الصفقات "المنفذة فعليًا" بس
        # (اللي رجعت من PortfolioSimulator بـ executed=True).
        # ==================================

        self.trades = [
            t for t in trades
            if getattr(t, "executed", True)
        ]

        self.initial_capital = initial_capital

        self.profit_field = profit_field

        #: Why the backtest's span could not be measured, when it could not.
        #: Empty on a normal run. Read it rather than guessing why six of the
        #: reported statistics came back as None.
        self.total_days_error = ""

    # ==================================
    # Summary
    # ==================================

    def summary(self):

        total_trades = len(self.trades)

        if total_trades == 0:
            return self._empty_summary()

        def p(trade):
            return getattr(trade, self.profit_field)

        wins = sum(1 for t in self.trades if t.result == "WIN")
        losses = sum(1 for t in self.trades if t.result == "LOSS")
        breakeven = sum(1 for t in self.trades if t.result == "BREAKEVEN")

        win_rate = round(wins / total_trades * 100, 2)

        net_profit = round(sum(p(t) for t in self.trades), 2)

        gross_profit = round(
            sum(p(t) for t in self.trades if p(t) > 0), 2
        )

        gross_loss = round(
            abs(sum(p(t) for t in self.trades if p(t) < 0)), 2
        )

        profit_factor = (
            round(gross_profit / gross_loss, 2)
            if gross_loss else 0
        )

        average_win = round(gross_profit / wins, 2) if wins else 0
        average_loss = round(gross_loss / losses, 2) if losses else 0

        expectancy = round(net_profit / total_trades, 2)

        average_holding = round(
            sum(t.holding_days for t in self.trades) / total_trades,
            2
        )

        average_r = round(
            sum(t.r_multiple for t in self.trades) / total_trades,
            2
        )

        average_profit_percent = round(
            sum(t.profit_percent for t in self.trades) / total_trades,
            2
        )

        best_trade = max(self.trades, key=lambda t: p(t))
        worst_trade = min(self.trades, key=lambda t: p(t))

        equity = EquityCurve(
            self.trades,
            self.initial_capital,
            self.profit_field
        )

        max_drawdown = equity.max_drawdown()
        max_drawdown_amount = equity.max_drawdown_amount()

        final_capital = equity.final_capital()
        total_return = equity.total_return()

        # ==================================
        # المقاييس الاحترافية الجديدة
        # ==================================

        max_consecutive_wins, max_consecutive_losses = (
            self._consecutive_streaks()
        )

        total_days = self._total_days()

        # Everything below this line is annualised, so all of it depends on
        # knowing how long the backtest ran. When that is unknown, each of
        # these reports as unknown rather than as a number derived from a
        # guess -- an unmeasured Sharpe is not a small Sharpe.
        if total_days is None:
            trades_per_year = sharpe = sortino = cagr = calmar = None
        else:
            trades_per_year = (
                round(total_trades / (total_days / 365.25), 2)
                if total_days > 0
                else total_trades
            )

            sharpe = self._sharpe_ratio(trades_per_year)
            sortino = self._sortino_ratio(trades_per_year)

            cagr = self._cagr(final_capital, total_days)

            calmar = (
                round(cagr / max_drawdown, 2)
                if max_drawdown > 0
                else 0
            )

        recovery_factor = (
            round(net_profit / max_drawdown_amount, 2)
            if max_drawdown_amount > 0
            else 0
        )

        kelly_percent = self._kelly_percent(
            win_rate / 100,
            average_win,
            average_loss
        )

        exposure_percent = (None if total_days is None
                            else self._exposure_percent(total_days))

        return {

            "Trades": total_trades,
            "Wins": wins,
            "Losses": losses,
            "BreakEven": breakeven,
            "WinRate": win_rate,

            "NetProfit": net_profit,
            "GrossProfit": gross_profit,
            "GrossLoss": gross_loss,
            "ProfitFactor": profit_factor,

            "AverageWin": average_win,
            "AverageLoss": average_loss,
            "Expectancy": expectancy,

            "AverageHoldingDays": average_holding,
            "AverageR": average_r,
            "AverageProfitPercent": average_profit_percent,

            "BestTrade": p(best_trade),
            "WorstTrade": p(worst_trade),
            "LargestWinner": best_trade.symbol,
            "LargestLoser": worst_trade.symbol,

            "InitialCapital": self.initial_capital,
            "FinalCapital": final_capital,
            "TotalReturn": total_return,
            "MaxDrawdown": max_drawdown,
            "MaxDrawdownAmount": max_drawdown_amount,

            # ==================================
            # Professional Metrics
            # ==================================

            "MaxConsecutiveWins": max_consecutive_wins,
            "MaxConsecutiveLosses": max_consecutive_losses,

            "SharpeRatio": sharpe,
            "SortinoRatio": sortino,
            "CalmarRatio": calmar,

            "CAGR": cagr,
            "RecoveryFactor": recovery_factor,
            "KellyPercent": kelly_percent,
            "ExposurePercent": exposure_percent,

            "TradesPerYear": trades_per_year,
            "BacktestDays": total_days

        }

    # ==================================
    # Empty Summary (لو مفيش صفقات خالص)
    # ==================================

    def _empty_summary(self):

        return {

            "Trades": 0, "Wins": 0, "Losses": 0, "BreakEven": 0,
            "WinRate": 0, "NetProfit": 0, "GrossProfit": 0,
            "GrossLoss": 0, "ProfitFactor": 0, "AverageWin": 0,
            "AverageLoss": 0, "Expectancy": 0, "AverageHoldingDays": 0,
            "AverageR": 0, "AverageProfitPercent": 0, "BestTrade": 0,
            "WorstTrade": 0, "LargestWinner": "", "LargestLoser": "",
            "InitialCapital": self.initial_capital,
            "FinalCapital": self.initial_capital, "TotalReturn": 0,
            "MaxDrawdown": 0, "MaxDrawdownAmount": 0,
            "MaxConsecutiveWins": 0, "MaxConsecutiveLosses": 0,
            "SharpeRatio": 0, "SortinoRatio": 0, "CalmarRatio": 0,
            "CAGR": 0, "RecoveryFactor": 0, "KellyPercent": 0,
            "ExposurePercent": 0, "TradesPerYear": 0, "BacktestDays": 0

        }

    # ==================================
    # Max Consecutive Wins / Losses
    # ==================================
    # بنرتب الصفقات بتاريخ الخروج (ترتيب حدوث النتيجة الفعلي)
    # ونعد أطول سلسلة متتالية من الفوز وأطول سلسلة من الخسارة.
    # ==================================

    def _consecutive_streaks(self):

        ordered = sorted(
            self.trades,
            key=lambda t: t.exit_date
        )

        max_wins = current_wins = 0
        max_losses = current_losses = 0

        for trade in ordered:

            if trade.result == "WIN":

                current_wins += 1
                current_losses = 0

            elif trade.result == "LOSS":

                current_losses += 1
                current_wins = 0

            else:

                current_wins = 0
                current_losses = 0

            max_wins = max(max_wins, current_wins)
            max_losses = max(max_losses, current_losses)

        return max_wins, max_losses

    # ==================================
    # إجمالي عدد الأيام اللي غطاها الباك تيست
    # (من أول دخول لآخر خروج)
    # ==================================

    def _total_days(self):
        """Days from the first entry to the last exit, or ``None``.

        ``None`` means the span could not be determined, and it is not the same
        as one day. This used to return 1 on any failure, silently, and 1 is
        the most damaging value it could have picked: it divides into every
        annualised figure this class reports.

        With 500 trades and a malformed date, ``trades_per_year`` became
        500 / (1 / 365.25) = 182,625, and a Sharpe that should read near 1.0
        was inflated roughly sixty-fold. CAGR, Calmar and exposure were
        annualised over a single day. Six reported statistics, all invented,
        none of them flagged.

        The caller now reports every one of them as unknown instead.
        """
        if not self.trades:
            return None

        try:
            entries = [
                datetime.strptime(t.entry_date, "%Y-%m-%d")
                for t in self.trades
            ]

            exits = [
                datetime.strptime(t.exit_date, "%Y-%m-%d")
                for t in self.trades
            ]
        except (TypeError, ValueError) as error:
            # Narrow on purpose: a malformed or missing date is the failure
            # this is guarding, and anything else here is a bug that should
            # surface rather than be absorbed into a missing statistic.
            self.total_days_error = f"{type(error).__name__}: {error}"
            return None

        days = (max(exits) - min(entries)).days
        return max(days, 1)

    # ==================================
    # Sharpe Ratio (مبني على عائد % كل صفقة، مُقارَب سنويًا
    # بعدد الصفقات فى السنة - مش يومي زي الأسهم العادية،
    # لأن صفقاتنا مش متباعدة بانتظام زمني)
    # ==================================

    def _sharpe_ratio(self, trades_per_year):

        returns = [t.profit_percent for t in self.trades]

        n = len(returns)

        if n < 2:
            return 0

        mean = sum(returns) / n

        variance = sum(
            (r - mean) ** 2 for r in returns
        ) / (n - 1)

        std = math.sqrt(variance)

        if std == 0:
            return 0

        return round(

            (mean / std) * math.sqrt(trades_per_year),

            2

        )

    # ==================================
    # Sortino Ratio (زي Sharpe بس بيعاقب بس على التذبذب
    # السلبي - الخسائر فقط، مش أي تذبذب)
    # ==================================

    def _sortino_ratio(self, trades_per_year):

        returns = [t.profit_percent for t in self.trades]

        n = len(returns)

        if n < 2:
            return 0

        mean = sum(returns) / n

        downside_returns = [r for r in returns if r < 0]

        if not downside_returns:
            return 0

        downside_variance = sum(
            r ** 2 for r in downside_returns
        ) / n

        downside_std = math.sqrt(downside_variance)

        if downside_std == 0:
            return 0

        return round(

            (mean / downside_std) * math.sqrt(trades_per_year),

            2

        )

    # ==================================
    # CAGR (Compound Annual Growth Rate) - بنستخدمها فى Calmar
    # ==================================

    def _cagr(self, final_capital, total_days):

        if total_days <= 0 or self.initial_capital <= 0:
            return 0

        years = total_days / 365.25

        if years <= 0:
            return 0

        ratio = final_capital / self.initial_capital

        if ratio <= 0:
            return 0

        return round(

            (ratio ** (1 / years) - 1) * 100,

            2

        )

    # ==================================
    # Kelly % - أد إيه من رأس المال "رياضيًا" المفروض تخاطر
    # بيه كل صفقة (مش نصيحة تنفيذية، مؤشر نظري بس)
    # ==================================

    def _kelly_percent(self, win_rate, average_win, average_loss):

        if average_loss <= 0 or average_win <= 0:
            return 0

        rr_ratio = average_win / average_loss

        kelly = win_rate - ((1 - win_rate) / rr_ratio)

        return round(kelly * 100, 2)

    # ==================================
    # Exposure % - نسبة استغلال رأس المال عبر الوقت (لو
    # كانت أعلى من 100%، معناها كان فيه صفقات متزامنة
    # بتستخدم قيمة أكبر من رأس المال الأساسي فى نفس اللحظة)
    # ==================================

    def _exposure_percent(self, total_days):
        """Share of capital-days actually deployed. ``None`` when unknowable.

        The caller does not pass ``None`` -- it reports unknown itself -- but
        the guard stays honest about what a zero here would mean: no exposure,
        which is a very different claim from an unmeasured one.
        """
        if total_days is None:
            return None

        if total_days <= 0 or self.initial_capital <= 0:
            return 0

        capital_days_used = sum(

            t.shares * t.entry_price * t.holding_days
            for t in self.trades

        )

        return round(

            capital_days_used
            / (self.initial_capital * total_days)
            * 100,

            2

        )

    # ==================================
    # Exit Reasons
    # ==================================

    def exit_reasons(self):

        reasons = {}

        for trade in self.trades:

            reason = trade.exit_reason

            if reason not in reasons:
                reasons[reason] = 0

            reasons[reason] += 1

        return dict(
            sorted(
                reasons.items(),
                key=lambda x: x[1],
                reverse=True
            )
        )

    # ==================================
    # Rejected Trades (تشخيصي فقط)
    # ==================================

    @staticmethod
    def rejected_summary(rejected_trades):

        return {
            "RejectedTrades": len(rejected_trades),
            "RejectedSymbols": sorted(
                {t.symbol for t in rejected_trades}
            )
        }