import pandas as pd
import logging
from datetime import datetime, timezone

from core.data_provider import load_history, provider_purpose, symbol_data_coverage
from core.egx_session import session_close_datetime
from core.live_actionability import actionability_fields
from core.level_status import (
    classic_levels_engine_version,
    classic_snapshot_evidence_hash,
)
from indicators.technical import calculate_indicators
from core.paper_trading import PaperTradingTracker
from config.settings_manager import settings
from core.symbols import load_symbols
from services.experiment_tracking import ExperimentRun
from forward_testing.service import ForwardTestingService
from strategy.trading_decision import TradingDecisionService
from strategy_breakout.breakout_strategy import BreakoutSwingStrategy
from strategy_selector.selector import (
    AdaptiveStrategySelector,
    unavailable_selector_result,
)
from services.swing_coverage_audit import (
    ScanResults,
    failed_coverage_row,
    successful_coverage_row,
    write_coverage_report,
)


logger = logging.getLogger(__name__)


def scan_symbols(source, data_purpose="scanner"):

    # يضمن أن أي تعديل محفوظ من شاشة Settings أو من ملف الإعدادات
    # يُطبّق على أول Scan تالي حتى لو ظلّ Streamlit مفتوحاً.
    settings.reload()

    symbols = load_symbols(source)

    # Phase 6 tracking wraps the scan and records its inputs/outputs only. It
    # never participates in signal evaluation, sorting, or paper trading.
    experiment = ExperimentRun("SCAN", "LIVE_SCAN", symbols)

    # ==========================
    # لو موديل الـ AI مش موجود، السكانر يفضل شغال
    # (بدون AI) بدل ما يقع بالكامل.
    # ==========================

    decision_service = TradingDecisionService(
        # Production remains Strategy-led. AI is recorded as advisory evidence
        # and cannot demote a valid technical BUY to WATCH.
        mode=TradingDecisionService.LIVE_ADVISORY
    )
    # Phase 10 is deliberately additive.  BREAKOUT_SWING evaluates the same
    # completed candle but can never rewrite the frozen Classic result below.
    try:
        breakout_strategy = BreakoutSwingStrategy()
    except Exception:
        # A research-strategy configuration fault must never suppress a
        # validated Classic row from the production scanner.
        logger.exception("BREAKOUT_SWING initialization failed")
        breakout_strategy = None

    results = []
    failures = []
    coverage = []
    required_lookback = int(settings.get("data").get("min_bars", 250))

    for symbol in symbols:

        try:

            df = load_history(symbol, purpose=data_purpose)
            provider_metadata = dict(df.attrs.get("market_data", {}))
            df = calculate_indicators(df)
            # Pandas indicator operations may drop attrs; restore provider
            # evidence so the post-strategy actionability layer is auditable.
            df.attrs["market_data"] = provider_metadata

            # آخر شمعة
            i = len(df) - 1

            last = df.iloc[i]
            # Nested EGX30 market data follows the same Scanner/Dashboard route.
            with provider_purpose(data_purpose):
                result = decision_service.evaluate(df, i)
            breakout_result = _safe_breakout_evaluation(
                breakout_strategy, df, i, symbol
            )
            frozen_price = float(last["Close"])
            completed_session = pd.Timestamp(df.index[i]).date()
            completed_timestamp = session_close_datetime(
                completed_session
            ).isoformat()
            evidence_hash = classic_snapshot_evidence_hash(symbol, df, result)
            levels_engine = classic_levels_engine_version()
            # Recorded after the frozen decision exists.  It is metadata only;
            # the timestamp is not passed to strategy or used by sorting.
            signal_timestamp = datetime.now(timezone.utc).isoformat()

            # ==========================
            # Rating
            # ==========================

            if result["Confidence"] >= 90:
                rating = "A+"

            elif result["Confidence"] >= 80:
                rating = "A"

            elif result["Confidence"] >= 70:
                rating = "B+"

            elif result["Confidence"] >= 60:
                rating = "B"

            else:
                rating = "C"

            results.append({

                "Ticker": symbol,

                "Rating": rating,

                # ``Price`` remains the backward-compatible completed-candle
                # display value.  The additive raw/provenance fields below stop
                # the details page from mistaking it for a live quote.
                "Price": round(frozen_price, 2),
                "FrozenSnapshotPrice": frozen_price,
                "FrozenDataTimestamp": completed_timestamp,
                "SignalTimestamp": signal_timestamp,
                "LastCompletedSession": completed_timestamp,
                "CompletedSessionClose": frozen_price,
                "CompletedSessionTimestamp": completed_timestamp,
                "CompletedSessionProvider": provider_metadata.get("provider"),
                "LevelsCalculationVersion": levels_engine,
                "EvidenceHash": evidence_hash,
                "HistoricalProvider": provider_metadata.get("provider"),
                "DataDomain": provider_metadata.get("data_domain"),
                "LivePrice": provider_metadata.get("live_quote_last"),
                "LivePriceTimestamp": provider_metadata.get("live_quote_timestamp"),
                "LivePriceReceivedTimestamp": provider_metadata.get(
                    "live_quote_received_timestamp"
                ),
                "LivePriceStatus": provider_metadata.get("live_quote_freshness"),
                "LiveProvider": provider_metadata.get("live_quote_provider"),
                "SnapshotStatus": (
                    "FROZEN + LIVE OVERLAY"
                    if provider_metadata.get("live_quote_available")
                    else "FROZEN"
                ),

                "Signal": result["Signal"],
                "Stars": result["Stars"],

                "Confidence": result["Confidence"],
                "Score": result["Score"],

                # ==========================
                # AI
                # ==========================

                "AIProbability": result["AIProbability"],
                "AILevel": result["AILevel"],
                "AIApproved": result["AIApproved"],

                # ==========================

                "Trend": result["Trend"],
                "Volume": result["Volume"],
                "Momentum": result["Momentum"],
                "Candles": result["Candles"],
                "Breakout": result["Breakout"],

                "Support": result["Support"],
                "Resistance": result["Resistance"],

                "BuyLow": result["BuyLow"],
                "BuyHigh": result["BuyHigh"],

                "StopLoss": result["StopLoss"],

                "Target1": result["Target1"],
                "Target2": result["Target2"],

                "RR": result["RR"],

                # ==========================
                # Independent Phase 10 strategy comparison
                # ==========================
                # These fields are research/display evidence only.  The
                # canonical ``Signal`` and every existing downstream consumer
                # continue to use the frozen Classic decision above.
                "ClassicDecision": result["Signal"],
                "ClassicRR": result["RR"],
                "BreakoutDecision": breakout_result["Signal"],
                "BreakoutRR": breakout_result["RR"],
                "BreakoutScore": breakout_result["Score"],
                "BreakoutConfidence": breakout_result["Confidence"],
                "BreakoutEdgeScore": breakout_result["EdgeScore"],
                "BreakoutEntry": breakout_result["Entry"],
                "BreakoutStopLoss": breakout_result["StopLoss"],
                "BreakoutTarget1": breakout_result["Target1"],
                "BreakoutTarget2": breakout_result["Target2"],
                "BreakoutSetup": " | ".join(breakout_result["SetupTypes"]),
                "BreakoutReasons": " | ".join(breakout_result["Reasons"]),
                "BreakoutTrace": breakout_result["DecisionTrace"],
                "BreakoutDecisionSupportOnly": breakout_result["DecisionSupportOnly"],
                "HigherQualityStrategy": _higher_quality_strategy(
                    result, breakout_result
                ),

                "Reasons": " | ".join(result["Reasons"]),

                # ==========================
                # Decision Engine Transparency
                # ==========================
                # جايين مباشرة من strategy/decision_engine.py -
                # مفيدين للعرض فى تفاصيل السهم (ليه القرار طلع
                # كده بالظبط، مش مجرد رقم Score نهائي).
                # ==========================

                "Regime": result["Regime"],
                "IndexRegime": result.get("IndexRegime", "N/A"),
                "DecisionTrace": result["DecisionTrace"],
                "ConfidenceBreakdown": result["ConfidenceBreakdown"],

                # AI Details

                "AIFeatures": last,

                # هنستخدمه فى صفحة تفاصيل السهم

                "Data": df

            })
            # Additive operational fields never rewrite the frozen Signal.
            results[-1].update(actionability_fields(result["Signal"], df))
            coverage.append(successful_coverage_row(
                symbol, df, result["Signal"], required_lookback
            ))

        except Exception as e:

            logger.exception("Swing scan failed for %s", symbol)
            failures.append({"Symbol": symbol, "Error": str(e)})
            try:
                evidence = symbol_data_coverage(symbol)
            except Exception as audit_error:
                evidence = {"rubix_error": str(audit_error)}
            coverage.append(failed_coverage_row(
                symbol, evidence, e, required_lookback
            ))

    # ==========================
    # Smart Sorting
    # ==========================

    results.sort(

        key=lambda x: (

            _numeric_sort_value(x.get("AIProbability")),
            x["Confidence"],
            x["Score"],
            x["RR"]

        ),

        reverse=True

    )

    # ==========================
    # Rank
    # ==========================

    for rank, stock in enumerate(results, start=1):
        stock["Rank"] = rank

    # Phase 11 is a post-strategy selector.  It consumes the two completed
    # outputs and market evidence but never rewrites ``Signal`` or either
    # strategy's geometry.
    adaptive_context = _apply_adaptive_selector(results)

    # ==========================
    # Paper Trading (متابعة حية)
    # ==========================
    # 1) نحدّث أول حاجة أي صفقة مفتوحة قديمة (هل ضربت هدف/ستوب
    #    أو خلصت مدتها) قبل ما نسجل إشارات جديدة.
    # 2) نسجل أي إشارة BUY جديدة ظهرت فى السكان ده.
    # ==========================

    try:

        tracker = PaperTradingTracker()

        tracker.update_open_trades()

        tracker.record_signals([
            row for row in results
            if row.get("Signal") != "BUY" or row.get("Actionable", False)
        ])

    except Exception as e:

        print(f"Paper Trading Tracker Error -> {e}")

    # Store a serializable result view without the price frames required by
    # the live details page. Existing UI code selects named columns, so RunID
    # is additive and backward compatible.
    for stock in results:
        stock["RunID"] = experiment.run_id

    # Phase 7 consumes the already-final scanner decisions. It evaluates only
    # older signals before persisting today's immutable session evidence.
    try:
        forward_session = ForwardTestingService().process_scan(
            results, experiment.run_id, experiment.run_dir
        )
    except Exception as error:
        experiment.fail(error)
        raise RuntimeError(f"Forward testing session failed: {error}") from error

    experiment.save_records("scan_results.csv", results)
    experiment.save_records("failed_symbols.csv", failures)
    coverage_frame = write_coverage_report(coverage)
    experiment.save_dataframe("swing_symbol_coverage_audit.csv", coverage_frame)
    signals = {
        "BUY": sum(row.get("Signal") == "BUY" for row in results),
        "WATCH": sum(row.get("Signal") == "WATCH" for row in results),
        "AVOID": sum(row.get("Signal") == "AVOID" for row in results),
    }
    experiment.save_dataframe(
        "summary.csv", pd.DataFrame([{"Stocks": len(results), **signals}])
    )
    scan_dates = [
        row["Data"].index[-1]
        for row in results
        if row.get("Data") is not None and len(row["Data"])
    ]
    experiment.complete(
        metrics={"Stocks": len(results), **signals},
        failures=failures,
        successful_symbols=len(results),
        walk_forward_status="NOT_APPLICABLE",
        extra_metadata={
            "date_range": {
                "start": str(min(scan_dates)) if scan_dates else None,
                "end": str(max(scan_dates)) if scan_dates else None,
            },
            "forward_testing": forward_session,
            "adaptive_selector": adaptive_context,
        },
    )

    return ScanResults(results, coverage=coverage, failures=failures)


