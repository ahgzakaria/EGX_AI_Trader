import streamlit as st

from dashboard.home import show_dashboard
from dashboard.watchlist import show_watchlist
from dashboard.settings import show_settings


st.set_page_config(
    page_title="EGX AI Trader",
    page_icon="📈",
    layout="wide"
)

# ==================================
# Sidebar
# ==================================

st.sidebar.title("📈 EGX AI Trader")

page = st.sidebar.radio(

    "Navigation",

    [

        "📈 Dashboard",

        "⭐ Watchlist",

        "⚙️ Settings"

    ]

)

# ==================================
# Pages
# ==================================

if page == "📈 Dashboard":

    show_dashboard()

elif page == "⭐ Watchlist":

    show_watchlist()

elif page == "⚙️ Settings":

    show_settings()