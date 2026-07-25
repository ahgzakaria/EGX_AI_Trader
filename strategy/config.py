from types import SimpleNamespace

from config.settings_manager import settings


def load():

    # ==================================
    # بنقرا الإعدادات "Live" من settings.data كل مرة الدالة
    # دي بتتنادى، بدل ما نجمّد القيم مرة واحدة وقت الاستيراد.
    # ده بيضمن إن أي تعديل من شاشة Settings (حتى من غير
    # إعادة تشغيل السيرفر) يتطبق فورًا على أول Backtest/Scan
    # جاي بعد الحفظ.
    # ==================================

    strategy = settings.get("strategy")

    return SimpleNamespace(

        MIN_SCORE=strategy.get("min_score", 65),

        MIN_CONFIDENCE=strategy.get("min_confidence", 80),

        MIN_RR=strategy.get("min_rr", 2.0),

        MIN_TREND=strategy.get("min_trend", 25),

        MIN_MOMENTUM=strategy.get("min_momentum", 5),

        MIN_VOLUME=strategy.get("min_volume", 5),

        WATCH_SCORE=strategy.get("watch_score", 60),
        WATCH_CONFIDENCE=strategy.get("watch_confidence", 65),

        MARKET_TREND_ADX=strategy.get("market_trend_adx", 25),
        MARKET_WEAK_TREND_ADX=strategy.get("market_weak_trend_adx", 18),
        MARKET_INDEX_FAST_EMA=strategy.get("market_index_fast_ema", 50),
        MARKET_INDEX_SLOW_EMA=strategy.get("market_index_slow_ema", 200),

        MAX_RR=strategy.get("max_rr", 100.0),

        REQUIRE_CANDLE_CONFIRMATION=strategy.get(
            "require_candle_confirmation",
            False
        ),

        REQUIRE_MARKET_ANALYZER=strategy.get(
            "require_market_analyzer",
            False
        ),

        REQUIRE_QUALITY_FILTER=strategy.get(
            "require_quality_filter",
            False
        ),

        QUALITY_MIN_ADX=strategy.get(
            "quality_min_adx",
            20
        ),

        QUALITY_MIN_VOLUME_RATIO=strategy.get(
            "quality_min_volume_ratio",
            1.0
        ),

        QUALITY_MIN_ATR_PERCENT=strategy.get(
            "quality_min_atr_percent",
            1.5
        ),

        QUALITY_MIN_RESISTANCE_ROOM=strategy.get(
            "quality_min_resistance_room",
            3.0
        )

    )
