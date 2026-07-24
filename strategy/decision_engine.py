import strategy.config as config

from strategy.market_analyzer import analyze as analyze_market
from strategy.market_regime import evaluate_market

from strategy.trend import trend_score
from strategy.volume import volume_score
from strategy.support import support_resistance
from strategy.entry import entry_signal
from strategy.momentum import momentum_score
from strategy.candles import candle_score
from strategy.breakout import breakout_score
from strategy import quality_filter


def evaluate(df, i):

    cfg = config.load()

    # ==================================
    # 0) Market Analyzer (EGX30)
    # ==================================

    index_state = analyze_market(df.index[i], cfg)

    if cfg.REQUIRE_MARKET_ANALYZER and not index_state["Passed"]:

        return {

            "MarketPassed": False,
            "Regime": index_state["Regime"],
            "IndexRegime": index_state["Regime"],

            "Score": 0,
            "Confidence": 0,
            "Reasons": index_state["Reasons"],

            "Trend": 0,
            "Volume": 0,
            "Momentum": 0,
            "Candles": 0,
            "Breakout": 0,

            "Support": 0,
            "Resistance": 0,

            "BuyLow": 0,
            "BuyHigh": 0,

            "StopLoss": 0,

            "Target1": 0,
            "Target2": 0,

            "RR": 0,

            "Signal": "AVOID",
            "Stars": 1,
            "RejectReason": "MarketAnalyzer",

            "ConfidenceBreakdown": {

                "Trend": 0,
                "Volume": 0,
                "Momentum": 0,
                "Pattern": 0,
                "Risk": 0

            },

            "DecisionTrace": {

                "MarketAnalyzer": "FAIL",
                "MarketRegime": "N/A",
                "Trend": "N/A",
                "Momentum": "N/A",
                "Volume": "N/A",
                "Risk": "N/A",
                "QualityFilter": "N/A",
                "CandleConfirmation": "N/A"

            }

        }

    # ==================================
    # 1) Market Regime (اتجاه السهم نفسه لوحده)
    # ==================================

    market = evaluate_market(df, i, cfg)

    if not market["Passed"]:

        return {

            "MarketPassed": False,
            "Regime": market["Regime"],
            "IndexRegime": index_state["Regime"],

            "Score": 0,
            "Confidence": 0,
            "Reasons": market["Reasons"],

            "Trend": 0,
            "Volume": 0,
            "Momentum": 0,
            "Candles": 0,
            "Breakout": 0,

            "Support": 0,
            "Resistance": 0,

            "BuyLow": 0,
            "BuyHigh": 0,

            "StopLoss": 0,

            "Target1": 0,
            "Target2": 0,

            "RR": 0,

            "Signal": "AVOID",
            "Stars": 1,
            "RejectReason": "MarketFilter",

            "ConfidenceBreakdown": {

                "Trend": 0,
                "Volume": 0,
                "Momentum": 0,
                "Pattern": 0,
                "Risk": 0

            },

            "DecisionTrace": {

                "MarketAnalyzer": (
                    "PASS" if index_state["Passed"] else "FAIL"
                ),
                "MarketRegime": "FAIL",
                "Trend": "N/A",
                "Momentum": "N/A",
                "Volume": "N/A",
                "Risk": "N/A",
                "QualityFilter": "N/A",
                "CandleConfirmation": "N/A"

            }

        }

    # ==================================
    # 2) تقييم كل بُعد لوحده (Independent Evaluators)
    # ==================================

    trend = trend_score(df, i)
    volume = volume_score(df, i)
    support = support_resistance(df, i)
    entry = entry_signal(df, i)
    momentum = momentum_score(df, i)
    candles = candle_score(df, i)
    breakout = breakout_score(df, i)

    # ==================================
    # 3) Score
    # ==================================

    score = (

        trend["score"]
        + volume["score"]
        + support["score"]
        + entry["score"]
        + momentum["score"]
        + candles["score"]
        + breakout["score"]

    )

    # ==================================
    # 4) Confidence Engine
    # ==================================

    trend_confidence = trend["confidence"]
    volume_confidence = volume["confidence"]
    momentum_confidence = momentum["confidence"]

    pattern_confidence = (

        candles["confidence"]
        + breakout["confidence"]

    )

    risk_confidence = (

        entry["confidence"]
        + support["confidence"]

    )

    confidence = min(

        trend_confidence
        + volume_confidence
        + momentum_confidence
        + pattern_confidence
        + risk_confidence,

        100

    )

    reasons = (

        market["Reasons"]
        + trend["reasons"]
        + volume["reasons"]
        + support["reasons"]
        + entry["reasons"]
        + momentum["reasons"]
        + candles["reasons"]
        + breakout["reasons"]

    )

    rr = entry["RR"]

    # ==================================
    # Quality Filter (Opt-in)
    # ==================================

    current_price = float(df["Close"].iloc[i])

    quality = quality_filter.evaluate(

        df,
        i,
        support["resistance"],
        current_price,
        cfg

    )

    reasons = reasons + quality["Reasons"]

    # ==================================
    # 5) البوابات (Waterfall Gates)
    # ==================================

    gates = {

        "Trend": trend["score"] >= cfg.MIN_TREND,

        "Momentum": momentum["score"] >= cfg.MIN_MOMENTUM,

        "Volume": volume["score"] >= cfg.MIN_VOLUME,

        "Risk": cfg.MIN_RR <= rr <= cfg.MAX_RR,

        "CandleConfirmation": (

            not cfg.REQUIRE_CANDLE_CONFIRMATION

            or candles["score"] > 0

        ),

        "QualityFilter": (

            not cfg.REQUIRE_QUALITY_FILTER

            or quality["Passed"]

        )

    }

    all_gates_passed = all(gates.values())

    # ==================================
    # 6) القرار النهائي
    # ==================================

    if (

        all_gates_passed

        and score >= cfg.MIN_SCORE

        and confidence >= cfg.MIN_CONFIDENCE

    ):

        signal = "BUY"
        stars = 5

    elif score >= cfg.WATCH_SCORE and confidence >= cfg.WATCH_CONFIDENCE:

        signal = "WATCH"
        stars = 4

    elif score >= 40:

        signal = "WATCH"
        stars = 3

    else:

        signal = "AVOID"
        stars = 2

    # ==================================
    # 7) Reject Reason (Waterfall Attribution)
    # ==================================

    if signal == "BUY":

        reject_reason = None

    elif score < cfg.MIN_SCORE:

        reject_reason = "Score"

    elif confidence < cfg.MIN_CONFIDENCE:

        reject_reason = "Confidence"

    elif rr < cfg.MIN_RR:

        reject_reason = "RR"

    elif rr > cfg.MAX_RR:

        reject_reason = "MaxRR"

    elif not gates["Trend"]:

        reject_reason = "Trend"

    elif not gates["Momentum"]:

        reject_reason = "Momentum"

    elif not gates["Volume"]:

        reject_reason = "Volume"

    elif not gates["QualityFilter"]:

        reject_reason = "QualityFilter"

    else:

        reject_reason = "CandleConfirmation"

    # ==================================
    # 8) Decision Trace
    # ==================================

    decision_trace = {

        "MarketAnalyzer": (
            "PASS" if index_state["Passed"] else "FAIL"
        ),

        "MarketRegime": "PASS",

        "Trend": "PASS" if gates["Trend"] else "FAIL",
        "Momentum": "PASS" if gates["Momentum"] else "FAIL",
        "Volume": "PASS" if gates["Volume"] else "FAIL",
        "Risk": "PASS" if gates["Risk"] else "FAIL",

        "QualityFilter": (

            "PASS" if gates["QualityFilter"] else "FAIL"

        ),

        "CandleConfirmation": (

            "PASS" if gates["CandleConfirmation"] else "FAIL"

        )

    }

    return {

        "MarketPassed": True,
        "Regime": market["Regime"],
        "IndexRegime": index_state["Regime"],

        "Score": score,
        "Confidence": confidence,
        "Reasons": reasons,

        "Trend": trend["score"],
        "Volume": volume["score"],
        "Momentum": momentum["score"],
        "Candles": candles["score"],
        "Breakout": breakout["score"],

        "Support": support["support"],
        "Resistance": support["resistance"],

        "BuyLow": entry["BuyLow"],
        "BuyHigh": entry["BuyHigh"],

        "StopLoss": entry["StopLoss"],

        "Target1": entry["Target1"],
        "Target2": entry["Target2"],

        "RR": rr,

        "Signal": signal,
        "Stars": stars,
        "RejectReason": reject_reason,

        "ConfidenceBreakdown": {

            "Trend": trend_confidence,
            "Volume": volume_confidence,
            "Momentum": momentum_confidence,
            "Pattern": pattern_confidence,
            "Risk": risk_confidence

        },

        "DecisionTrace": decision_trace

    }
