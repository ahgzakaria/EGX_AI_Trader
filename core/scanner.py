import pandas as pd
import logging
import time
from datetime import datetime, timezone

from core.data_provider import load_history, provider_purpose, symbol_data_coverage
from core.research_router import reset_research_caches
from core.scan_context import ScanCancelled, build_scan_context
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


# Typed per-symbol outcomes. A generic ProviderError told the operator nothing about
# WHY a symbol was dropped; these map the real cause so a coverage gap is visible
# instead of being hidden behind a success rate.
SYMBOL_SUCCESS = "SUCCESS"
SYMBOL_EODHD_CACHE_MISS = "EODHD_CACHE_MISS"
SYMBOL_EODHD_REFRESH_FAILED = "EODHD_REFRESH_FAILED"
SYMBOL_EODHD_TIMEOUT = "EODHD_TIMEOUT"
SYMBOL_EODHD_CONNECTION_FAILED = "EODHD_CONNECTION_FAILED"
SYMBOL_EODHD_RATE_LIMITED = "EODHD_RATE_LIMITED"
SYMBOL_EODHD_AUTH_FAILED = "EODHD_AUTH_FAILED"
SYMBOL_EODHD_PROVIDER_UNAVAILABLE = "EODHD_PROVIDER_UNAVAILABLE"
SYMBOL_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
SYMBOL_INVALID_HISTORY = "INVALID_HISTORY"
SYMBOL_EXCLUDED_NON_EQUITY = "EXCLUDED_NON_EQUITY"
SYMBOL_VOLUME_POLICY_UNRESOLVED = "VOLUME_POLICY_UNRESOLVED"
SYMBOL_RUBIX_QUOTE_MISSING = "RUBIX_QUOTE_MISSING"
SYMBOL_RUBIX_OVERLAY_UNAVAILABLE = "RUBIX_OVERLAY_UNAVAILABLE"
SYMBOL_CANCELLED = "CANCELLED"
SYMBOL_INTERNAL_ERROR = "INTERNAL_ERROR"

# Research-router statuses map straight onto a symbol outcome. Everything here is a
# deterministic per-symbol data condition — none of it indicates a provider outage.
_RESEARCH_STATUS_TO_SYMBOL_STATUS = {
    "DATA_INSUFFICIENT": SYMBOL_INSUFFICIENT_HISTORY,
    "DATA_UNAVAILABLE": SYMBOL_EODHD_CACHE_MISS,
    "EXCLUDED_NON_EQUITY": SYMBOL_EXCLUDED_NON_EQUITY,
    "VOLUME_POLICY_UNRESOLVED": SYMBOL_VOLUME_POLICY_UNRESOLVED,
    "EODHD_RESEARCH_REVIEW_REQUIRED": SYMBOL_INVALID_HISTORY,
    "BRIDGE_CONFLICT": SYMBOL_INVALID_HISTORY,
    "LOCAL_PLUS_RUBIX_STALE": SYMBOL_INVALID_HISTORY,
    "LOCAL_SEED_ONLY_STALE": SYMBOL_INVALID_HISTORY,
    "LOCAL_PLUS_RUBIX_BUILDING_HISTORY": SYMBOL_INSUFFICIENT_HISTORY,
    "EODHD_PROVIDER_UNAVAILABLE": SYMBOL_EODHD_PROVIDER_UNAVAILABLE,
}

# Typed provider exceptions map by CLASS, which is exact — no message matching.
_EODHD_EXCEPTION_STATUS = (
    ("EODHDAuthFailed", SYMBOL_EODHD_AUTH_FAILED),
    ("EODHDRateLimited", SYMBOL_EODHD_RATE_LIMITED),
    ("EODHDTimeout", SYMBOL_EODHD_TIMEOUT),
    ("EODHDConnectionFailed", SYMBOL_EODHD_CONNECTION_FAILED),
    ("EODHDCancelled", SYMBOL_CANCELLED),
)


