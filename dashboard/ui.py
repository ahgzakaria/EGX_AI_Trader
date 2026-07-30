"""Shared visual system for the EGX AI Trader Streamlit application.

Presentation only. Nothing here changes widget behaviour, strategy logic, or any
numeric value — it styles and lays out what the pages already compute.
"""

from __future__ import annotations

import html

import streamlit as st

from dashboard.formatting import status_label, status_tone

# Semantic tone -> (background, border, text) for dark badges.
_TONE = {
    "green": ("rgba(16,185,129,.16)", "rgba(16,185,129,.45)", "#34d399"),
    "amber": ("rgba(217,119,6,.16)", "rgba(245,158,11,.45)", "#fbbf24"),
    "red": ("rgba(220,38,38,.16)", "rgba(248,113,113,.45)", "#f87171"),
    "gray": ("rgba(100,116,139,.16)", "rgba(148,163,184,.35)", "#94a3b8"),
    "blue": ("rgba(37,99,235,.16)", "rgba(59,130,246,.45)", "#60a5fa"),
}


def apply_global_style():
    """Dark trading-terminal theme (presentation only)."""
    st.markdown(
        """
        <style>
        :root {
            --bg: #0b1220;
            --bg-2: #0e1729;
            --surface: #131c30;
            --surface-2: #17223b;
            --border: #223049;
            --text: #e6edf7;
            --muted: #8ea1bd;
            --green: #34d399; --amber: #fbbf24; --red: #f87171;
            --blue: #60a5fa; --gray: #94a3b8;
        }
        .stApp { background: var(--bg); color: var(--text); font-size:16px; }
        /* clear the fixed Streamlit toolbar so the page title is never clipped */
        .block-container { max-width: 1640px; padding-top: 3.4rem; padding-bottom: 2.4rem; }
        header[data-testid="stHeader"] { background: transparent; }
        [data-testid="stSidebar"] { background: #080e1a; border-right: 1px solid var(--border); }
        [data-testid="stSidebar"] * { color: #c7d3e6; }
        [data-testid="stSidebarNav"] a {
            border-radius: 8px; min-height:42px; font-size:.96rem;
        }
        [data-testid="stSidebarNav"] a:hover { background: rgba(255,255,255,.06); }
        [data-testid="stSidebarNav"] a[aria-current="page"] {
            background: rgba(59,130,246,.18); font-weight: 700;
        }
        h1,h2,h3,h4 { color: var(--text); letter-spacing: -.01em; }
        h1 { font-size: 1.75rem !important; }
        p, label, .stMarkdown { color: var(--text); font-size:1rem; line-height:1.65; }
        .stCaption, [data-testid="stCaptionContainer"] { color: var(--muted) !important; }

        /* metrics -> compact dark cards */
        [data-testid="stMetric"] {
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 12px; padding: .7rem .85rem; min-height: 0;
        }
        [data-testid="stMetricLabel"] { color: var(--muted); font-weight: 600; font-size: .78rem; }
        [data-testid="stMetricValue"] { color: var(--text); font-weight: 750; font-size: 1.5rem; }
        [data-testid="stMetricDelta"] { font-size: .78rem; }

        .stButton > button, .stDownloadButton > button {
            border-radius: 9px; min-height: 46px; font-weight: 700;
            font-size:1rem; padding:.55rem 1rem;
            background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(90deg,#2563eb,#0891b2); border: 0; color: white;
        }
        [data-testid="stDataFrame"] {
            border: 1px solid var(--border); border-radius: 12px; overflow: hidden;
            font-size:15px;
        }
        [data-testid="stDataFrame"] [role="columnheader"] {
            font-size:15px; font-weight:800;
        }
        [data-testid="stDataFrame"] [role="gridcell"] {
            font-size:15px; min-height:40px;
        }
        [data-baseweb="tab-list"] {
            gap: .25rem; background: var(--surface); border: 1px solid var(--border);
            border-radius: 10px; padding: .25rem;
        }
        [data-baseweb="tab"] {
            border-radius: 8px; padding: .6rem 1rem; color: var(--muted);
            font-size:.96rem; font-weight:700;
        }
        [data-baseweb="tab"][aria-selected="true"] { background: rgba(59,130,246,.18); color: var(--text); }
        [data-testid="stExpander"] { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
        div[data-testid="stAlert"] { border-radius: 10px; }
        [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
        [data-baseweb="select"] > div {
            background: var(--surface-2); color: var(--text); border-color: var(--border);
        }

        /* --- custom components --- */
        .egx-hero { display:flex; align-items:center; justify-content:space-between;
            background: linear-gradient(120deg,#0e1729,#152241); border:1px solid var(--border);
            border-radius: 13px; padding: .9rem 1.35rem; margin-bottom: .7rem; }
        .egx-hero h1 { margin:0 !important; font-size:1.6rem !important; letter-spacing:-.02em; }
        .egx-hero p { margin:.25rem 0 0; color: var(--muted); font-size:.84rem; }
        /* badge sits vertically centred, clear of the top-right border/toolbar */
        .egx-hero .egx-badge { background: rgba(59,130,246,.14); border:1px solid rgba(59,130,246,.35);
            color:#93c5fd; padding:.22rem .6rem; border-radius:999px; font-size:.64rem; font-weight:700;
            letter-spacing:.03em; white-space:nowrap; margin:.15rem .35rem 0 0; align-self:center; }

        .egx-statusbar { display:flex; flex-wrap:wrap; gap:.4rem; align-items:center;
            background: var(--surface); border:1px solid var(--border); border-radius:11px;
            padding:.5rem .7rem; margin-bottom:.7rem; }
        .egx-chip { display:inline-flex; align-items:center; gap:.35rem; font-size:.76rem;
            font-weight:650; padding:.22rem .55rem; border-radius:999px; white-space:nowrap; }
        .egx-chip .k { color: var(--muted); font-weight:600; }
        .egx-sep { width:1px; height:16px; background: var(--border); margin:0 .1rem; }

        .egx-badge-pill { display:inline-block; padding:.16rem .5rem; border-radius:999px;
            font-size:.74rem; font-weight:700; line-height:1.3; }

        .egx-metric { background: var(--surface); border:1px solid var(--border);
            border-radius:11px; padding:.55rem .8rem; }
        .egx-metric .v { font-size:1.45rem; font-weight:780; line-height:1.0; }
        .egx-metric .lar { color: var(--text); font-size:.84rem; font-weight:700; margin-top:.1rem; direction:rtl; }
        .egx-metric .len { color: var(--muted); font-size:.66rem; font-weight:600; text-transform:uppercase;
            letter-spacing:.04em; margin-top:.02rem; }
        .egx-metric .s { color: var(--muted); font-size:.68rem; margin-top:.18rem; }
        .egx-metric .tag { display:inline-block; font-size:.57rem; font-weight:700; padding:.03rem .3rem;
            border-radius:5px; background:rgba(148,163,184,.14); color:var(--muted); margin-top:.2rem;
            text-transform:uppercase; letter-spacing:.03em; }

        .egx-section { display:flex; align-items:baseline; justify-content:space-between; margin:.9rem 0 .45rem; }
        .egx-section h3 { margin:0; font-size:1.02rem; }
        .egx-section span { color: var(--muted); font-size:.8rem; }
        .egx-empty { text-align:center; padding:1.6rem 1rem; border:1px dashed var(--border);
            border-radius:12px; background: var(--surface); color: var(--muted); }
        .egx-empty strong { display:block; color: var(--text); margin-bottom:.25rem; }

        .egx-rangebar { position:relative; height:8px; border-radius:999px;
            background: linear-gradient(90deg,#10233f,#12325a); border:1px solid var(--border); }
        .egx-rangebar .dot { position:absolute; top:-3px; width:12px; height:12px; border-radius:50%;
            background:#e6edf7; border:2px solid #0b1220; transform:translateX(-50%); }
        .egx-oppcard { background: var(--surface); border:1px solid var(--border);
            border-left:3px solid var(--green); border-radius:11px; padding:.7rem .85rem; height:100%; }
        .egx-system-link { display:inline-block; padding:.55rem .85rem; border-radius:9px;
            background:var(--surface-2); border:1px solid var(--border); color:#93c5fd !important;
            font-weight:750; text-decoration:none; margin:.35rem 0 .65rem; }
        @media (max-width: 900px) {
            .block-container { padding-left:1rem; padding-right:1rem; }
            .egx-hero { align-items:flex-start; gap:.65rem; }
            [data-baseweb="tab-list"] { overflow-x:auto; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# --- reusable components ----------------------------------------------------


def page_header(title, subtitle, icon="📈", badge=None):
    badge_html = f'<span class="egx-badge">{html.escape(str(badge))}</span>' if badge else ""
    st.markdown(
        f"""<div class="egx-hero"><div>
            <h1>{html.escape(icon)}&nbsp;{html.escape(title)}</h1>
            <p>{html.escape(subtitle)}</p></div>{badge_html}</div>""",
        unsafe_allow_html=True)


def section_header(title, caption=""):
    st.markdown(
        f'<div class="egx-section"><h3>{html.escape(title)}</h3>'
        f'<span>{html.escape(caption)}</span></div>', unsafe_allow_html=True)


def empty_state(title, message, icon="○"):
    st.markdown(
        f'<div class="egx-empty"><div style="font-size:1.5rem">{html.escape(icon)}</div>'
        f'<strong>{html.escape(title)}</strong><span>{html.escape(message)}</span></div>',
        unsafe_allow_html=True)


def badge_html(text, tone="gray", title=""):
    bg, border, fg = _TONE.get(tone, _TONE["gray"])
    t = f' title="{html.escape(str(title))}"' if title else ""
    return (f'<span class="egx-badge-pill"{t} style="background:{bg};'
            f'border:1px solid {border};color:{fg}">{html.escape(str(text))}</span>')


def status_badge(status):
    """Render a status/advisory as a coloured badge (full string in tooltip)."""
    st.markdown(badge_html(status_label(status), status_tone(status), title=str(status)),
                unsafe_allow_html=True)


def _calendar_banner(icon, accent, title, detail):
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:.9rem;background:var(--surface);'
        f'border:1px solid var(--border);border-left:4px solid {accent};border-radius:11px;'
        f'padding:.75rem 1rem;margin:.35rem 0 .6rem">'
        f'<div style="font-size:1.4rem">{icon}</div>'
        f'<div><div style="font-weight:800;letter-spacing:.3px">{html.escape(title)}</div>'
        f'<div style="color:var(--muted);font-size:.82rem">{detail}</div></div></div>',
        unsafe_allow_html=True)


def egx_holiday_banner(now=None):
    """Render an EGX session-calendar notice for any non-normal session state.

    Presentation only — reads the dynamic EGX calendar service. Covers confirmed
    holiday, pending-review announcements, and uncertain status. Returns True when a
    banner was shown. Never fabricates a session or auto-confirms an uncertain day.
    """
    try:
        from core.egx_calendar import session_status
        s = session_status(now)
    except Exception:
        return False
    nxt = str(s.next_trading_session or "")
    try:
        from datetime import date as _date
        nxt = _date.fromisoformat(nxt).strftime("%d %b %Y").lstrip("0")
    except (ValueError, TypeError):
        pass

    if s.status in ("HOLIDAY_CONFIRMED", "EXCEPTIONAL_CLOSURE"):
        name = s.holiday_name or "EGX Holiday"
        _calendar_banner("🕌", "#60a5fa", f"EGX HOLIDAY · {name}",
                         f"Market Closed — no live trading today. Next Session: "
                         f"<b>{html.escape(nxt)}</b> · last values are the previous session snapshot.")
        return True
    if s.status == "HOLIDAY_PENDING_REVIEW":
        _calendar_banner("📋", "#fbbf24", "CALENDAR REVIEW REQUIRED",
                         f"An unconfirmed announcement suggests a possible closure "
                         f"({html.escape(str(s.holiday_name or 'unnamed'))}). Live signals are paused "
                         f"until confirmed — open SYSTEM → Trading Calendar to review.")
        return True
    if s.status == "SESSION_STATUS_UNCERTAIN":
        _calendar_banner("⚠", "#f87171", "SESSION STATUS UNCERTAIN",
                         "Live research is paused pending manual calendar review. "
                         "Inspect SYSTEM → Trading Calendar / System Health.")
        return True
    if s.status in ("LATE_OPEN", "EARLY_CLOSE", "PARTIAL_SESSION"):
        _calendar_banner("🕘", "#fbbf24", f"PARTIAL SESSION · {s.status.replace('_', ' ').title()}",
                         f"Adjusted hours today — continuous {html.escape(str(s.continuous_start))}"
                         f"–{html.escape(str(s.continuous_end))}. No new entries after the adjusted close.")
        return True
    return False


def status_bar(items):
    """A compact horizontal status bar. items: list of (key, value, tone)."""
    chips = []
    for i, (key, value, tone) in enumerate(items):
        _, _, fg = _TONE.get(tone, _TONE["gray"])
        bg = _TONE.get(tone, _TONE["gray"])[0]
        chips.append(
            f'<span class="egx-chip" style="background:{bg};color:{fg}">'
            f'<span class="k">{html.escape(str(key))}</span>{html.escape(str(value))}</span>')
    st.markdown('<div class="egx-statusbar">' + "".join(chips) + "</div>", unsafe_allow_html=True)


def metric_card(label_ar, value, label_en="", sub="", tone=None, tag=""):
    """Bilingual metric card: Arabic primary label + smaller English secondary."""
    color = f"color:{_TONE.get(tone, ('', '', 'var(--text)'))[2]}" if tone else ""
    st.markdown(
        f'<div class="egx-metric"><div class="v" style="{color}">{html.escape(str(value))}</div>'
        f'<div class="lar">{html.escape(str(label_ar))}</div>'
        + (f'<div class="len">{html.escape(str(label_en))}</div>' if label_en else "")
        + (f'<div class="s">{html.escape(str(sub))}</div>' if sub else "")
        + (f'<div class="tag">{html.escape(str(tag))}</div>' if tag else "")
        + "</div>", unsafe_allow_html=True)


def range_position_bar(position_percent, low_label="", high_label=""):
    """A compact Expected-Low ── Current ── Expected-High indicator."""
    try:
        p = max(0.0, min(100.0, float(position_percent)))
    except (TypeError, ValueError):
        p = 0.0
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;font-size:.72rem;color:var(--muted)">'
        f'<span>{html.escape(str(low_label))}</span><span>{html.escape(str(high_label))}</span></div>'
        f'<div class="egx-rangebar"><div class="dot" style="left:{p}%"></div></div>',
        unsafe_allow_html=True)


def sidebar_brand():
    st.sidebar.markdown(
        """<div style="padding:.4rem .35rem .9rem">
          <div style="font-size:1.12rem;font-weight:800;color:#e6edf7">📈 متداول البورصة المصرية</div>
          <div style="font-size:.82rem;color:#8ea1bd;margin-top:.2rem">بحث كمي ومتابعة آمنة</div>
        </div>""", unsafe_allow_html=True)
