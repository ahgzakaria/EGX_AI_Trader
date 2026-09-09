import math
from datetime import datetime

from backtesting.equity import CLOSED_TRADE, EquityCurve


class BacktestStatistics:

    def __init__(
        self,
        trades,
        initial_capital=100000,
        profit_field="portfolio_profit",
        prices=None,
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

        #: Daily closes double as the benchmark source: `prices` is what makes
        #: `BenchmarkReturn` computable at all. Without it that field reports
        #: `None`, never zero -- an unmeasured benchmark is not a flat market.
        #:
        #: Daily closes for the traded symbols, from `backtesting/prices.py`.
        #: With them, `MaxDrawdown` marks open positions to market. Without
        #: them it is the realised, closed-trade figure this project reported
        #: until 2026-08-29 -- which is not wrong, but is a different quantity,
        #: so `DrawdownBasis` in the summary always says which one it is.
        self.prices = prices

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
            self.profit_field,
            prices=self.prices,
        )

        # `max_drawdown` marks open positions to market when prices are
        # available. The closed-trade figure is reported beside it rather than
        # replaced by it, because every number this project has published so
        # far is that one, and a metric that silently changed meaning between
        # two runs of the same code would be worse than the defect.
        max_drawdown = equity.max_drawdown()
        max_drawdown_amount = equity.max_drawdown_amount()
        closed_trade_drawdown = equity.closed_trade_max_drawdown()
        drawdown_basis = equity.basis()

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

        benchmark = self._benchmark(total_days)

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
            "MaxDrawdownClosedTrades": closed_trade_drawdown,
            "DrawdownBasis": drawdown_basis,

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
            "BacktestDays": total_days,

            # ==================================
            # What not trading was worth
            # ==================================
            # No summary this project produced had one. The Daily Dashboard
            # strategy reports profit factor 1.29 and CAGR 5.43% over a window
            # in which the median EGX name returned 17.51% a year simply held,
            # and nothing on the page said so.
            #
            # `None` where it could not be computed, never 0.

            "BenchmarkReturn": benchmark.get("TotalReturn"),
            "BenchmarkCAGR": benchmark.get("CAGR"),
            "BenchmarkSymbols": benchmark.get("Symbols"),
            "ExcessReturn": (
                round(total_return - benchmark["TotalReturn"], 2)
                if benchmark.get("TotalReturn") is not None else None
            ),

            # Equal-weight buy-and-hold of the same traded names, and the
            # strategy's return against it. Untradable as a portfolio, and
            # reported anyway: it is the number that says whether a rule which
            # is in the market a quarter of the time beat simply owning it.
            "BenchmarkEqualWeightReturn": benchmark.get("EqualWeightReturn"),
            "BenchmarkEqualWeightCAGR": benchmark.get("EqualWeightCAGR"),
            "ExcessReturnVsEqualWeight": (
                round(total_return - benchmark["EqualWeightReturn"], 2)
                if benchmark.get("EqualWeightReturn") is not None else None
            ),

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
            "MaxDrawdownClosedTrades": 0, "DrawdownBasis": CLOSED_TRADE,
            "BenchmarkReturn": None, "BenchmarkCAGR": None,
            "BenchmarkSymbols": None, "ExcessReturn": None,
            "BenchmarkEqualWeightReturn": None,
            "BenchmarkEqualWeightCAGR": None,
            "ExcessReturnVsEqualWeight": None,
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

    def _benchmark(self, total_days):
        """Buy and hold the median traded name over the same window.

        The median rather than the mean, and a single held position rather than
        a rebalanced basket: that is what an investor actually does, and it is a
        geometric return on one decision. A daily-rebalanced equal-weighted
        index over this universe returns several times more and nobody could
        trade it.

        Measured only over symbols this run actually traded, between the first
        entry and the last exit, so it answers "what would holding these names
        have paid" rather than "what did some other universe do".

        Returns empty when there are no prices. An unmeasured benchmark reports
        as unknown, not as a flat market -- the same rule the annualised
        statistics follow when the span cannot be determined.
        """
        if self.prices is None or not len(self.prices) or not self.trades:
            return {}
        try:
            start = min(datetime.strptime(t.entry_date, "%Y-%m-%d")
                        for t in self.trades)
            end = max(datetime.strptime(t.exit_date, "%Y-%m-%d")
                      for t in self.trades)
        except (AttributeError, TypeError, ValueError):
            return {}

        window = self.prices.loc[
            (self.prices.index >= start) & (self.prices.index <= end)]
        if len(window) < 2:
            return {}

        returns = []
        for symbol in window.columns:
            series = window[symbol].dropna()
            if len(series) < 2 or series.iloc[0] <= 0:
                continue
            returns.append((series.iloc[-1] / series.iloc[0] - 1) * 100)
        if not returns:
            return {}

        returns.sort()
        middle = len(returns) // 2
        median = (returns[middle] if len(returns) % 2
                  else (returns[middle - 1] + returns[middle]) / 2)
        years = (total_days / 365.25) if total_days else 0
        cagr = (round(((1 + median / 100) ** (1 / years) - 1) * 100, 2)
                if years > 0 and median > -100 else None)

        # The second reading: an equal-weighted basket of the same names,
        # rebalanced daily. It is not tradable -- rebalancing 200 EGX names
        # every session is a cost nobody would pay -- and it returns several
        # times the median, which is why the median is the headline and this
        # sits beside it. It is reported because it is the index-like number,
        # the one a strategy that spends most of its time in cash is really
        # competing against, and leaving it out let "beats the median name"
        # stand in for "beats the market".
        equal_weight = None
        equal_weight_cagr = None
        daily = window.pct_change()
        if len(daily) > 1:
            basket = daily.mean(axis=1, skipna=True).fillna(0.0)
            growth = float((1.0 + basket).prod())
            if growth > 0:
                equal_weight = round((growth - 1) * 100, 2)
                if years > 0:
                    equal_weight_cagr = round(
                        ((growth) ** (1 / years) - 1) * 100, 2)

        return {"TotalReturn": round(median, 2), "CAGR": cagr,
                "Symbols": len(returns),
                "EqualWeightReturn": equal_weight,
                "EqualWeightCAGR": equal_weight_cagr}

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