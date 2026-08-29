import pandas as pd

from core.data_provider import current_provider_purpose, load_history


# ==========================================================
# Market Analyzer (السؤال الأول: هل السوق ككل بيسمح بالشراء؟)
# ==========================================================
#
# ده مختلف تمامًا عن strategy/market_regime.py (اللي بيحلل
# اتجاه السهم نفسه لوحده). هنا بنحلل **مؤشر EGX30 نفسه**
# (^CASE30 على ياهو فايننس) - يعني حالة السوق المصري ككل،
# بغض النظر عن أي سهم بعينه.
#
# الفلسفة: لو السوق ككل فى اتجاه هابط واضح، حتى أفضل سهم
# فى مصر ممكن يتسحب معاه فى انهيار عام - فمنطقي نمنع فتح
# مراكز شراء جديدة فى الحالة دي، بغض النظر عن جودة السهم.
# ==========================================================


INDEX_SYMBOL = "^CASE30"

# ==================================
# Cache على مستوى العملية (Process-Level)
# ==================================
# بيانات المؤشر بتتحمّل مرة واحدة بس لكل Scan/Backtest، مش
# مرة لكل سهم - غير كده هنعمل مئات نداءات مزود البيانات زيادة
# لنفس البيانات بالظبط فى كل تشغيلة.
# ==================================

_cache = {"key": None, "df": None}


def _load_index_data(period, fast_ema, slow_ema):

    purpose = current_provider_purpose()
    cache_key = (purpose, period, fast_ema, slow_ema)
    if _cache["df"] is not None and _cache["key"] == cache_key:

        return _cache["df"]

    df = load_history(
        INDEX_SYMBOL,
        period=period,
        interval="1d",
        purpose=purpose,
        require_positive_volume=False,
        min_bars=1,
    )

    if df is None or df.empty:

        raise ValueError(

            f"No data found for {INDEX_SYMBOL}"

        )

    # ==================================
    # ملحوظة: من غير فلتر Volume > 0 اللي فى core/data_loader.py
    # - بيانات حجم التداول للمؤشرات نفسها مش دايمًا موثوقة أو
    # موجودة زي الأسهم الفردية، ومش مهمة لتحليل الاتجاه هنا.
    # ==================================

    df = df[["Open", "High", "Low", "Close"]].dropna()

    df["EMA50"] = df["Close"].ewm(

        span=fast_ema,

        adjust=False

    ).mean()

    df["EMA200"] = df["Close"].ewm(

        span=slow_ema,

        adjust=False

    ).mean()

    _cache["key"] = cache_key
    _cache["df"] = df

    return df


def reset_cache():

    # مفيدة لو عايز تجبر تحميل بيانات المؤشر من جديد (مثلاً
    # فى تشغيلة تانية بعد فترة طويلة)

    _cache["key"] = None
    _cache["df"] = None


# ==================================
# Analyze (بأي تاريخ محدد - مهم جدًا للـ Backtest)
# ==================================
# لازم ناخد بس بيانات المؤشر "لحد نفس تاريخ السهم اللي
# بنقيّمه دلوقتي" - عشان منعملش Lookahead Bias (يعني نستخدم
# معرفة عن حالة السوق فى المستقبل وقت تقييم صفقة فى الماضي).
# ==================================

def analyze(date, cfg):

    try:

        from config.settings_manager import settings

        period = settings.get("data").get("history_period", "10y")
        df = _load_index_data(
            period,
            cfg.MARKET_INDEX_FAST_EMA,
            cfg.MARKET_INDEX_SLOW_EMA,
        )

    except Exception as e:

        # لو فشل تحميل المؤشر لأي سبب (مشكلة شبكة مثلاً)،
        # منمنعش كل الصفقات بسبب كده - نسمح افتراضيًا.
        #
        # Failing open on a network error is right. Failing open *silently* is
        # not, and that is what this was doing: `^CASE30` has one bar in the
        # backtest cache and is served by neither live provider, so this branch
        # is taken on EVERY bar in EVERY mode -- while `require_market_analyzer`
        # reads true in the settings UI and the decision trace reports
        # `MarketAnalyzer: PASS`. A gate that cannot fire and says PASS is worse
        # than no gate, because a reader believes it.
        #
        # `Available` is what the caller needs to tell "the index says go" from
        # "there is no index". See DAILY_STRATEGY_DIAGNOSIS.md section 7.

        return {

            "Passed": True,

            "Available": False,

            "Regime": "UNKNOWN",

            "Reasons": [

                f"Market Analyzer unavailable ({e}) - defaulting to allow"

            ]

        }

    date = pd.Timestamp(date)

    # أقرب موقع لتاريخ <= التاريخ المطلوب (من غير أي نظرة
    # للمستقبل)

    position = df.index.searchsorted(date, side="right") - 1

    if position < 200:

        # Same distinction as the exception branch above: not enough index
        # history is not the index saying "sideways", and reporting it as such
        # is how a gate with one bar of data came to read PASS for years.

        return {

            "Passed": True,

            "Available": False,

            "Regime": "UNKNOWN",

            "Reasons": [

                "Market Analyzer: insufficient EGX30 history yet"

            ]

        }

    close = df["Close"].iloc[position]
    ema50 = df["EMA50"].iloc[position]
    ema200 = df["EMA200"].iloc[position]

    if close > ema50 > ema200:

        return {

            "Passed": True,

            "Available": True,

            "Regime": "BULL",

            "Reasons": [

                "EGX30: Price > EMA50 > EMA200 (Bull Market)"

            ]

        }

    elif close < ema50 < ema200:

        return {

            "Passed": False,

            "Available": True,

            "Regime": "BEAR",

            "Reasons": [

                "EGX30: Price < EMA50 < EMA200 (Bear Market) "
                "- New Buys Blocked"

            ]

        }

    else:

        return {

            "Passed": True,

            "Available": True,

            "Regime": "SIDEWAYS",

            "Reasons": [

                "EGX30: No clear trend alignment (Sideways)"

            ]

        }
