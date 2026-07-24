from strategy.filter import market_filter


# ==================================
# ترجمة أنظمة السوق الداخلية (TRENDING/WEAK_TREND/RANGING)
# لمصطلحات احترافية مفهومة (Bull/Sideways/Bear).
#
# ملحوظة مهمة: الدالة دي "غلاف" (Wrapper) حوالين
# strategy/filter.py الموجود بالفعل، مش منطق جديد. يعني
# قرار الـ Pass/Fail نفسه متغيرش خالص - بس بقى ليه اسم
# ومعنى احترافي واضح للعرض والتتبع (Decision Trace).
# ==================================


def evaluate_market(df, i, cfg):

    result = market_filter(df, i, cfg)

    regime = result["regime"]

    passed = result["passed"]

    # ==================================
    # لو السوق فى اتجاه صاعد واضح (TRENDING) بس رفض الدخول
    # (يعني EMA20 تحت EMA50 أو السعر تحت EMA20) - ده انعكاس
    # فعلي، نسميه BEAR مش مجرد "مفيش فرصة".
    #
    # لو رفض فى WEAK_TREND أو RANGING - ده مجرد "مفيش فرصة
    # واضحة دلوقتي"، مش انعكاس حقيقي، فنسميه SIDEWAYS.
    # ==================================

    if regime == "TRENDING":

        label = "BULL" if passed else "BEAR"

    else:

        label = "SIDEWAYS"

    return {

        "Passed": passed,

        "Regime": label,

        "RawRegime": regime,

        "Reasons": result["reasons"]

    }
