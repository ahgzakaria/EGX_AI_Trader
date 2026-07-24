from backtesting.config import load as load_backtest_config


class ExitManager:
    """
    ==========================================================
    التعديل الجوهري: Trailing Stop حقيقي + Partial Exit حقيقي
    ==========================================================

    الاتنين معطّلين افتراضيًا (Opt-in)، يعني السلوك القديم
    (خروج بس عند Target1/Target2/StopLoss/Timeout الثابتين)
    فاضل زي ما هو تمامًا لو محدش فعّل حاجة من الإعدادات دي.

    Trailing Stop (لو TRAILING_ENABLED=True):
        الستوب بيرتفع تلقائيًا (وبس لفوق، أبدًا لتحت) مع تقدم
        السعر، بناءً على إما EMA20 أو مسافة ATR من إغلاق اليوم
        اللي فات (مش اليوم الحالي - عشان منستخدمش معلومة لسه
        منعرفهاش وقت اتخاذ القرار).

    Partial Exit (لو PARTIAL_EXIT=True، ومع EXIT_MODE=TARGET2):
        لما السعر يوصل Target1، بنحصّل PARTIAL_PERCENT من
        المركز عند سعر Target1، والباقي بيفضل شغال لحد ما
        يضرب Target2 أو الستوب (العادي أو المتحرك) أو الوقت
        يخلص. السعر والنتيجة النهائية للصفقة بيبقوا "متوسط
        مرجّح" (Weighted Average) بين السعرين.
    """

    def __init__(

        self,

        costs

    ):

        self.costs = costs

        cfg = load_backtest_config()

        self.exit_mode = cfg.EXIT_MODE
        self.max_holding_days = cfg.MAX_HOLDING_DAYS
        self.move_to_breakeven = cfg.MOVE_TO_BREAKEVEN

        self.trailing_enabled = cfg.TRAILING_ENABLED
        self.trailing_mode = cfg.TRAILING_MODE
        self.trailing_atr_multiplier = cfg.TRAILING_ATR

        self.partial_exit_enabled = cfg.PARTIAL_EXIT
        self.partial_percent = cfg.PARTIAL_PERCENT

    # ==================================
    # Manage Trade
    # ==================================

    def manage(self, context, allow_timeout=True):

        data = context.data

        signal = context.signal

        current_stop = signal["StopLoss"]

        break_even = False

        # ==================================
        # Partial Exit State
        # ==================================

        partial_taken = False
        partial_price = None
        partial_date = None

        last_index = min(

            context.entry_index +

            self.max_holding_days - 1,

            data.length - 1

        )

        for k in range(

            context.entry_index,

            last_index + 1

        ):

            # ==================================
            # Trailing Stop Update (Opt-in)
            # ==================================
            # بيتحدث بناءً على بيانات "يوم أمس" (k-1) بس، عشان
            # منستخدمش معلومة (زي إقفال أو ATR اليوم الحالي)
            # كانت لسه مش معروفة وقت ما كنا بنحدد الستوب لليوم ده.
            # ==================================

            if self.trailing_enabled and k > context.entry_index:

                prev = k - 1

                if self.trailing_mode == "EMA20":

                    candidate_stop = float(data.ema20[prev])

                elif self.trailing_mode == "ATR":

                    candidate_stop = float(

                        data.close[prev]
                        - (self.trailing_atr_multiplier * data.atr[prev])

                    )

                else:

                    candidate_stop = current_stop

                # الترايلينج بيرفع الستوب بس - أبدًا ميقلّلوش
                # تحت آخر مستوى وصله

                if candidate_stop > current_stop:

                    current_stop = candidate_stop

            # ==================================
            # Stop Loss (عادي أو Trailing)
            # ==================================
            # بيانات OHLC اليومية لا تخبرنا بترتيب الـ High والـ
            # Low داخل اليوم. لذلك لو لمس السعر الستوب والهدف في
            # نفس الشمعة نختار الستوب أولاً؛ وهي فرضية محافظة تمنع
            # تضخيم نتائج الباك تست.

            if data.low[k] <= current_stop:

                final_price = self.costs.exit_price(

                    current_stop

                )

                self._close(

                    context,

                    k,

                    final_price,

                    partial_taken,

                    partial_price,

                    partial_date,

                    break_even,

                    current_stop,

                    signal

                )

                return True

            # ==================================
            # Target 1
            # ==================================

            if data.high[k] >= signal["Target1"]:

                if self.exit_mode == "TARGET1":

                    context.exit_price = self.costs.exit_price(

                        signal["Target1"]

                    )

                    context.exit_date = str(

                        data.index[k].date()

                    )

                    context.exit_index = k

                    context.exit_reason = "Target1"

                    context.result = "WIN"

                    return True

                elif (

                    self.partial_exit_enabled

                    and self.exit_mode == "TARGET2"

                    and not partial_taken

                ):

                    # بنحصّل جزء من المركز هنا، والباقي فاضل
                    # شغال (منخرجش من اللوب)

                    partial_taken = True

                    partial_price = self.costs.exit_price(

                        signal["Target1"]

                    )

                    partial_date = data.index[k]

                    if self.move_to_breakeven:

                        break_even = True

                        current_stop = max(

                            current_stop,

                            context.entry_price

                        )

                elif self.move_to_breakeven:

                    break_even = True

                    current_stop = max(

                        current_stop,

                        context.entry_price

                    )

            # ==================================
            # Target 2
            # ==================================

            if (

                self.exit_mode == "TARGET2"

                and

                data.high[k] >= signal["Target2"]

            ):

                final_price = self.costs.exit_price(

                    signal["Target2"]

                )

                context.exit_price = self._blend(

                    partial_taken,
                    partial_price,
                    final_price

                )

                context.exit_date = str(

                    data.index[k].date()

                )

                context.exit_index = k

                context.exit_reason = (

                    "Partial+Target2"
                    if partial_taken
                    else "Target2"

                )

                context.result = "WIN"

                return True

        # لا نغلق الصفقة الحية لمجرد أن البيانات المتاحة اليوم أقل
        # من مدة الاحتفاظ؛ نستكمل التتبع في الـ scan التالي.
        holding_end_index = (
            context.entry_index + self.max_holding_days - 1
        )

        if not allow_timeout and data.length - 1 < holding_end_index:
            return False

        # ==================================
        # Timeout
        # ==================================

        final_price = self.costs.exit_price(

            float(

                data.close[last_index]

            )

        )

        self._close(

            context,

            last_index,

            final_price,

            partial_taken,

            partial_price,

            partial_date,

            break_even,

            current_stop,

            signal,

            is_timeout=True

        )

        return True

    # ==================================
    # Blend Partial + Final Price (Weighted Average)
    # ==================================

    def _blend(

        self,

        partial_taken,

        partial_price,

        final_price

    ):

        if not partial_taken:

            return final_price

        return round(

            (partial_price * self.partial_percent)

            + (final_price * (1 - self.partial_percent)),

            4

        )

    # ==================================
    # Close Helper (Stop Loss / BreakEven / Timeout)
    # ==================================

    def _close(

        self,

        context,

        k,

        final_price,

        partial_taken,

        partial_price,

        partial_date,

        break_even,

        current_stop,

        signal,

        is_timeout=False

    ):

        data = context.data

        blended_price = self._blend(

            partial_taken,

            partial_price,

            final_price

        )

        context.exit_price = blended_price

        context.exit_date = str(

            data.index[k].date()

        )

        context.exit_index = k

        base_reason = None

        base_result = None

        if is_timeout:

            base_reason = "Timeout"

            base_result = (

                "WIN"
                if final_price > context.entry_price
                else "LOSS"

            )

        elif break_even and current_stop >= context.entry_price:

            base_reason = "BreakEven"

            base_result = "BREAKEVEN"

        elif self.trailing_enabled and current_stop > signal["StopLoss"]:

            # الستوب اتحرك لفوق وقفل بربح حقيقي (مش مجرد
            # وصل لسعر الدخول) - ده نتيجة الـ Trailing، مش
            # خسارة عادية

            base_reason = "TrailingStop"

            base_result = (

                "WIN"
                if final_price > context.entry_price
                else "LOSS"

            )

        else:

            base_reason = "StopLoss"

            base_result = "LOSS"

        context.exit_reason = (

            f"Partial+{base_reason}"
            if partial_taken
            else base_reason

        )

        context.result = base_result
