"""Streamlit entry point with three deliberately separated workspaces."""

import streamlit as st

# Load .env once, centrally, BEFORE any settings/provider initialization so real OS
# environment variables win and no module needs its own load_dotenv().
from core.environment import load_project_environment

load_project_environment()

from dashboard.backtest_state import (
    initialize_backtest_state,
    recover_interrupted_backtest,
)
from dashboard.ai_stock_analysis import show_ai_stock_analysis
from dashboard.compare_runs import show_compare_runs
from dashboard.decision_support import show_decision_analytics
from dashboard.expected_range_scalper import show_expected_range_scalper
from dashboard.forward_testing import show_forward_testing
from dashboard.home import show_dashboard, show_stock_details_page
from dashboard.opportunities import show_opportunities
from dashboard.run_history import show_run_history
from dashboard.scalping import (
    show_active_scalping_trades,
    show_scalping_dashboard,
    show_scalping_history,
    show_scalping_settings,
)
from dashboard.eodhd_migration_review import show_eodhd_migration_review
from dashboard.settings import show_settings
from dashboard.trading_calendar import show_trading_calendar
from dashboard.system_health import (
    show_provider_diagnostics,
    show_replay_run,
    show_system_health,
)
from dashboard.ui import apply_global_style, sidebar_brand
from dashboard.watchlist import show_watchlist
from services.experiment_tracking import RunRepository


st.set_page_config(page_title="EGX AI Trader", page_icon="📈", layout="wide")

apply_global_style()
sidebar_brand()

# A browser disconnect can abort a long backtest before its normal cleanup.
# Recover only that tracked run on the next rerun.
initialize_backtest_state(st.session_state)
recover_interrupted_backtest(st.session_state, RunRepository)

# The default Swing page keeps Research & System collapsed until requested.
navigation = st.navigation({
    "SWING / DAILY": [
        st.Page(show_dashboard, title="Dashboard", icon="📊", default=True),
        st.Page(show_watchlist, title="Watchlist", icon="⭐"),
        st.Page(show_stock_details_page, title="Stock Details", icon="🔎"),
    ],
    "SCALPING": [
        st.Page(show_scalping_dashboard, title="Dashboard", icon="⚡"),
        st.Page(show_opportunities, title="Opportunities", icon="🎯"),
        st.Page(show_expected_range_scalper, title="Expected Range Scalper", icon="📐"),
        st.Page(show_active_scalping_trades, title="Active Paper Trades", icon="📍"),
        st.Page(show_scalping_history, title="History", icon="🗂️"),
        st.Page(show_scalping_settings, title="Settings", icon="🛠️"),
    ],
    "RESEARCH & SYSTEM": [
        st.Page(show_ai_stock_analysis, title="AI Stock Analysis", icon="🤖"),
        st.Page(show_settings, title="Backtest", icon="⚙️"),
        st.Page(show_run_history, title="Run History", icon="🧪"),
        st.Page(show_compare_runs, title="Compare Runs", icon="⚖️"),
        st.Page(show_replay_run, title="Replay", icon="🔁"),
        st.Page(show_decision_analytics, title="Performance Analytics", icon="📐"),
        st.Page(show_forward_testing, title="Forward Testing", icon="🛰️"),
        st.Page(show_system_health, title="System Health", icon="🩺"),
        st.Page(show_provider_diagnostics, title="Provider Diagnostics", icon="🗄️"),
        st.Page(show_trading_calendar, title="Trading Calendar", icon="📅"),
        st.Page(show_eodhd_migration_review, title="EODHD Migration Review", icon="🧭"),
    ],
})
navigation.run()
