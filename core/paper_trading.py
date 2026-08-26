import os
import logging
import math
from dataclasses import dataclass
from enum import Enum

import pandas as pd

from core.data_provider import load_history
from indicators.technical import calculate_indicators
from core.market_data import MarketData
from providers.base_provider import ProviderError

from backtesting.config import load as load_backtest_config
from backtesting.context import BacktestContext
from backtesting.costs import TradingCosts
from backtesting.managers.exit_manager import ExitManager


PAPER_TRADES_FILE = "data/paper_trades.csv"

logger = logging.getLogger(__name__)


class TrackerState(str, Enum):
    """Typed, non-financial outcomes for one tracker operation."""

    RECORDED = "RECORDED"
    PENDING_DATA = "PENDING_DATA"
    SKIPPED_MISSING_REQUIRED_FIELD = "SKIPPED_MISSING_REQUIRED_FIELD"
    LEGACY_INCOMPLETE = "LEGACY_INCOMPLETE"
    EVALUATION_NOT_DUE = "EVALUATION_NOT_DUE"
    PRICE_UNAVAILABLE = "PRICE_UNAVAILABLE"
    INVALID_RECORD = "INVALID_RECORD"
    UPDATED = "UPDATED"


@dataclass(frozen=True)
class TrackerOutcome:
    """Why a paper-trading record was recorded, deferred, skipped, or updated."""

    symbol: str
    signal_date: str
    state: TrackerState
    reason: str
    field: str | None = None


# Contract violations are reported once per process for the same immutable
# record/field/state. Expected pending or legacy absence is DEBUG-only.
_WARNED_CONTRACT_KEYS = set()


def _finite_number(value, *, positive=False, non_negative=False):
    """Return a validated number without mapping missing/invalid values to zero."""

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if non_negative and number < 0:
        return None
    return number


def _session_date(value):
    """Parse the tracker's date-only contract; never invent a missing timestamp."""

    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = pd.to_datetime(text, errors="raise")
    except (TypeError, ValueError):
        return None
    return parsed.date()