def _numeric_sort_value(value):
    """Keep disabled/unavailable AI values sortable with numeric predictions."""
    try:
        return float(value) if value is not None else -1.0
    except (TypeError, ValueError):
        return -1.0


def _higher_quality_strategy(classic_result, breakout_result):
    """Compare displayed quality without merging or changing either signal."""

    if breakout_result.get("Signal") == "UNAVAILABLE":
        return "CLASSIC (BREAKOUT UNAVAILABLE)"

    classic_quality = (
        _numeric_sort_value(classic_result.get("Score"))
        + _numeric_sort_value(classic_result.get("Confidence"))
    ) / 2.0
    breakout_quality = _numeric_sort_value(breakout_result.get("EdgeScore"))
    if abs(classic_quality - breakout_quality) < 1e-9:
        return "TIE"
    return "BREAKOUT_SWING" if breakout_quality > classic_quality else "CLASSIC"


def _safe_breakout_evaluation(strategy, frame, index, symbol):
    """Isolate the optional Phase 10 research path from Classic scanning."""

    if strategy is not None:
        try:
            return strategy.evaluate(frame, index)
        except Exception:
            logger.exception("BREAKOUT_SWING evaluation failed for %s", symbol)
    return {
        "Signal": "UNAVAILABLE",
        "RR": 0.0,
        "Score": 0,
        "Confidence": 0,
        "EdgeScore": 0.0,
        "Entry": None,
        "StopLoss": None,
        "Target1": None,
        "Target2": None,
        "SetupTypes": [],
        "Reasons": ["BREAKOUT_SWING_UNAVAILABLE"],
        "DecisionTrace": {},
        "DecisionSupportOnly": True,
    }


