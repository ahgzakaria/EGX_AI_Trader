import os

import pandas as pd

from core.data_provider import load_history
from indicators.technical import calculate_indicators
from core.market_data import MarketData

from backtesting.config import load as load_backtest_config
from backtesting.context import BacktestContext
from backtesting.costs import TradingCosts
from backtesting.managers.exit_manager import ExitManager


PAPER_TRADES_FILE = "data/paper_trades.csv"

COLUMNS = [

    "Symbol",
    "SignalDate",

    "BuyLow",

    "EntryPrice",
    "EntryDate",
    "StopLoss",
    "Target1",
    "Target2",

    "Score",
    "Confidence",
    "RR",
    "AIProbability",

    "Status",

    "ExitDate",
    "ExitPrice",
    "ExitReason",
    "HoldingDays"

]


class PaperTradingTracker:
    """
    سجل متابعة حي لإشارات BUY الحقيقية اللي بتطلع من Scan Market.

    الفكرة: كل مرة يظهر سهم BUY، بيتسجل هنا بسعره وأهدافه وقتها.
    وفي كل Scan جديد، بنتأكد هل أي صفقة مفتوحة قديمة ضربت الهدف
    أو الستوب أو خلصت مدتها القصوى (نفس منطق الـ backtest بالظبط:
    MAX_HOLDING_DAYS و EXIT_MODE من نفس الإعدادات).

    ده مختلف تمامًا عن الـ Backtest التاريخي: هنا كل صفقة اتسجلت
    فعلاً في لحظة حقيقية وبتتابع بيانات حقيقية بعدها بالوقت.
    """

    def __init__(self):

        os.makedirs("data", exist_ok=True)

        if not os.path.exists(PAPER_TRADES_FILE):

            pd.DataFrame(columns=COLUMNS).to_csv(

                PAPER_TRADES_FILE,

                index=False,

                encoding="utf-8-sig"

            )

    # ==================================
    # Load / Save
    # ==================================

    def load(self):

        df = pd.read_csv(

            PAPER_TRADES_FILE,

            encoding="utf-8-sig",

            # الأعمدة دي بتتسجل فاضية الأول وبعدين تتملى بقيم
            # مختلطة (نص/رقم)، فلازم نجبر النوع "object" من
            # الأول عشان pandas ميفترضش إنها float ويرفض بعد
            # كده أي محاولة نحط فيها نص.
            dtype={

                "ExitDate": object,
                "ExitPrice": object,
                "ExitReason": object,
                "HoldingDays": object

            }

        )

        for col in COLUMNS:

            if col not in df.columns:
                df[col] = None

        return df

    def save(self, df):

        df.to_csv(

            PAPER_TRADES_FILE,

            index=False,

            encoding="utf-8-sig"

        )

    # ==================================
    # Record New BUY Signals
    # ==================================

    def record_signals(self, results):

        df = self.load()

        existing_keys = (

            set(zip(df["Symbol"], df["SignalDate"]))
            if len(df)
            else set()

        )

        new_rows = []

        for stock in results:

            if stock.get("Signal") != "BUY":
                continue

            # تاريخ الإشارة هو تاريخ آخر شمعة فعلية، لا تاريخ جهاز
            # التشغيل؛ فقد تكون السوق مغلقة أو بيانات المزود متأخرة.
            signal_date = str(stock["Data"].index[-1].date())
            key = (stock["Ticker"], signal_date)

            # منع تسجيل نفس السهم مرتين في نفس اليوم
            if key in existing_keys:
                continue

            new_rows.append({

                "Symbol": stock["Ticker"],
                "SignalDate": signal_date,

                "BuyLow": stock["BuyLow"],
                "EntryPrice": stock["BuyHigh"],
                "EntryDate": "",
                "StopLoss": stock["StopLoss"],
                "Target1": stock["Target1"],
                "Target2": stock["Target2"],

                "Score": stock["Score"],
                "Confidence": stock["Confidence"],
                "RR": stock["RR"],
                "AIProbability": stock["AIProbability"],

                "Status": "PENDING_ENTRY",

                "ExitDate": "",
                "ExitPrice": "",
                "ExitReason": "",
                "HoldingDays": ""

            })

        if new_rows:

            df = pd.concat(

                [df, pd.DataFrame(new_rows)],

                ignore_index=True

            )

            self.save(df)

        return len(new_rows)

    # ==================================
    # Update Open Trades (Live Check)
    # ==================================

    def update_open_trades(self):

        df = self.load()

        open_mask = df["Status"].isin(["PENDING_ENTRY", "OPEN"])

        if not open_mask.any():
            return 0

        cfg = load_backtest_config()

        updated = 0

        for idx in df[open_mask].index:

            row = df.loc[idx]

            symbol = row["Symbol"]

            try:

                # Paper updates are forward/live operations, never historical
                # backtests, so they follow the Scanner provider route.
                raw = load_history(symbol, purpose="scanner")

                raw = calculate_indicators(raw)

                data = MarketData(raw)

            except Exception:

            # السهم فشل تحميله دلوقتي - نسيبه كما هو ونجرب
                # تاني فى المرة الجاية
                continue

            signal_date = pd.to_datetime(row["SignalDate"]).date()

            after = [

                i for i in range(data.length)

                if data.index[i].date() > signal_date

            ]

            # لسه مفيش شمعة جديدة بعد يوم الإشارة (نفس اليوم)
            if not after:
                continue

            entry_index = after[0]

            stop = float(row["StopLoss"])
            target1 = float(row["Target1"])

            buy_low = float(row.get("BuyLow", row["EntryPrice"]))
            buy_high = float(row["EntryPrice"])

            # الإشارات الجديدة تظل معلّقة إلى أن يلمس السعر نطاق
            # الدخول خلال نفس المهلة المستخدمة في الباك تست.
            if row["Status"] == "PENDING_ENTRY":
                entry_candidates = [
                    i for i in after[:cfg.ENTRY_WAIT_DAYS]
                    if data.low[i] <= buy_high and data.high[i] >= buy_low
                ]

                if not entry_candidates:
                    if len(after) >= cfg.ENTRY_WAIT_DAYS:
                        df.loc[idx, "Status"] = "EXPIRED"
                        df.loc[idx, "ExitDate"] = str(
                            data.index[after[cfg.ENTRY_WAIT_DAYS - 1]].date()
                        )
                        df.loc[idx, "ExitReason"] = "EntryTimeout"
                        updated += 1
                    continue

                filled_index = entry_candidates[0]
                df.loc[idx, "Status"] = "OPEN"
                df.loc[idx, "EntryDate"] = str(data.index[filled_index].date())
                # نفس افتراض الباك تست: تنفيذ limit عند الحد الأعلى
                # للنطاق بعد احتساب الانزلاق.
                df.loc[idx, "EntryPrice"] = TradingCosts().entry_price(buy_high)
                row = df.loc[idx]
                entry_index = filled_index + 1

            else:
                entry_date = row.get("EntryDate")
                if pd.notna(entry_date) and str(entry_date).strip():
                    positions = [
                        i for i in range(data.length)
                        if data.index[i].date() == pd.to_datetime(entry_date).date()
                    ]
                    if not positions:
                        continue
                    entry_index = positions[0] + 1

            if entry_index >= data.length:
                continue

            stored_entry_date = row.get("EntryDate")
            if pd.isna(stored_entry_date) or not str(stored_entry_date).strip():
                stored_entry_date = signal_date

            context = BacktestContext(
                symbol=symbol,
                data=data,
                signal_index=entry_index - 1,
                signal={
                    "StopLoss": stop,
                    "Target1": target1,
                    "Target2": float(row["Target2"])
                },
                entry_price=float(row["EntryPrice"]),
                entry_date=str(stored_entry_date),
                entry_index=entry_index
            )

            # allow_timeout=False يمنع إغلاقاً وهمياً قبل اكتمال مدة
            # الاحتفاظ، لكنه يغلق فور تحقق هدف أو ستوب.
            closed = ExitManager(TradingCosts()).manage(
                context,
                allow_timeout=False
            )

            if not closed:
                continue

            df.loc[idx, "Status"] = "CLOSED"
            df.loc[idx, "ExitDate"] = context.exit_date
            df.loc[idx, "ExitPrice"] = context.exit_price
            df.loc[idx, "ExitReason"] = context.exit_reason
            df.loc[idx, "HoldingDays"] = (
                pd.to_datetime(context.exit_date).date()
                - pd.to_datetime(context.entry_date).date()
            ).days
            updated += 1

        if updated:
            self.save(df)

        return updated
