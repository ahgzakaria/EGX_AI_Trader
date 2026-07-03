import streamlit as st

from config.settings_manager import settings
from services.backtest_service import run_backtest
from ai.trainer import AITrainer


def show_settings():

    settings.reload()

    strategy = settings.get("strategy")
    backtest = settings.get("backtest")
    ai = settings.get("ai")

    st.title("⚙️ Settings")
    st.caption("EGX AI Trader Configuration")

    tab1, tab2, tab3, tab4 = st.tabs(

        [

            "📈 Strategy",

            "💰 Backtest",

            "🤖 AI",

            "🚀 Tools"

        ]

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

        if st.button(
            "▶ Run Backtest",
            use_container_width=True
        ):

            progress = st.progress(0)
            status = st.empty()
            percent_text = st.empty()
            elapsed_text = st.empty()
            result = None
     
            for update in run_backtest():

                if update["type"] == "progress":

                   progress.progress(
                       update["current"] / update["total"]
                   )

                   status.info(
                       f"Current Symbol : {update['symbol']}"
                   )

                   percent_text.write(
                       f"Progress : {update['current']} / {update['total']} "
                       f"({update['percent']}%)"
                   )

                elif update["type"] == "finished":

                    result = update

            progress.empty()
            status.empty()
            percent_text.empty()

            elapsed_text.success(
                f"Finished in {result['elapsed']}"
            )

            st.success("Backtest Finished Successfully")

            summary = result["summary"]

            c1, c2, c3 = st.columns(3)

            c1.metric("Trades", summary["Trades"])
            c2.metric("Win Rate", f"{summary['WinRate']}%")
            c3.metric("Profit Factor", summary["ProfitFactor"])

            c1, c2, c3 = st.columns(3)

            c1.metric("Net Profit", summary["NetProfit"])
            c2.metric("Return %", summary["TotalReturn"])
            c3.metric("Drawdown", summary["MaxDrawdown"])

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

            c1, c2, c3 = st.columns(3)

            c1.metric(
                "Accuracy",
                f"{result['accuracy']}%"
            )

            c2.metric(
                "Cross Validation",
                f"{result['cv_mean']}%"
            )

            c3.metric(
                "Std Deviation",
                f"{result['cv_std']}%"
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

            st.success("Settings restored successfully.")

    # ==================================
    # Save
    # ==================================

    if st.button(
        "💾 Save Settings",
        use_container_width=True
    ):

        settings.set("strategy", {

            "min_score": min_score,
            "min_confidence": min_confidence,
            "min_rr": min_rr,
            "min_trend": min_trend,
            "min_momentum": min_momentum,
            "min_volume": min_volume

        })

        settings.set("backtest", {

            "entry_wait_days": entry_wait,
            "exit_mode": exit_mode,
            "max_holding_days": holding,
            "move_to_breakeven": breakeven,

            "partial_exit": backtest["partial_exit"],
            "partial_percent": backtest["partial_percent"],

            "risk_mode": backtest["risk_mode"],
            "risk_percent": backtest["risk_percent"],

            "trailing_mode": backtest["trailing_mode"],
            "trailing_atr": backtest["trailing_atr"],

            "allow_overlapping_trades": backtest["allow_overlapping_trades"],

            "initial_capital": backtest["initial_capital"],

            "commission": backtest["commission"],
            "slippage": backtest["slippage"]

        })

        settings.set("ai", {

            "enabled": ai_enabled,
            "min_probability": ai_probability

        })

        settings.reload()

        st.success("Settings saved successfully.")

    st.info(
        "Changes will be applied the next time you run a Scan or Backtest."
    )