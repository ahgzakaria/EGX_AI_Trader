from config.settings_manager import settings

from backtesting.engine import BacktestEngine
from backtesting.statistics import BacktestStatistics
from core.symbols import load_symbols
from strategy.trading_decision import TradingDecisionService


class StrategyEvaluator:

    def __init__(self):

        settings.reload()

        self.symbols = load_symbols("data/symbols.csv")
        self.decision_service = TradingDecisionService()

        # نحفظ نسخة من إعدادات الاستراتيجية الأصلية عشان
        # نرجعها زي ما كانت بعد ما التجريب يخلص (الميثود دي
        # بتلعب فى الإعدادات "فى الذاكرة" بس، مش بتكتب على
        # settings.json خالص، عشان الأداء ومنعًا لتخريب
        # الملف بمليون قيمة أثناء الـ Grid Search).

        self._original_strategy = dict(

            settings.get("strategy")

        )

    # ==================================

    def evaluate(

        self,

        params

    ):

        # ---------------------------------
        # Apply Parameters (In-Memory Only)
        # ---------------------------------
        # بما إن strategy/config.py بقى بيقرا الإعدادات Live
        # من settings.data فى كل نداء لـ signal_engine، أبسط
        # طريقة نجرب بيها باراميترات مختلفة هي إننا نعدّل
        # القاموس نفسه فى الذاكرة مباشرة (من غير .set() اللي
        # بتكتب على القرص فى كل تجربة).
        # ---------------------------------

        strategy = settings.get("strategy")

        strategy["min_score"] = params.score
        strategy["min_confidence"] = params.confidence
        strategy["min_rr"] = params.rr
        strategy["min_trend"] = params.trend
        strategy["min_momentum"] = params.momentum
        strategy["min_volume"] = params.volume

        # ---------------------------------

        trades = []

        successful = 0

        failed = 0

        # ---------------------------------

        for symbol in self.symbols:

            try:

                engine = BacktestEngine(
                    symbol,
                    decision_service=self.decision_service
                )

                trades.extend(

                    engine.run()

                )

                successful += 1

            except Exception:

                failed += 1

        # ---------------------------------

        if len(trades) == 0:

            return None

        # ملحوظة: الصفقات هنا جايه مباشرة من BacktestEngine
        # (من غير PortfolioSimulator)، فـ "executed" بتاعها
        # False بالـ default. هنا بنقيّمها كلها كـ "صفقات
        # مرشحة" بمعزل عن قيود رأس المال، عشان الهدف من
        # الـ Optimizer مقارنة جودة الإشارات نفسها، مش محاكاة
        # محفظة حقيقية.

        for trade in trades:
            trade.executed = True

        summary = BacktestStatistics(

            trades,

            profit_field="profit"

        ).summary()

        # ---------------------------------

        return {

            "Score": params.score,

            "Confidence": params.confidence,

            "RR": params.rr,

            "Trend": params.trend,

            "Momentum": params.momentum,

            "Volume": params.volume,

            "Trades": len(trades),

            "Successful": successful,

            "Failed": failed,

            "WinRate": summary["WinRate"],

            "ProfitFactor": summary["ProfitFactor"],

            "NetProfit": summary["NetProfit"],

            "Expectancy": summary["Expectancy"],

            "MaxDrawdown": summary["MaxDrawdown"]

        }

    # ==================================
    # Restore Original Settings
    # ==================================
    # لازم تتنادى بعد ما التجريب كله يخلص، عشان ترجع
    # settings.json (فى الذاكرة) لآخر حاجة كانت محفوظة قبل
    # ما نبدأ نجرب باراميترات مختلفة.

    def restore(self):

        settings.data["strategy"] = self._original_strategy
