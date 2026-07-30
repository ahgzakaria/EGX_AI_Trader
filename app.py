"""Streamlit entry point with three simple, task-oriented workspaces."""

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
from dashboard.home import show_dashboard, show_stock_details_page
from dashboard.scalping import (
    show_active_scalping_trades,
    show_scalping_dashboard,
    show_scalping_history,
)
from dashboard.settings import show_settings
from dashboard.system_health import show_system_health
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

# Primary navigation contains only the tasks a trader needs day to day. Older
# research and diagnostic views remain available through System Health.
navigation = st.navigation({
    "SWING / DAILY": [
        st.Page(show_dashboard, title="Daily Dashboard", icon="📊", default=True),
        st.Page(show_watchlist, title="Watchlist", icon="⭐"),
        st.Page(show_stock_details_page, title="Stock Details", icon="🔎"),
    ],
    "SCALPING": [
        st.Page(show_scalping_dashboard, title="Scalping Dashboard", icon="⚡"),
        st.Page(show_active_scalping_trades, title="Active Trades", icon="📍"),
        st.Page(show_scalping_history, title="History", icon="🗂️"),
    ],
    "RESEARCH & SYSTEM": [
        # Compatibility name used by integration tests: AI Stock Analysis.
        st.Page(show_ai_stock_analysis, title="AI Analysis", icon="🤖"),
        st.Page(
            show_system_health,
            title="System Health",
            icon="🩺",
            url_path="system-health",
        ),
        st.Page(show_settings, title="Settings", icon="⚙️"),
    ],
})
navigation.run()
