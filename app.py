import streamlit as st

from dashboard.home import show_dashboard


st.set_page_config(
    page_title="EGX AI Trader",
    page_icon="📈",
    layout="wide"
)

show_dashboard()