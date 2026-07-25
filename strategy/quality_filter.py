# ==========================================================
# Quality Filter (Sprint: Quality over Quantity)
# ==========================================================
#
# هدفه الوحيد: تقليل عدد الصفقات عن طريق رفض الإعدادات
# "الحدّية" (اللي بالكاد عدّت الشروط الأساسية) قبل ما توصل
# لمرحلة BUY، حتى لو Score/Confidence/RR كانوا مقبولين.
#
# 4 فحوصات مستقلة، كلهم لازم يعدّوا:
#
#   1) قوة الاتجاه الحقيقية (ADX)
#   2) حجم التداول لازم يكون فوق المتوسط (Volume Ratio)
#   3) حركة سعرية كافية (ATR% - مش سهم "نايم")
#   4) مساحة كافية للمقاومة (مش قريب جدًا من سقف يوقفه)
#
# اختياري بالكامل (Opt-in) - معطّل افتراضيًا.
# ==========================================================


def evaluate(df, i, resistance, current_price, cfg):

    adx = float(df["ADX"].iloc[i])

    volume_ratio = float(df["VOLUME_RATIO"].iloc[i])

    atr_percent = float(df["ATR_PERCENT"].iloc[i])

    if resistance and resistance > 0 and current_price > 0:

        resistance_room_percent = (

            (resistance - current_price)

            / current_price

            * 100

        )

    else:

        # مفيش مقاومة معروفة قريبة - نعتبرها مساحة مفتوحة
        resistance_room_percent = 999

    checks = {

        "Trend (ADX)": adx >= cfg.QUALITY_MIN_ADX,

        "Volume Ratio": volume_ratio >= cfg.QUALITY_MIN_VOLUME_RATIO,

        "ATR %": atr_percent >= cfg.QUALITY_MIN_ATR_PERCENT,

        "Resistance Room": (

            resistance_room_percent

            >= cfg.QUALITY_MIN_RESISTANCE_ROOM

        )

    }

    passed = all(checks.values())

    reasons = []

    if not checks["Trend (ADX)"]:

        reasons.append(

            f"Quality Reject: ADX {adx:.1f} < "
            f"{cfg.QUALITY_MIN_ADX} (اتجاه ضعيف)"

        )

    if not checks["Volume Ratio"]:

        reasons.append(

            f"Quality Reject: Volume Ratio {volume_ratio:.2f} < "
            f"{cfg.QUALITY_MIN_VOLUME_RATIO} (حجم تداول أقل من المتوسط)"

        )

    if not checks["ATR %"]:

        reasons.append(

            f"Quality Reject: ATR% {atr_percent:.2f} < "
            f"{cfg.QUALITY_MIN_ATR_PERCENT} (حركة سعرية ضعيفة)"

        )

    if not checks["Resistance Room"]:

        reasons.append(

            f"Quality Reject: Resistance Room "
            f"{resistance_room_percent:.1f}% < "
            f"{cfg.QUALITY_MIN_RESISTANCE_ROOM}% (قريب من المقاومة)"

        )

    return {

        "Passed": passed,

        "Checks": checks,

        "Reasons": reasons

    }