def _required_text(value):
    """Return a non-empty text field, rejecting pandas missing values."""

    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


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
        self.last_outcomes = []

        if not os.path.exists(PAPER_TRADES_FILE):

            pd.DataFrame(columns=COLUMNS).to_csv(

                PAPER_TRADES_FILE,

                index=False,

                encoding="utf-8-sig"

            )

    def _record_outcome(
        self,
        symbol,
        signal_date,
        state,
        reason,
        *,
        field=None,
        warn=False,
    ):
        """Store one typed result and use warning severity only for violations."""

        outcome = TrackerOutcome(
            symbol=str(symbol or ""),
            signal_date=str(signal_date or ""),
            state=TrackerState(state),
            reason=str(reason),
            field=str(field) if field else None,
        )
        self.last_outcomes.append(outcome)
        if warn:
            key = (
                outcome.symbol,
                outcome.signal_date,
                outcome.field,
                outcome.state.value,
            )
            if key not in _WARNED_CONTRACT_KEYS:
                _WARNED_CONTRACT_KEYS.add(key)
                logger.warning(
                    "Paper trade skipped: symbol=%s signal_date=%s state=%s "
                    "field=%s reason=%s",
                    outcome.symbol,
                    outcome.signal_date,
                    outcome.state.value,
                    outcome.field or "—",
                    outcome.reason,
                )
        else:
            logger.debug(
                "Paper tracker state: symbol=%s signal_date=%s state=%s "
                "field=%s reason=%s",
                outcome.symbol,
                outcome.signal_date,
                outcome.state.value,
                outcome.field or "—",
                outcome.reason,
            )
        return outcome

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

                "EntryDate": object,
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

            symbol = _required_text(stock.get("Ticker")) or ""
            frame = stock.get("Data")
            if not symbol or frame is None or getattr(frame, "empty", True):
                self._record_outcome(
                    symbol,
                    "",
                    TrackerState.SKIPPED_MISSING_REQUIRED_FIELD,
                    "BUY signal has no immutable symbol/history snapshot",
                    field="Ticker/Data",
                    warn=True,
                )
                continue

            # تاريخ الإشارة هو تاريخ آخر شمعة فعلية، لا تاريخ جهاز
            # التشغيل؛ فقد تكون السوق مغلقة أو بيانات المزود متأخرة.
            try:
                signal_date = str(frame.index[-1].date())
            except (AttributeError, IndexError, TypeError, ValueError):
                self._record_outcome(
                    symbol,
                    "",
                    TrackerState.SKIPPED_MISSING_REQUIRED_FIELD,
                    "BUY signal history has no valid completed-session date",
                    field="SignalDate",
                    warn=True,
                )
                continue

            required_prices = (
                ("BuyLow", stock.get("BuyLow")),
                ("BuyHigh", stock.get("BuyHigh")),
                ("StopLoss", stock.get("StopLoss")),
                ("Target1", stock.get("Target1")),
                ("Target2", stock.get("Target2")),
            )
            invalid_fields = [
                field for field, value in required_prices
                if _finite_number(value, positive=True) is None
            ]
            if invalid_fields:
                fields = ",".join(invalid_fields)
                self._record_outcome(
                    symbol,
                    signal_date,
                    TrackerState.SKIPPED_MISSING_REQUIRED_FIELD,
                    f"BUY signal is missing valid required price field(s): {fields}",
                    field=fields,
                    warn=True,
                )
                continue

            key = (symbol, signal_date)

            # منع تسجيل نفس السهم مرتين في نفس اليوم
            if key in existing_keys:
                continue

            new_rows.append({

                "Symbol": symbol,
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
            self._record_outcome(
                symbol,
                signal_date,
                TrackerState.RECORDED,
                "complete BUY signal queued as PENDING_ENTRY",
            )

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
        commission = _finite_number(
            getattr(cfg, "COMMISSION", None),
            non_negative=True,
        )
        slippage = _finite_number(
            getattr(cfg, "SLIPPAGE", None),
            non_negative=True,
        )
        wait_days = _finite_number(
            getattr(cfg, "ENTRY_WAIT_DAYS", None),
            positive=True,
        )
        if (
            commission is None
            or slippage is None
            or wait_days is None
            or not wait_days.is_integer()
        ):
            if commission is None:
                field = "COMMISSION"
            elif slippage is None:
                field = "SLIPPAGE"
            else:
                field = "ENTRY_WAIT_DAYS"
            self._record_outcome(
                "CONFIG",
                "",
                TrackerState.INVALID_RECORD,
                "paper-trading costs and entry wait must be valid finite values",
                field=field,
                warn=True,
            )
            return 0
        wait_days = int(wait_days)

        updated = 0

        for idx in df[open_mask].index:

            row = df.loc[idx]

            symbol = _required_text(row.get("Symbol")) or ""
            signal_date = _session_date(row.get("SignalDate"))
            if not symbol or signal_date is None:
                field = "Symbol" if not symbol else "SignalDate"
                self._record_outcome(
                    symbol,
                    row.get("SignalDate"),
                    TrackerState.INVALID_RECORD,
                    "active paper record has invalid immutable identity",
                    field=field,
                    warn=True,
                )
                continue

            try:

                # Paper updates are forward/live operations, never historical
                # backtests, so they follow the Scanner provider route.
                raw = load_history(symbol, purpose="scanner")

                raw = calculate_indicators(raw)

                data = MarketData(raw)

            except ProviderError:
                # السهم فشل تحميله دلوقتي - نسيبه كما هو ونجرب
                # تاني فى المرة الجاية
                self._record_outcome(
                    symbol,
                    signal_date,
                    TrackerState.PRICE_UNAVAILABLE,
                    "scanner history is temporarily unavailable",
                )
                logger.debug(
                    "Paper tracker price load failed for %s on %s",
                    symbol,
                    signal_date,
                    exc_info=True,
                )
                continue
            except Exception:
                logger.exception(
                    "Paper tracker market-data preparation failed for %s on %s",
                    symbol,
                    signal_date,
                )
                raise

            after = [

                i for i in range(data.length)

                if data.index[i].date() > signal_date

            ]

            # لسه مفيش شمعة جديدة بعد يوم الإشارة (نفس اليوم)
            if not after:
                self._record_outcome(
                    symbol,
                    signal_date,
                    TrackerState.EVALUATION_NOT_DUE,
                    "no completed candle exists after the signal session",
                )
                continue

            entry_index = after[0]

            prices = {
                field: _finite_number(row.get(field), positive=True)
                for field in ("StopLoss", "Target1", "Target2", "EntryPrice")
            }
            invalid_fields = [
                field for field, value in prices.items() if value is None
            ]
            if invalid_fields:
                fields = ",".join(invalid_fields)
                self._record_outcome(
                    symbol,
                    signal_date,
                    TrackerState.INVALID_RECORD,
                    f"active paper record has invalid required price field(s): {fields}",
                    field=fields,
                    warn=True,
                )
                continue

            stop = prices["StopLoss"]
            target1 = prices["Target1"]
            target2 = prices["Target2"]
            buy_high = prices["EntryPrice"]

            # الإشارات الجديدة تظل معلّقة إلى أن يلمس السعر نطاق
            # الدخول خلال نفس المهلة المستخدمة في الباك تست.
            if row["Status"] == "PENDING_ENTRY":
                buy_low = _finite_number(row.get("BuyLow"), positive=True)
                if buy_low is None:
                    self._record_outcome(
                        symbol,
                        signal_date,
                        TrackerState.SKIPPED_MISSING_REQUIRED_FIELD,
                        "pending entry requires a valid BuyLow",
                        field="BuyLow",
                        warn=True,
                    )
                    continue
                entry_candidates = [
                    i for i in after[:wait_days]
                    if data.low[i] <= buy_high and data.high[i] >= buy_low
                ]

                if not entry_candidates:
                    if len(after) >= wait_days:
                        df.loc[idx, "Status"] = "EXPIRED"
                        df.loc[idx, "ExitDate"] = str(
                            data.index[after[wait_days - 1]].date()
                        )
                        df.loc[idx, "ExitReason"] = "EntryTimeout"
                        updated += 1
                        self._record_outcome(
                            symbol,
                            signal_date,
                            TrackerState.UPDATED,
                            "pending entry expired after the configured wait",
                        )
                    else:
                        self._record_outcome(
                            symbol,
                            signal_date,
                            TrackerState.PENDING_DATA,
                            "entry window remains incomplete",
                        )
                    continue

                filled_index = entry_candidates[0]
                df.loc[idx, "Status"] = "OPEN"
                df.loc[idx, "EntryDate"] = str(data.index[filled_index].date())
                # نفس افتراض الباك تست: تنفيذ limit عند الحد الأعلى
                # للنطاق بعد احتساب الانزلاق.
                df.loc[idx, "EntryPrice"] = TradingCosts(
                    symbol=symbol).entry_price(buy_high)
                row = df.loc[idx]
                entry_index = filled_index + 1

            else:
                entry_date = row.get("EntryDate")
                if pd.notna(entry_date) and str(entry_date).strip():
                    parsed_entry_date = _session_date(entry_date)
                    if parsed_entry_date is None:
                        self._record_outcome(
                            symbol,
                            signal_date,
                            TrackerState.INVALID_RECORD,
                            "open paper record has an invalid EntryDate",
                            field="EntryDate",
                            warn=True,
                        )
                        continue
                    positions = [
                        i for i in range(data.length)
                        if data.index[i].date() == parsed_entry_date
                    ]
                    if not positions:
                        self._record_outcome(
                            symbol,
                            signal_date,
                            TrackerState.PRICE_UNAVAILABLE,
                            "stored entry session is absent from provider history",
                            field="EntryDate",
                        )
                        continue
                    entry_index = positions[0] + 1
                else:
                    # Legacy OPEN rows did not persist EntryDate. The historical
                    # behavior used SignalDate for exit evaluation; preserve it
                    # without fabricating or writing a replacement timestamp.
                    self._record_outcome(
                        symbol,
                        signal_date,
                        TrackerState.LEGACY_INCOMPLETE,
                        "legacy OPEN record has no EntryDate; SignalDate is used in memory",
                        field="EntryDate",
                    )

            if entry_index >= data.length:
                self._record_outcome(
                    symbol,
                    signal_date,
                    TrackerState.EVALUATION_NOT_DUE,
                    "no completed candle exists after the effective entry",
                )
                continue

            stored_entry_date = row.get("EntryDate")
            if pd.isna(stored_entry_date) or not str(stored_entry_date).strip():
                stored_entry_date = signal_date

            # BuyLow is an entry-band requirement only. Legacy OPEN rows may not
            # have it and must still be evaluated using their frozen exit fields.
            if (
                row["Status"] == "OPEN"
                and _finite_number(row.get("BuyLow"), positive=True) is None
            ):
                self._record_outcome(
                    symbol,
                    signal_date,
                    TrackerState.LEGACY_INCOMPLETE,
                    "legacy OPEN record has no BuyLow; exit tracking does not require it",
                    field="BuyLow",
                )

            context = BacktestContext(
                symbol=symbol,
                data=data,
                signal_index=entry_index - 1,
                signal={
                    "StopLoss": stop,
                    "Target1": target1,
                    "Target2": target2
                },
                entry_price=buy_high,
                entry_date=str(stored_entry_date),
                entry_index=entry_index
            )

            # allow_timeout=False يمنع إغلاقاً وهمياً قبل اكتمال مدة
            # الاحتفاظ، لكنه يغلق فور تحقق هدف أو ستوب.
            closed = ExitManager(TradingCosts(symbol=symbol)).manage(
                context,
                allow_timeout=False
            )

            if not closed:
                self._record_outcome(
                    symbol,
                    signal_date,
                    TrackerState.PENDING_DATA,
                    "open position has not reached an exit condition",
                )
                continue

            exit_price = _finite_number(context.exit_price, positive=True)
            exit_date = _session_date(context.exit_date)
            entry_date = _session_date(context.entry_date)
            if exit_price is None or exit_date is None or entry_date is None:
                fields = []
                if exit_price is None:
                    fields.append("ExitPrice")
                if exit_date is None:
                    fields.append("ExitDate")
                if entry_date is None:
                    fields.append("EntryDate")
                field = ",".join(fields)
                self._record_outcome(
                    symbol,
                    signal_date,
                    TrackerState.INVALID_RECORD,
                    f"exit manager returned invalid required field(s): {field}",
                    field=field,
                    warn=True,
                )
                continue

            df.loc[idx, "Status"] = "CLOSED"
            df.loc[idx, "ExitDate"] = str(exit_date)
            df.loc[idx, "ExitPrice"] = exit_price
            df.loc[idx, "ExitReason"] = context.exit_reason
            df.loc[idx, "HoldingDays"] = (exit_date - entry_date).days
            updated += 1
            self._record_outcome(
                symbol,
                signal_date,
                TrackerState.UPDATED,
                "open paper position closed by the existing exit manager",
            )

        if updated:
            self.save(df)

        return updated