def _apply_adaptive_selector(results):
    """Attach independent Phase 11 recommendations without changing inputs."""

    if not results:
        return {"status": "NO_RESULTS", "regime": "UNKNOWN"}
    try:
        selector = AdaptiveStrategySelector()
        classification = selector.classify_scan(results)
        for row in results:
            selection = selector.select_symbol(
                {
                    "Signal": row.get("ClassicDecision", row.get("Signal")),
                    "Score": row.get("Score"),
                    "Confidence": row.get("Confidence"),
                    "RR": row.get("ClassicRR", row.get("RR")),
                },
                {
                    "Signal": row.get("BreakoutDecision"),
                    "Score": row.get("BreakoutScore"),
                    "Confidence": row.get("BreakoutConfidence"),
                    "RR": row.get("BreakoutRR"),
                    "EdgeScore": row.get("BreakoutEdgeScore"),
                },
                classification,
            )
            row.update(selection)
            row["AdaptiveMarketReasons"] = " | ".join(classification.reasons)
            row["AdaptiveMarketFeatures"] = classification.snapshot.__dict__
        return {
            "status": "COMPLETED",
            "regime": classification.regime,
            "confidence": classification.confidence,
            "reasons": list(classification.reasons),
            "buy_classic": sum(
                row.get("FinalRecommendation") == "BUY_CLASSIC" for row in results
            ),
            "buy_breakout": sum(
                row.get("FinalRecommendation") == "BUY_BREAKOUT" for row in results
            ),
        }
    except Exception as error:
        logger.exception("Adaptive selector failed; frozen strategy rows retained")
        unavailable = unavailable_selector_result(error)
        for row in results:
            row.update(unavailable)
        return {"status": "FAILED", "regime": "UNKNOWN", "error": str(error)}
