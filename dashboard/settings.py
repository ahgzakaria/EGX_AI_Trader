from copy import deepcopy
from datetime import datetime
import logging
from pathlib import Path
import time
import traceback

import pandas as pd
import streamlit as st

from config.settings_manager import settings
from services.backtest_service import FULL_HISTORY, VALIDATED_OOS, run_backtest
from ai.trainer import AITrainer
from dashboard.ui import page_header, section_header
from dashboard.backtest_state import (
    SCOPE_LABELS,
    consume_backtest_updates,
    displayed_scope,
    initialize_backtest_state,
    scope_from_label,
    settings_are_dirty,
)
from services.experiment_tracking import RunRepository


logger = logging.getLogger(__name__)


def show_settings():

    settings.reload()
    initialize_backtest_state(st.session_state)

    strategy = settings.get("strategy")
    backtest = settings.get("backtest")
    ai = settings.get("ai")

    page_header(
        "Settings & Backtest",
        "Frozen strategy configuration, validated research and model tools",
        icon="⚙️",
        badge="RESEARCH CONTROL",
    )

    tab1, tab2, tab3, tab4 = st.tabs(

        [

            "📈 Strategy",

            "💰 Backtest",

            "🤖 AI",

            "🚀 Tools"

        ],
        default=st.session_state.get("settings_tabs", "📈 Strategy"),
        key="settings_tabs",

    )

    # ==================================
    # Strategy
    # ==================================

    with tab1:

        c1, c2 = st.columns(2)

        with c1:

            min_score = st.slider(
                "Minimum Score",
                0,
                100,
                strategy["min_score"]
            )

            min_confidence = st.slider(
                "Minimum Confidence",
                0,
                100,
                strategy["min_confidence"]
            )

            min_rr = st.slider(
                "Minimum RR",
                1.0,
                5.0,
                float(strategy["min_rr"]),
                0.1
            )

        with c2:

            min_trend = st.slider(
                "Minimum Trend",
                0,
                30,
                strategy["min_trend"]
            )

            min_momentum = st.slider(
                "Minimum Momentum",
                0,
                20,
                strategy["min_momentum"]
            )

            min_volume = st.slider(
                "Minimum Volume",
                0,
                20,
                strategy["min_volume"]
            )

        st.divider()

        st.caption(
            "🧪 تجريبي — فعّل كل واحد لوحده وقارن نتيجة "
            "الـ Backtest قبل وبعد (القيمة الافتراضية = معطّل "
            "تمامًا، يعني بيرجع لنفس سلوك الاستراتيجية القديمة)"
        )

        c3, c4 = st.columns(2)

        with c3:

            max_rr_enabled = st.checkbox(
                "Enable Max RR Cap",
                value=strategy.get("max_rr", 100.0) < 100.0
            )

            max_rr = st.slider(
                "Maximum RR (لو مفعّل)",
                2.0,
                10.0,
                float(strategy.get("max_rr", 100.0))
                if strategy.get("max_rr", 100.0) < 100.0
                else 3.0,
                0.5,
                disabled=not max_rr_enabled
            )

        with c4:

            require_candle = st.checkbox(
                "Require Candle Confirmation "
                "(candle_score > 0)",
                value=strategy.get(
                    "require_candle_confirmation",
                    False
                )
            )

            require_market_analyzer = st.checkbox(
                "Require Market Analyzer (EGX30 Index)",
                value=strategy.get(
                    "require_market_analyzer",
                    False
                ),
                help=(
                    "لو مفعّل: البرنامج بيحلل مؤشر EGX30 نفسه "
                    "(^CASE30) - لو السوق ككل فى اتجاه هابط "
                    "واضح (Price < EMA50 < EMA200)، بيمنع فتح "
                    "أي صفقة شراء جديدة بغض النظر عن جودة "
                    "السهم نفسه."
                )
            )

        st.divider()

        st.caption(
            "🏆 Quality over Quantity — رفض الإعدادات الحدّية "
            "(بالكاد عدّت الشروط) عشان نقلل عدد الصفقات ونرفع "
            "جودتها (معطّل افتراضيًا)"
        )

        quality_filter_enabled = st.checkbox(
            "Enable Quality Filter",
            value=strategy.get("require_quality_filter", False)
        )

        c5, c6, c7, c8 = st.columns(4)

        with c5:

            quality_min_adx = st.slider(
                "Min ADX (قوة الاتجاه)",
                10,
                40,
                int(strategy.get("quality_min_adx", 20)),
                disabled=not quality_filter_enabled
            )

        with c6:

            quality_min_volume_ratio = st.slider(
                "Min Volume Ratio",
                0.5,
                3.0,
                float(strategy.get("quality_min_volume_ratio", 1.0)),
                0.1,
                disabled=not quality_filter_enabled
            )

        with c7:

            quality_min_atr_percent = st.slider(
                "Min ATR %",
                0.5,
                5.0,
                float(strategy.get("quality_min_atr_percent", 1.5)),
                0.1,
                disabled=not quality_filter_enabled
            )

        with c8:

            quality_min_resistance_room = st.slider(
                "Min Resistance Room %",
                1.0,
                10.0,
                float(strategy.get("quality_min_resistance_room", 3.0)),
                0.5,
                disabled=not quality_filter_enabled
            )

    # ==================================
    # Backtest
    # ==================================

    with tab2:

        c1, c2 = st.columns(2)

        with c1:

            holding = st.slider(
                "Maximum Holding Days",
                5,
                60,
                backtest["max_holding_days"]
            )

            entry_wait = st.slider(
                "Entry Wait Days",
                1,
                10,
                backtest["entry_wait_days"]
            )

            backtest_modes = [
                "STRATEGY_ONLY",
                "AI_HARD_FILTER",
                "AI_POSITION_SIZING",
                "AI_RANKING_ONLY",
                "AI_HYBRID",
            ]
            configured_mode = backtest.get("ai_mode", "STRATEGY_ONLY")
            ai_backtest_mode = st.selectbox(
                "Historical AI Mode",
                backtest_modes,
                index=backtest_modes.index(configured_mode)
                if configured_mode in backtest_modes else 0,
                help=(
                    "Strategy Only is the default. Every AI mode uses only "
                    "Walk-Forward out-of-sample predictions; no global model "
                    "is allowed in a historical backtest."
                )
            )

            walk_forward_splits = st.slider(
                "Walk-Forward Folds",
                2,
                10,
                int(backtest.get("walk_forward_splits", 5)),
                disabled=ai_backtest_mode == "STRATEGY_ONLY",
            )

        with c2:

            exit_mode = st.selectbox(
                "Exit Mode",
                [
                    "TARGET1",
                    "TARGET2"
                ],
                index=0 if backtest["exit_mode"] == "TARGET1" else 1
            )

            breakeven = st.checkbox(
                "Move To BreakEven",
                value=backtest["move_to_breakeven"]
            )

        st.divider()

        st.caption("⚠️ Risk & Portfolio Controls")

        c3, c4 = st.columns(2)

        with c3:

            risk_percent = st.slider(
                "Risk Percent per Trade (%)",
                0.25,
                5.0,
                float(backtest["risk_percent"]),
                0.25
            )

            max_open_positions = st.slider(
                "Max Open Positions (concurrent)",
                1,
                30,
                int(backtest.get("max_open_positions", 10))
            )

        with c4:

            max_portfolio_risk_percent = st.slider(
                "Max Portfolio Heat (%)",
                2.0,
                30.0,
                float(backtest.get("max_portfolio_risk_percent", 10)),
                1.0,
                help=(
                    "أقصى نسبة من رأس المال ممكن تكون معرضة "
                    "للخطر فى نفس اللحظة من كل الصفقات المفتوحة "
                    "مجتمعة (لو كل الـ Stop Loss ضربوا مع بعض)."
                )
            )

            allow_overlap = st.checkbox(
                "Allow Overlapping Trades (same symbol)",
                value=backtest["allow_overlapping_trades"]
            )

            initial_capital = st.number_input(
                "Initial Capital (EGP)",
                min_value=1000,
                value=int(backtest["initial_capital"]),
                step=1000
            )

        # ==================================
        # الرقم الفعلي المشتق (Risk × Positions ≤ Heat)
        # ==================================

        try:

            effective_positions = min(
                int(max_open_positions),
                int(
                    max_portfolio_risk_percent
                    // float(risk_percent)
                )
            )

            st.caption(
                f"ℹ️ Effective Max Concurrent Positions: "
                f"**{effective_positions}** "
                f"(= min(Max Open Positions, "
                f"Heat {max_portfolio_risk_percent}% ÷ "
                f"Risk {risk_percent}%))"
            )

        except ZeroDivisionError:

            pass

        st.divider()

        st.caption(
            "🎯 تجريبي — التعديلات الجوهرية اللي بتعالج مشكلة "
            "صفقات الـ Timeout (معطّلين افتراضيًا)"
        )

        c5, c6 = st.columns(2)

        with c5:

            trailing_enabled = st.checkbox(
                "Enable Trailing Stop",
                value=backtest.get("trailing_enabled", False),
                help=(
                    "الستوب بيرتفع تلقائيًا مع تقدم السعر (وبس "
                    "لفوق، أبدًا لتحت) بدل ما يفضل ثابت من "
                    "لحظة الدخول."
                )
            )

            trailing_mode = st.selectbox(
                "Trailing Mode",
                ["EMA20", "ATR"],
                index=0 if backtest["trailing_mode"] == "EMA20" else 1,
                disabled=not trailing_enabled
            )

            trailing_atr = st.slider(
                "Trailing ATR Multiplier (لو ATR Mode)",
                1.0,
                5.0,
                float(backtest["trailing_atr"]),
                0.5,
                disabled=(
                    not trailing_enabled
                    or trailing_mode != "ATR"
                )
            )

        with c6:

            partial_exit_enabled = st.checkbox(
                "Enable Partial Exit",
                value=backtest["partial_exit"],
                help=(
                    "لما السعر يوصل Target1، بيتحصّل جزء من "
                    "المركز، والباقي بيكمل لـ Target2 أو "
                    "الستوب أو الوقت يخلص. شغال بس لو Exit "
                    "Mode = TARGET2."
                )
            )

            partial_percent = st.slider(
                "Partial Exit % (عند Target1)",
                0.1,
                0.9,
                float(backtest["partial_percent"]),
                0.1,
                disabled=not partial_exit_enabled
            )

            if partial_exit_enabled and exit_mode != "TARGET2":

                st.warning(
                    "⚠️ Partial Exit محتاج Exit Mode = TARGET2 "
                    "عشان يشتغل فعليًا."
                )

    # ==================================
    # AI
    # ==================================

    with tab3:

        c1, c2 = st.columns(2)

        with c1:

            ai_enabled = st.checkbox(
                "Enable AI Filter",
                value=ai["enabled"]
            )

        with c2:

            ai_probability = st.slider(
                "Minimum AI Probability",
                50,
                100,
                ai["min_probability"]
            )

    # ==================================
    # TOOLS
    # ==================================

    with tab4:

        st.subheader("🚀 Tools")

        scope_label = st.selectbox(
            "Backtest Scope",
            list(SCOPE_LABELS),
            key="backtest_scope_selector",
            help=(
                "The validated Phase 5 figures use only the common Walk-Forward "
                "coverage with fresh initial capital. Full History starts earlier "
                "and is not directly comparable to the Phase 5 baseline."
            ),
        )
        backtest_scope = scope_from_label(scope_label)
        if backtest_scope == FULL_HISTORY:
            st.warning(
                "Full History will not reproduce the Phase 5 65.81% baseline "
                "because it includes the pre-OOS years and a different capital path."
            )

        state = st.session_state
        status_name = state.backtest_status
        state_banner = st.empty()
        if status_name == "COMPLETED":
            state_banner.success(
                f"Latest backtest completed in "
                f"{state.backtest_elapsed_seconds or 0:.1f} seconds."
            )
        elif status_name == "FAILED":
            state_banner.error(state.backtest_error or "The backtest failed.")
        elif status_name == "CANCELLED":
            state_banner.warning(state.backtest_error or "The backtest was cancelled.")
        else:
            state_banner.info("Backtest is idle and ready to run.")

        run_button = st.empty()
        run_clicked = run_button.button(
            "▶ Run Backtest",
            use_container_width=True,
            type="primary",
            disabled=state.backtest_running,
            key="run_backtest_button",
        )

        if run_clicked and not state.backtest_running:
            started_clock = time.monotonic()
            started_at = datetime.now().astimezone()
            state.backtest_running = True
            state.backtest_status = "RUNNING"
            state.backtest_result = None
            state.backtest_error = None
            state.backtest_traceback = None
            state.backtest_started_at = started_at.isoformat()
            state.backtest_finished_at = None
            state.backtest_elapsed_seconds = None
            state.backtest_stage = "Starting backtest"
            state.backtest_progress = 0.0
            state.backtest_progress_text = "Preparing experiment"
            state.backtest_active_run_id = None
            state.backtest_active_run_directory = None
            state.backtest_last_scope = backtest_scope
            state_banner.warning(
                f"Backtest started at {started_at:%Y-%m-%d %H:%M:%S %Z}."
            )

            run_button.empty()
            run_button.button(
                "⏳ Backtest Running…",
                use_container_width=True,
                disabled=True,
                key="run_backtest_running_indicator",
            )
            progress = st.progress(0)
            status = st.empty()
            percent_text = st.empty()
            elapsed_text = st.empty()
            status.info(f"Started at {started_at:%Y-%m-%d %H:%M:%S %Z}")

            def observe_update(update):
                """Render service events without altering their calculations."""

                update_type = update.get("type")
                if update_type == "started":
                    state.backtest_active_run_id = update.get("run_id")
                    state.backtest_active_run_directory = update.get("run_directory")
                    state.backtest_stage = "Experiment created"
                    status.info(
                        f"Experiment {update.get('run_id')} created — "
                        "preparing market data"
                    )
                elif update_type == "stage":
                    state.backtest_stage = update.get(
                        "phase", update.get("stage", "Running")
                    )
                    status.info(update.get("message", state.backtest_stage))
                elif update_type == "progress":
                    total = max(int(update.get("total") or 0), 1)
                    current = min(int(update.get("current") or 0), total)
                    ratio = current / total
                    state.backtest_progress = ratio
                    state.backtest_stage = update.get("phase", "Backtest")
                    state.backtest_progress_text = (
                        f"{current} / {total} ({update.get('percent', 0)}%)"
                    )
                    progress.progress(ratio)
                    status.info(
                        f"{state.backtest_stage} — Current symbol: "
                        f"{update.get('symbol', 'N/A')}"
                    )
                    percent_text.write(state.backtest_progress_text)
                elapsed_text.caption(
                    f"Elapsed: {time.monotonic() - started_clock:.1f} seconds"
                )

            try:
                result = consume_backtest_updates(
                    backtest_scope,
                    run_backtest,
                    observer=observe_update,
                )
                state.backtest_result = result
                state.backtest_status = "COMPLETED"
                state.backtest_stage = "Completed"
                state.backtest_active_run_id = result.get("run_id")
                state.backtest_active_run_directory = result.get("run_directory")
            except Exception as exc:
                full_traceback = traceback.format_exc()
                logger.exception("Backtest UI execution failed")
                state.backtest_status = "FAILED"
                state.backtest_error = (
                    "Backtest failed. No trading settings were changed. "
                    f"Details: {exc}"
                )
                state.backtest_traceback = full_traceback
                if state.backtest_active_run_id:
                    RunRepository.mark_interrupted(
                        state.backtest_active_run_id,
                        f"Backtest failed: {exc}",
                    )
            finally:
                state.backtest_running = False
                state.backtest_finished_at = datetime.now().astimezone().isoformat()
                state.backtest_elapsed_seconds = time.monotonic() - started_clock

            if state.backtest_status == "COMPLETED":
                state_banner.success(
                    f"Latest backtest completed in "
                    f"{state.backtest_elapsed_seconds:.1f} seconds."
                )
            else:
                state_banner.error(state.backtest_error or "The backtest failed.")

            progress.empty()
            status.empty()
            percent_text.empty()
            elapsed_text.empty()
            # Re-enable the same button widget and render the persisted result
            # on a clean pass. The keyed tabs preserve the visible Tools tab.
            st.rerun()

        if state.backtest_status == "FAILED" and state.backtest_traceback:
            with st.expander("Technical error details"):
                st.code(state.backtest_traceback)

        result = state.backtest_result
        if result:
            st.success("Backtest Finished Successfully")
            st.caption(
                f"Started: {state.backtest_started_at or 'N/A'} | "
                f"Elapsed: {state.backtest_elapsed_seconds or 0:.1f} seconds"
            )

            # Show the immutable experiment identifier while retaining every
            # existing metric and report control.
            if result.get("run_id"):
                st.caption(f"Experiment: {result['run_id']}")

            scope_name = displayed_scope(result)
            period_start = result.get("comparison_start") or result.get("actual_start")
            period_end = result.get("comparison_end") or result.get("actual_end")
            st.info(
                f"Scope: {scope_name} | Evaluation period: "
                f"{period_start or 'N/A'} to {period_end or 'N/A'}"
            )

            summary = result["summary"]

            c1, c2, c3 = st.columns(3)

            c1.metric("Trades", summary["Trades"])
            c2.metric("Win Rate", f"{summary['WinRate']}%")
            c3.metric("Profit Factor", summary["ProfitFactor"])

            c1, c2, c3 = st.columns(3)

            c1.metric("Net Profit", summary["NetProfit"])
            c2.metric("Return %", summary["TotalReturn"])
            c3.metric("Drawdown", summary["MaxDrawdown"])

            if (
                result.get("backtest_scope") == VALIDATED_OOS
                and result.get("mode") == "STRATEGY_ONLY"
            ):
                reference = {"Return": 65.81, "Profit Factor": 1.28, "Drawdown": 17.91}
                st.caption(
                    "Phase 5 reference · Return 65.81% · Profit Factor 1.28 · "
                    "Drawdown 17.91%"
                )
                deltas = st.columns(3)
                deltas[0].metric(
                    "Return vs Reference", f"{summary['TotalReturn']:.2f}%",
                    delta=f"{summary['TotalReturn'] - reference['Return']:+.2f} pp",
                )
                deltas[1].metric(
                    "PF vs Reference", f"{summary['ProfitFactor']:.2f}",
                    delta=f"{summary['ProfitFactor'] - reference['Profit Factor']:+.2f}",
                )
                deltas[2].metric(
                    "DD vs Reference", f"{summary['MaxDrawdown']:.2f}%",
                    delta=f"{reference['Drawdown'] - summary['MaxDrawdown']:+.2f} pp",
                    delta_color="normal",
                )

            equity_path = Path(result.get("run_directory", "")) / "equity_curve.csv"
            if equity_path.is_file():
                equity = pd.read_csv(equity_path)
                if not equity.empty and "Equity" in equity:
                    equity["Peak"] = equity["Equity"].cummax()
                    equity["DrawdownPercent"] = (
                        (equity["Peak"] - equity["Equity"]) / equity["Peak"] * 100
                    ).fillna(0)
                    chart_col, dd_col = st.columns(2)
                    with chart_col:
                        section_header("Equity Curve", "Run-level portfolio path")
                        st.line_chart(equity[["Equity"]])
                    with dd_col:
                        section_header("Drawdown", "Distance from running peak")
                        st.area_chart(equity[["DrawdownPercent"]], color="#dc2626")

            st.caption(
                f"Signals: {result['signals']} | "
                f"Executed: {result['trades']} | "
                f"Rejected: {result['rejected']} | "
                f"Effective Max Positions: "
                f"{result['effective_max_positions']}"
            )

            if result.get("walk_forward"):

                st.subheader("🤖 Walk-Forward AI Validation")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("OOS Accuracy", result["walk_forward"]["accuracy"])
                c2.metric("OOS F1", result["walk_forward"]["f1"])
                c3.metric("Historical mode", result["mode"])
                c4.metric("OOS predictions", result["walk_forward"]["predictions"])
                st.caption(
                    "This run uses only fold-local out-of-sample AI predictions. "
                    "Use the Phase 4 comparison report to compare all overlay modes."
                )

            with st.expander("📐 Professional Metrics"):

                c1, c2, c3 = st.columns(3)

                c1.metric(
                    "Sharpe Ratio",
                    summary["SharpeRatio"]
                )

                c2.metric(
                    "Sortino Ratio",
                    summary["SortinoRatio"]
                )

                c3.metric(
                    "Calmar Ratio",
                    summary["CalmarRatio"]
                )

                c1, c2, c3 = st.columns(3)

                c1.metric(
                    "CAGR",
                    f"{summary['CAGR']}%"
                )

                c2.metric(
                    "Recovery Factor",
                    summary["RecoveryFactor"]
                )

                c3.metric(
                    "Kelly %",
                    f"{summary['KellyPercent']}%"
                )

                c1, c2, c3 = st.columns(3)

                c1.metric(
                    "Exposure %",
                    f"{summary['ExposurePercent']}%"
                )

                c2.metric(
                    "Max Consecutive Wins",
                    summary["MaxConsecutiveWins"]
                )

                c3.metric(
                    "Max Consecutive Losses",
                    summary["MaxConsecutiveLosses"]
                )

                st.caption(
                    f"Backtest Period: {summary['BacktestDays']} يوم "
                    f"(~{round(summary['BacktestDays']/365.25, 1)} سنة) | "
                    f"Trades/Year: {summary['TradesPerYear']} | "
                    f"Max Drawdown Amount: {summary['MaxDrawdownAmount']}"
                )

                st.caption(
                    "ℹ️ Sharpe/Sortino هنا محسوبين على أساس عائد % "
                    "كل صفقة (Trade-Based) وتقريب سنوي بعدد الصفقات "
                    "فى السنة - مش على منحنى يومي، فده تقريب عملي "
                    "مش رقم أكاديمي دقيق 100%."
                )

            with st.expander("📊 Rejected Trades Breakdown"):

                st.write("**Signal Level** (قبل حتى ما تبقى صفقة مرشحة)")

                st.table(
                    sorted(
                        result["signal_rejections"].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )
                )

                st.write("**Portfolio Level** (صفقة مرشحة بس اترفضت)")

                st.table(
                    sorted(
                        result["portfolio_rejections"].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )
                )

            if result["errors"]:

                with st.expander(

                    f"⚠️ Failed Symbols ({len(result['errors'])})"

                ):

                    st.caption(
                        "الأسهم دي فشلت فى التحميل من مزود البيانات "
                        "(بيانات مش كافية / رمز غير موجود / موقوف "
                        "عن التداول). التفاصيل محفوظة كمان فى "
                        "reports/failed_symbols.csv"
                    )

                    st.dataframe(
                        result["errors"],
                        hide_index=True,
                        use_container_width=True
                    )

        st.divider()

        if st.button(
            "🤖 Train AI",
            use_container_width=True
        ):

            with st.spinner("Training AI Model..."):

                trainer = AITrainer()

                result = trainer.train()

                trainer.save()

            st.success("AI Training Completed Successfully")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Accuracy", f"{result['accuracy']}%")
            c2.metric("Precision", f"{result['precision']}%")
            c3.metric("Recall", f"{result['recall']}%")
            c4.metric("F1", f"{result['f1']}%")
            st.caption(
                f"Walk-forward predictions: {result['predictions']} | "
                f"ROC-AUC: {result['roc_auc']}"
            )

        st.button(
            "📂 Open Reports",
            use_container_width=True,
            disabled=True
        )

        if st.button(
            "🔄 Reset Settings",
            use_container_width=True
        ):

            settings.reset()
            st.session_state.settings_saved_notice = (
                "Settings restored successfully."
            )
            st.rerun()

    # ==================================
    # Save
    # ==================================

    # Build one complete snapshot and compare it to the loaded file.  Saving is
    # deliberately explicit and atomic at the SettingsManager level; opening
    # the page or running a backtest never writes configuration implicitly.
    proposed_settings = deepcopy(settings.data)
    updated_strategy = dict(strategy)
    updated_strategy.update({
        "min_score": min_score,
        "min_confidence": min_confidence,
        "min_rr": min_rr,
        "min_trend": min_trend,
        "min_momentum": min_momentum,
        "min_volume": min_volume,
        "max_rr": max_rr if max_rr_enabled else 100.0,
        "require_candle_confirmation": require_candle,
        "require_market_analyzer": require_market_analyzer,
        "require_quality_filter": quality_filter_enabled,
        "quality_min_adx": quality_min_adx,
        "quality_min_volume_ratio": quality_min_volume_ratio,
        "quality_min_atr_percent": quality_min_atr_percent,
        "quality_min_resistance_room": quality_min_resistance_room,
    })
    proposed_settings["strategy"] = updated_strategy
    proposed_settings["backtest"] = {
        "entry_wait_days": entry_wait,
        "ai_mode": ai_backtest_mode,
        "walk_forward_splits": walk_forward_splits,
        "exit_mode": exit_mode,
        "max_holding_days": holding,
        "move_to_breakeven": breakeven,
        "partial_exit": partial_exit_enabled,
        "partial_percent": partial_percent,
        "risk_mode": backtest["risk_mode"],
        "risk_percent": risk_percent,
        "trailing_mode": trailing_mode,
        "trailing_atr": trailing_atr,
        "trailing_enabled": trailing_enabled,
        "allow_overlapping_trades": allow_overlap,
        "max_open_positions": max_open_positions,
        "max_portfolio_risk_percent": max_portfolio_risk_percent,
        "initial_capital": initial_capital,
        "commission": backtest["commission"],
        "slippage": backtest["slippage"],
    }
    proposed_settings["ai"] = {
        "enabled": ai_enabled,
        "min_probability": ai_probability,
    }

    settings_dirty = settings_are_dirty(settings.data, proposed_settings)
    saved_notice = st.session_state.pop("settings_saved_notice", None)
    if saved_notice:
        st.success(saved_notice)
    elif settings_dirty:
        st.warning("You have unsaved settings changes.")
    else:
        st.caption("No unsaved changes — Save Settings is disabled by design.")

    if st.button(
        "💾 Save Settings",
        use_container_width=True,
        disabled=not settings_dirty or st.session_state.backtest_running,
        help=(
            "Change a setting to enable saving."
            if not settings_dirty
            else "Persist the visible settings to config/settings.json."
        ),
        key="save_settings_button",
    ):
        settings.save(proposed_settings)
        settings.reload()
        st.session_state.settings_saved_notice = "Settings saved successfully."
        st.rerun()

    st.info(
        "Changes will be applied the next time you run a Scan or Backtest."
    )
