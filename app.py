"""Streamlit entry point: three workspaces, one per way of trading.

The three are not cosmetic groupings. They hold different timeframes with
different economics, and the split exists so a number from one is never read
as if it came from the other:

* **SWING** works on daily bars over days to weeks. The round-trip cost of
  0.46% plus spread is a minor term there -- on a twenty-day hold it is 46% of
  the average move, and the strategy has 559,483 stock-days of history to be
  validated against.
* **SCALPING** works inside one session, where that same cost is roughly ten
  times the average intraday drift of +0.078%. Everything on that surface has
  to earn its way past a cost that dominates it.
* **AI ANALYSIS** is per-symbol narrative research and belongs to neither; it
  was previously buried under a "Research & System" heading with the
  diagnostics, which said nothing about what it is for.

System Health and Settings are tools rather than a fourth way of trading, and
the legacy research pages stay reachable only from System Health.
"""

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
from dashboard.orb_signals import show_orb_signals
from dashboard.settings import show_settings
from dashboard.swing_signals import show_swing_signals
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

navigation = st.navigation({
    # Days to weeks, on daily bars. The timeframe where the cost of trading
    # stops being the dominant term.
    "سوينج · SWING": [
        st.Page(show_dashboard, title="Daily Dashboard", icon="📊", default=True),
        st.Page(
            show_swing_signals,
            title="Swing Breakout",
            icon="📈",
            url_path="swing-breakout",
        ),
        st.Page(show_watchlist, title="Watchlist", icon="⭐"),
        st.Page(show_stock_details_page, title="Stock Details", icon="🔎"),
    ],
    # Inside one session. The legacy scalping pages (Scalping Dashboard /
    # Active Trades / History, backed by `scalping/`,
    # `scalping_expected_range/` and `scalping_uptrend_pullback/`) are retired
    # from navigation. Their code and research databases are intentionally
    # left in place: that removal is a separate, deliberate change, and
    # ~19k lines are still imported by two `core`/`services` modules.
    "سكالبنج · SCALPING": [
        st.Page(
            show_orb_signals,
            title="ORB Signals",
            icon="🎯",
            url_path="orb-signals",
        ),
    ],
    # Per-symbol narrative research, on its own rather than filed with the
    # diagnostics. Compatibility name used by integration tests:
    # AI Stock Analysis.
    "تحليل · AI ANALYSIS": [
        st.Page(show_ai_stock_analysis, title="AI Analysis", icon="🤖"),
    ],
    # Tools, not a fourth way of trading. The legacy research and diagnostic
    # views remain reachable only from System Health.
    "النظام · SYSTEM": [
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