def classify_symbol_failure(error):
    """Map a raised error onto a typed symbol status. Never hides the failure."""
    from core.scan_context import ScanCancelled

    if isinstance(error, ScanCancelled):
        return SYMBOL_CANCELLED
    # Exact class match first — a typed provider exception needs no message parsing.
    for class_name, symbol_status in _EODHD_EXCEPTION_STATUS:
        if any(base.__name__ == class_name for base in type(error).__mro__):
            return symbol_status
    status = getattr(error, "status", None)
    if status and str(status) in _RESEARCH_STATUS_TO_SYMBOL_STATUS:
        return _RESEARCH_STATUS_TO_SYMBOL_STATUS[str(status)]
    text = str(error)
    for research_status, symbol_status in _RESEARCH_STATUS_TO_SYMBOL_STATUS.items():
        if research_status in text:
            return symbol_status
    lowered = text.lower()
    # The engine's own lookback gate raises a plain ProviderDataError with no status
    # attribute — e.g. "ICLE.CA: not enough history (196 bars; minimum 250)". That is a
    # deterministic per-symbol data condition, not a programming defect.
    if "not enough history" in lowered or "insufficient history" in lowered:
        return SYMBOL_INSUFFICIENT_HISTORY
    if "timeout" in lowered or "timed out" in lowered:
        return SYMBOL_EODHD_TIMEOUT
    if "rate limit" in lowered or "429" in lowered:
        return SYMBOL_EODHD_RATE_LIMITED
    if "eodhd" in lowered and ("token" in lowered or "not configured" in lowered
                               or "auth" in lowered):
        return SYMBOL_EODHD_AUTH_FAILED
    if "connection" in lowered and "eodhd" in lowered:
        return SYMBOL_EODHD_CONNECTION_FAILED
    if "no quote" in lowered or "SYMBOL_MISSING" in text:
        return SYMBOL_RUBIX_QUOTE_MISSING
    if "current research unavailable" in lowered:
        return SYMBOL_EODHD_CACHE_MISS
    return SYMBOL_INTERNAL_ERROR


def _open_archive_session(experiment, scan_ctx):
    """Start the run-scoped archive writer, or fall back to synchronous capture.

    A run without a dataset archive (or an environment where the session cannot be
    created) keeps the previous inline behaviour rather than losing evidence.
    """
    archive = getattr(experiment, "dataset_archive", None)
    if archive is None:
        return None, None
    try:
        from services.archive_capture_session import QueuedArchiveCaptureSession
        from services.dataset_archive import activate_queued_session

        session = QueuedArchiveCaptureSession(
            archive, cancellation_event=scan_ctx.cancellation_event)
        return session, activate_queued_session(session)
    except Exception:
        logger.exception("Queued archive session unavailable; capturing inline")
        return None, None


def _close_archive_session(session, token, *, cancelled):
    """Drain and clear the writer. Returns a sanitized failure, or ``None``."""
    if session is None:
        return None
    from services.dataset_archive import deactivate_queued_session

    try:
        if cancelled:
            session.cancel()
        return session.close_and_drain()
    finally:
        deactivate_queued_session(token)


