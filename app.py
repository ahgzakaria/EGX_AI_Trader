"""Streamlit entry point: two workspaces, one per way of trading.

* **SWING** works on daily bars over days to weeks. The round-trip cost of
  0.46% plus spread is a minor term there -- on a twenty-day hold it is 46% of
  the average move, and the strategy has 559,483 stock-days of history to be
  validated against.
* **AI ANALYSIS** is per-symbol narrative research and belongs to neither
  timeframe; it was previously buried under a "Research & System" heading with
  the diagnostics, which said nothing about what it is for.

There was a third. SCALPING worked inside one session, where the same cost is
1,030% of the average intraday drift of +0.078% -- and on 2026-08-25 the live
signals confirmed what that ratio implied. It is retired, and the reason sits
beside its former place below.

System Health and Settings are tools rather than a way of trading, and the
retired research pages stay reachable only from System Health.
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
from dashboard.confirmed_breakout import show_confirmed_breakout
from dashboard.home import show_dashboard, show_stock_details_page
from dashboard.orb_signals import show_orb_signals
from dashboard.sector_flow import show_sector_flow
from dashboard.settings import show_settings
from dashboard.swing_signals import show_swing_signals
from dashboard.system_health import show_system_health
from dashboard.ui import apply_global_style, sidebar_brand, sidebar_health
from dashboard.watchlist import show_watchlist
from services.experiment_tracking import RunRepository


st.set_page_config(page_title="EGX AI Trader", page_icon="📈", layout="wide")

apply_global_style()
sidebar_brand()
# Above the navigation, on every page: the failures this project has actually
# suffered were silences, and none of them were visible from the page you
# happened to be on when they started.
sidebar_health()

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
        # A close relative of Swing Breakout, not a rival to it: two
        # independent passes over this market landed on the same trigger, which
        # is the strongest thing either of them has going. It sits beside
        # rather than inside because what differs is measured -- a
        # close-position gate, a calm gate, a stop, and a price-limit guard --
        # and because it is scored through the portfolio simulator, so its
        # numbers are comparable to the Daily Dashboard strategy's line for
        # line. See docs/audits/strategies/CONFIRMED_VOLUME_BREAKOUT.md.
        st.Page(
            show_confirmed_breakout,
            title="Confirmed Breakout",
            icon="🚀",
            url_path="confirmed-breakout",
        ),
        st.Page(
            show_sector_flow,
            title="Sector Liquidity",
            icon="🌊",
            url_path="sector-liquidity",
        ),
        st.Page(show_watchlist, title="Watchlist", icon="⭐"),
        st.Page(show_stock_details_page, title="Stock Details", icon="🔎"),
    ],
    # There is no SCALPING workspace any more. ORB Signals joined the earlier
    # retirements (Scalping Dashboard / Active Trades / History, backed by
    # `scalping/`, `scalping_expected_range/` and `scalping_uptrend_pullback/`)
    # on 2026-08-25, on the same evidence they went on: the arithmetic.
    #
    # The round trip is 1,030% of the average intraday move on EGX and 292% at
    # three days; only at ten does the average move first exceed it. Measured
    # on 48 live ORB signals over six sessions, the median best price a signal
    # ever reached was +0.57% against a 0.75% cost, only 46% ever saw a price
    # that covered the cost even with a perfect exit at the day's best tick,
    # and of the 13 that did reach their target only 5 made money.
    #
    # The code, the engine, the collector and the shadow sessions all stay.
    # Retiring the route is not deleting the work, and the page is still
    # reachable from System Health with the rest of the research views.
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