def scan_symbols(source, data_purpose="scanner", scan_context=None, *,
                 progress=None, cancellation_event=None, job=None):

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

    # One scan-scoped context: one EODHD session, one expected-completed-session
    # resolution, and ONE batched Rubix overlay read for the whole universe. The
    # per-symbol path below therefore opens no Rubix connection of its own.
    cancelled = False
    # ``emit`` is a no-op unless a caller supplied a progress callback, so every
    # existing call site keeps its exact previous behaviour.
    def emit(event, **payload):
        if progress is not None:
            progress(event, **payload)

    if scan_context is None:
        reset_research_caches()
        emit("RUBIX_PREPARING", total=len(symbols))
        scan_ctx = build_scan_context(symbols, cancellation_event=cancellation_event,
                                      breaker=getattr(job, "breaker", None))
        owns_context = True
        if job is not None:
            # The job owns cleanup from here: its ``finally`` closes the context on
            # success, cancellation and exception alike.
            job.attach_context(scan_ctx)
            owns_context = False
    else:
        scan_ctx = scan_context
        owns_context = False
    emit("RUBIX_READY", available=scan_ctx.rubix_available_count,
         missing=len(symbols) - scan_ctx.rubix_available_count,
         status=scan_ctx.rubix_batch_status)
    emit("SCAN_STARTING", total=len(symbols))

    # Immutable evidence is still captured for every symbol, but its serialization runs
    # on ONE archive writer thread so it overlaps analysis instead of blocking it. The
    # session is drained and cleared before any run is published.
    archive_session, archive_token = _open_archive_session(experiment, scan_ctx)

    for symbol in symbols:

        symbol_started = time.monotonic()
        try:
            scan_ctx.raise_if_cancelled()
            emit("SYMBOL_STARTING", symbol=symbol)

            df = load_history(symbol, purpose=data_purpose, scan_context=scan_ctx)
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
            emit("SYMBOL_COMPLETED", symbol=symbol, status=SYMBOL_SUCCESS,
                 seconds=time.monotonic() - symbol_started)

        except ScanCancelled:
            # Cancellation stops scheduling immediately. Partial results are kept as
            # diagnostic evidence; the run is never finalized as a completed session.
            cancelled = True
            logger.info("Swing scan cancelled before %s", symbol)
            break

        except Exception as e:

            logger.exception("Swing scan failed for %s", symbol)
            failures.append({
                "Symbol": symbol,
                "Error": str(e),
                "Status": classify_symbol_failure(e),
            })
            try:
                evidence = symbol_data_coverage(symbol)
            except Exception as audit_error:
                evidence = {"rubix_error": str(audit_error)}
            coverage.append(failed_coverage_row(
                symbol, evidence, e, required_lookback
            ))
            emit("SYMBOL_FAILED", symbol=symbol, status=classify_symbol_failure(e),
                 seconds=time.monotonic() - symbol_started)

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

    except Exception:
        # Paper tracking is intentionally non-fatal to a completed market scan,
        # but unexpected failures must retain their traceback for diagnosis.
        logger.exception("Paper Trading Tracker failed")

    # Store a serializable result view without the price frames required by
    # the live details page. Existing UI code selects named columns, so RunID
    # is additive and backward compatible.
    for stock in results:
        stock["RunID"] = experiment.run_id

    # A cancelled scan is not a session. It never reaches forward-session
    # finalization and is never recorded as a completed immutable run — the partial
    # rows are returned as diagnostic evidence only.
    if cancelled:
        emit("SCAN_CANCELLED", completed=len(results) + len(failures))
        _close_archive_session(archive_session, archive_token, cancelled=True)
        if owns_context:
            scan_ctx.close()
        experiment.cancel() if hasattr(experiment, "cancel") else experiment.fail(
            RuntimeError("scan cancelled by operator"))
        return ScanResults(results, coverage=coverage, failures=failures,
                           status="CANCELLED")

    # Finalization is claimed exactly once, keyed on the job's scan_id. A Streamlit
    # rerun observes the published result; it can never re-enter this block.
    # Drain and join the writer before publication: a completed immutable archive may
    # never be published while artifacts are still in flight, and an archive failure is
    # a job failure rather than a quietly degraded scan.
    archive_failure = _close_archive_session(archive_session, archive_token,
                                             cancelled=False)
    if archive_failure is not None:
        experiment.fail(RuntimeError(archive_failure))
        if owns_context:
            scan_ctx.close()
        raise RuntimeError(f"Dataset archive capture failed: {archive_failure}")

    if job is not None and not job.begin_finalization():
        if owns_context:
            scan_ctx.close()
        return ScanResults(results, coverage=coverage, failures=failures,
                           status="CANCELLED" if job.cancelled else "COMPLETED")
    emit("FINALIZATION_STARTING", completed=len(results) + len(failures))

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

    if job is not None:
        job.finalization_completed = True
    if owns_context:
        scan_ctx.close()
    emit("SCAN_COMPLETED", success=len(results), failed=len(failures),
         total=len(symbols))
    status = "COMPLETED" if not failures else "COMPLETED_WITH_GAPS"
    return ScanResults(results, coverage=coverage, failures=failures, status=status)


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
