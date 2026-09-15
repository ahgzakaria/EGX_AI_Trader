"""Shared visual system for the EGX AI Trader Streamlit application.

Presentation only. Nothing here changes widget behaviour, strategy logic, or any
numeric value — it styles and lays out what the pages already compute.
"""

from __future__ import annotations

import html
import logging
from datetime import date

import streamlit as st

from dashboard.formatting import status_label, status_tone

logger = logging.getLogger(__name__)

#: Semantic tone -> its one colour. Everything else a badge needs is derived
#: from it, so a tone cannot drift into being three related-but-different hues.
#: It could: before this, "green" was text #34d399 over a #10b981 background
#: with a #10b981 border -- the design system's green wearing Tailwind's -- and
#: the same was true of amber, red and blue (docs/design/stitch/AUDIT.md §3).
_TONE_COLOUR = {
    "green": (61, 220, 151),      # #3ddc97
    "amber": (255, 176, 32),      # #ffb020
    "red": (255, 92, 108),        # #ff5c6c
    "gray": (139, 151, 170),      # #8b97aa
    "blue": (111, 158, 255),      # #6f9eff
    # Not a sixth shade of grey. "Never measured" has to be distinguishable
    # from "measured and unremarkable", and grey already means the second.
    "unknown": (192, 132, 252),   # #c084fc
}


def _tone(rgb):
    """(background, border, text) for one tone, all from the same colour."""
    r, g, b = rgb
    return (f"rgba({r},{g},{b},.12)", f"rgba({r},{g},{b},.30)",
            f"#{r:02x}{g:02x}{b:02x}")


# Semantic tone -> (background, border, text) for dark badges.
_TONE = {name: _tone(rgb) for name, rgb in _TONE_COLOUR.items()}

#: The palette as literal hex, for the one place CSS variables cannot reach:
#: chart libraries, which take a colour string and render outside the
#: stylesheet. Charts were picking their own -- #2563eb, #0891b2, #dc2626,
#: #64748b -- so the same "grey" on two pages was two different greys, and a
#: drawdown chart was Tailwind red beside a design-system red legend.
#: The same tones as CSS variable references, for inline styles in markup that
#: the browser resolves. Unlike COLOURS this is not a second copy of the
#: palette -- it points at the one declaration in the stylesheet.
TONE_VARS = {name: f"var(--{name})" for name in _TONE_COLOUR}

COLOURS = {name: _tone(rgb)[2] for name, rgb in _TONE_COLOUR.items()}
COLOURS.update({
    "accent": "#2f5fd0",        # the action colour, matching --accent
    "text": "#dfe6f0",
    "muted": "#8b97aa",
    "surface": "#0d121b",
    "bg": "#0a0e15",
    "bg_2": "#080c12",
    # Charts take a literal, so this is the one border that has to be opaque;
    # the stylesheet's --border is the same line expressed as an alpha over the
    # ground, which a chart library cannot composite against.
    "border": "#1b2230",
})


def apply_global_style():
    """Dark trading-terminal theme (presentation only)."""
    st.markdown(
        """
        <style>
        /* Loaded over the network; every rule below names a full fallback
           stack, so the app is legible on the mornings the link fails. */
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

        /* ===== design tokens ==============================================
           One source of truth, from docs/design/stitch/imported/screens/
           00-design-system.html section 2. Where the generated screens drifted
           to Tailwind's own defaults -- #10b981 for green, #f43f5e for red,
           #3b82f6 for blue, #f59e0b for amber -- the spec's values win, because
           the spec derived them from this app's existing palette and the
           substitutions were Stitch's, not a decision. See
           docs/design/stitch/AUDIT.md section 3.

           No shadows anywhere. Depth is one step on the surface ladder plus a
           border, which is what a dense table can carry without noise.
           ================================================================== */
        :root {
            /* Arabic is half of this interface and had no face loaded for it
               at all until now. It is in the *base* stack rather than behind a
               `[dir="rtl"]` rule, because this app writes Arabic and Latin
               inside the same text node and marks neither -- so a selector for
               Arabic matches nothing, and the first version of this rule was
               exactly that and loaded no font. Font fallback is per glyph:
               Latin resolves on Plex Sans, Arabic falls through to Plex Sans
               Arabic, and one line of mixed text gets both. */
            --font-sans: "IBM Plex Sans", "IBM Plex Sans Arabic", system-ui,
                -apple-system, "Segoe UI", sans-serif;
            --font-ar: "IBM Plex Sans Arabic", "Segoe UI", system-ui, sans-serif;
            /* Same reason: an Arabic word inside a monospaced cell would
               otherwise render in whatever the browser had. */
            --font-mono: "IBM Plex Mono", "IBM Plex Sans Arabic", ui-monospace,
                "Cascadia Mono", Consolas, monospace;

            /* surfaces: one ladder, four rungs */
            --bg: #0a0e15;
            --bg-2: #080c12;
            --surface: #0d121b;
            --surface-2: #121826;
            --surface-3: #161d2c;        /* subtle highlight: nav hover */
            --surface-well: #080c12;     /* recessed: inputs, code, wells */
            --sidebar-width: 280px;
            /* Hairlines, not borders. A terminal separates panes with a rule
               a pixel wide at low opacity; a 1px #223049 box around every
               element is what made this read as a page of cards. */
            --border: rgba(255,255,255,.075);
            --border-active: rgba(255,255,255,.15);
            --border-focus: rgba(255,255,255,.26);

            /* text: three tiers, not two */
            --text: #dfe6f0;
            --muted: #8b97aa;
            --text-low: #5a6577;         /* metadata, provenance, timestamps */

            /* semantic state */
            /* At 11px a desaturated state colour disappears. These carry
               more chroma than the page-scale palette they replace. */
            --green: #3ddc97; --amber: #ffb020; --red: #ff5c6c;
            --blue: #6f9eff; --gray: #8b97aa;
            --green-bg: rgba(52,211,153,.08); --red-bg: rgba(248,113,113,.08);
            --amber-bg: rgba(251,191,36,.08); --blue-bg: rgba(96,165,250,.08);
            --gray-bg: rgba(148,163,184,.08);

            /* Unknown is a state of its own, and deliberately not a grey:
               grey reads as "disabled" and this means "never measured". A
               value that was never computed must not be able to pass for a
               computed zero. */
            --unknown: #c084fc;
            --unknown-bg: rgba(192,132,252,.07);
            --unknown-border: rgba(192,132,252,.45);
            --unknown-hatch: repeating-linear-gradient(45deg,
                rgba(192,132,252,.10) 0 3px, transparent 3px 6px);

            /* 4px grid */
            --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s6:24px; --s8:32px;
            /* chips are 2px, panels 4px: no pills */
            --r-chip:0px; --r-card:0px;

            /* The one action colour. Darker than --blue, which is a text and
               state colour and cannot carry white text as a fill. */
            --accent: #2f5fd0; --accent-hover: #24499f;
            --border-hover: rgba(255,255,255,.2); --surface-hover: #161d2c;
            --link: #9dbcff;
        }
        /* Not 16. Sixteen is a reading size for prose on a website; this is an
   instrument read by sweeping, and the size is what separates the two before a
   single colour or border is chosen.

   But 12px was too far. On the 1920x1080 screen this is actually read on, at
   100% zoom, the tables came back as "I need a magnifying glass" -- and a
   number nobody can read is not dense, it is absent. 13.5px keeps the
   instrument register and is legible at arm's length. */
.stApp { background: var(--bg); color: var(--text); font-size:13.5px;
            font-family: var(--font-sans); }
        /* Digits that sit in a column must line up in that column. `zero` as
           well as `tnum`: a slashed zero is what separates 0 from O in a
           ticker column read every day. */
        [data-testid="stMetricValue"], [data-testid="stDataFrame"],
        code, kbd, pre { font-family: var(--font-mono); font-variant-numeric: tabular-nums;
            font-feature-settings: "tnum" 1, "zero" 1; }

        /* Arabic runs on its own face at its own size. Arabic glyphs carry
           taller ascenders and deeper descenders than Latin at the same point
           size, so matching the number matches the wrong thing -- these are
           the optical sizes from the design system's type table, which put the
           two scripts on one baseline instead of one font-size. */
        [dir="rtl"], .ar, .egx-metric .lar, .egx-hero h1:lang(ar) {
            font-family: var(--font-ar); }
        .egx-ar-prose { font-family: var(--font-ar); direction: rtl; text-align: right;
            font-size: .845rem; line-height: 1.95; max-width: 62ch; color: var(--text); }

        /* Keyboard focus is visible on everything, not only buttons. Four of
           the twelve generated screens had no focus state at all, and the app
           it replaces had one on buttons alone -- so tabbing through a table's
           controls left no visible position. */
        :where(a, button, input, select, textarea, summary,
               [role="button"], [role="tab"], [tabindex]):focus-visible {
            outline: 2px solid var(--blue); outline-offset: 2px; border-radius: var(--r-chip);
        }

        /* Nothing here animates -- Streamlit cannot carry motion and the
           design bans it -- but Streamlit's own spinners and progress bars do.
           A reader who asked the OS for less motion gets less of it. */
        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
                animation-duration: .001ms !important; animation-iteration-count: 1 !important;
                transition-duration: .001ms !important; scroll-behavior: auto !important;
            }
        }
        /* clear the fixed Streamlit toolbar so the page title is never clipped */
        .block-container { max-width: 1640px; padding-top: 2.4rem; padding-bottom: 1.2rem;
            padding-left: 1.1rem; padding-right: 1.1rem; }
        header[data-testid="stHeader"] { background: transparent; }
        /* The sidebar is a fixed 280px and never collapses: it carries the
           health panel, and a panel that can be folded away is a panel that
           will be. */
        [data-testid="stSidebar"] { background: var(--bg-2); border-right: 1px solid var(--border);
            width: var(--sidebar-width) !important; min-width: var(--sidebar-width) !important; }
        [data-testid="stSidebar"] * { color: #c7d3e6; }
        /* The health panel goes ABOVE the navigation, which is the whole
           argument for it -- and it was rendering below, at the bottom of the
           sidebar, off the fold. Streamlit builds the sidebar as header, nav,
           then user content, in that order, whatever order the page calls
           them in, so `sidebar_health()` running before `st.navigation()`
           changed nothing. Ordering the three boxes is the only way to put it
           where it belongs, and it is safe because nothing but the brand and
           the health panel is ever written to this sidebar. */
        [data-testid="stSidebarContent"] { display:flex; flex-direction:column; }
        [data-testid="stSidebarHeader"] { order:0; }
        [data-testid="stSidebarUserContent"] { order:1; padding-bottom:.35rem; }
        [data-testid="stSidebarNav"] { order:2; }
        [data-testid="stSidebarNav"] a {
            border-radius: 0; min-height:30px; font-size:.85rem;
            border-left:2px solid transparent; padding-left:.5rem;
        }
        [data-testid="stSidebarNav"] a:hover {
            background: var(--surface-3); color: var(--text);
        }
        /* The current page is marked by a rail on the leading edge, not a
           filled block: the same left-edge language the alerts and the signal
           cards use, so one position always means "state". Flat, 2px, no pill
           -- a rounded highlight block reads as a second navigation competing
           with the one it is inside. */
        [data-testid="stSidebarNav"] a[aria-current="page"] {
            background: var(--surface-3); font-weight: 500;
            border-left-color: var(--blue); color: var(--text);
        }
        [data-testid="stSidebarNav"] span { font-size:.8rem; }
        /* Bilingual group headings: Arabic, a quiet interpunct, then Latin
           uppercase. The separator is deliberately faint -- it joins two
           labels, it is not a third one. */
        [data-testid="stSidebarNav"] > ul > li > div,
        [data-testid="stSidebarNavSeparator"] ~ div,
        .egx-navgroup {
            font-size:.62rem; font-weight:600; letter-spacing:.14em;
            color: var(--text-low) !important; text-transform:uppercase;
            padding:.65rem .1rem .15rem;
        }

        /* --- the health panel above the navigation ------------------------ */
        .egx-health { border:1px solid var(--border); border-radius:0;
            padding:.45rem .55rem; margin:.1rem 0 .6rem; background:var(--surface); }
        .egx-health .t { display:flex; align-items:center; gap:.4rem;
            font-size:.72rem; font-weight:600; letter-spacing:.03em; }
        .egx-health .t i { width:7px; height:7px; border-radius:50%; display:block; flex-shrink:0; }
        .egx-health .d { font-family:var(--font-mono); font-size:.66rem;
            color:var(--text-low) !important; margin-top:.3rem; line-height:1.55; }
        .egx-health.ok { border-color:rgba(52,211,153,.28); background:rgba(52,211,153,.06); }
        .egx-health.ok .t i { background:var(--green); }
        .egx-health.warn { border-color:rgba(251,191,36,.32); background:rgba(251,191,36,.07); }
        .egx-health.warn .t i { background:var(--amber); }
        .egx-health.bad { border-color:rgba(248,113,113,.32); background:rgba(248,113,113,.07); }
        /* Retired: nothing is expected to run, so nothing is late or broken.
           Grey on purpose -- green would claim a run that did not happen, and
           red is the false alarm this state exists to end. */
        .egx-health.retired { border-color:var(--border); background:transparent;
            color:var(--muted); }
        .egx-health.retired .t i { background:var(--gray); }
        .egx-health.bad .t i { background:var(--red); }
        /* The fourth state: the panel could not find out. Not "ran and failed"
           -- which is what an unreadable status file used to be reported as,
           sending the reader to hunt a run that may have been fine. Hollow and
           hatched, in the same violet every unmeasured value on every screen
           wears. */
        .egx-health.unknown { border-color:var(--unknown-border);
            background:var(--unknown-hatch); }
        .egx-health.unknown .t { color:var(--unknown); }
        .egx-health.unknown .t i { background:transparent; border:1px solid var(--unknown); }
        h1,h2,h3,h4 { color: var(--text); letter-spacing: -.015em; }
        h1 { font-size: 1.3rem !important; }
        p, label, .stMarkdown { color: var(--text); font-size:.88rem; line-height:1.6; }
        .stCaption, [data-testid="stCaptionContainer"] { color: var(--muted) !important; }

        /* Metrics. The label is set small and quiet and the value large and
           monospaced, because a row of these is read by scanning the values --
           and values that do not share a digit width cannot be scanned. */
        [data-testid="stMetric"] {
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 0; padding: .45rem .6rem; min-height: 0;
        }
        [data-testid="stMetricLabel"] { color: var(--text-low); font-weight: 600;
            font-size: .68rem; letter-spacing:.1em; text-transform:uppercase; }
        [data-testid="stMetricValue"] { color: var(--text); font-weight: 600;
            font-size: 1.2rem; letter-spacing:-.015em; }
        [data-testid="stMetricDelta"] { font-size: .74rem; }

        /* One accent, flat. The blue-to-cyan gradient this replaces competed
           with the semantic greens and reds it sat beside, which are the only
           colours on these pages that carry a fact. */
        .stButton > button, .stDownloadButton > button {
            border-radius: 0; min-height: 28px; font-weight: 500;
            font-size:.82rem; padding:.25rem .8rem; letter-spacing:.06em;
            text-transform:uppercase; font-family:var(--font-mono);
            background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
        }
        .stButton > button:hover, .stDownloadButton > button:hover {
            border-color: var(--border-hover); background: var(--surface-hover);
        }
        .stButton > button[kind="primary"] {
            background: var(--accent); border: 1px solid var(--accent); color: #fff;
        }
        .stButton > button[kind="primary"]:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
        :is(.stButton, .stDownloadButton) > button:focus-visible {
            outline: 2px solid var(--blue); outline-offset: 2px;
        }

        /* 15px cells and 46px controls are a readability floor set deliberately
           in cf3898f, not a default. The header is the one thing set smaller:
           it is a label you learn once, while the cells are read every day. */
        [data-testid="stDataFrame"] {
            border: 1px solid var(--border); border-radius: 0; overflow: hidden;
            font-size:13px;
        }
        [data-testid="stDataFrame"] [role="columnheader"] {
            font-size:11px; font-weight:600; letter-spacing:.08em;
            text-transform:uppercase; color:var(--text-low);
        }
        /* 30px rows. Forty was a floor set for a 16px page -- two lines of air
   around one line of type, and why a scan of 209 names needed eight screens.
   Thirty is one line of 13px type with room to breathe, which is the point at
   which a column of prices can actually be read down. */
[data-testid="stDataFrame"] [role="gridcell"] { font-size:13px; min-height:30px; }

        /* Tabs as an underlined rail. The pills-in-a-box they replace read as
           a second, competing navigation next to the sidebar's. */
        [data-baseweb="tab-list"] {
            gap: .1rem; background: transparent; border: 0;
            border-bottom: 1px solid var(--border); border-radius: 0; padding: 0;
        }
        [data-baseweb="tab"] {
            border-radius: 0; padding: .35rem .75rem; color: var(--muted);
            font-size:.78rem; font-weight:600; border-bottom:2px solid transparent;
            margin-bottom:-1px; letter-spacing:.05em; text-transform:uppercase;
        }
        [data-baseweb="tab"]:hover { color: var(--text); }
        [data-baseweb="tab"][aria-selected="true"] {
            background: transparent; color: var(--text); border-bottom-color: var(--blue);
        }
        [data-baseweb="tab-highlight"] { background: transparent; }

        [data-testid="stExpander"] { background: var(--surface); border: 1px solid var(--border);
            border-radius: 0; }
        [data-testid="stExpander"] summary { font-size:.82rem; font-weight:600;
            letter-spacing:.04em; }

        /* Alerts carry their state on the left edge, the same place the signal
           cards carry theirs, so severity reads in one consistent position. */
        div[data-testid="stAlert"] { border-radius: 0; border-left-width: 2px;
            border-left-style: solid; font-size:.84rem; padding:.45rem .7rem; }
        div[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) { border-left-color: var(--green); }
        div[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) { border-left-color: var(--amber); }
        div[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) { border-left-color: var(--red); }
        div[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) { border-left-color: var(--blue); }
        [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
        [data-baseweb="select"] > div {
            background: var(--surface-2); color: var(--text); border-color: var(--border);
        }

        /* --- custom components --- */
        /* A masthead, not a box. The gradient panel it replaces spent the
           page's strongest visual weight on a title the reader already knows,
           and left nothing for the numbers underneath. */
        .egx-hero { display:flex; align-items:flex-end; justify-content:space-between;
            gap:1rem; border-bottom:1px solid var(--border);
            padding: 0 .1rem .45rem; margin-bottom: .6rem; }
        .egx-hero h1 { margin:0 !important; font-size:1.15rem !important; font-weight:600;
            letter-spacing:-.02em; }
        .egx-hero p { margin:.2rem 0 0; color: var(--muted); font-size:.78rem;
            max-width:92ch; line-height:1.5; }
        .egx-hero .egx-badge { background:transparent; border:1px solid var(--border);
            color:var(--muted); padding:.2rem .55rem; border-radius:5px;
            font-family:var(--font-mono); font-size:.66rem; font-weight:500;
            letter-spacing:.04em; white-space:nowrap; flex-shrink:0; }

        .egx-statusbar { display:flex; flex-wrap:wrap; gap:.4rem; align-items:center;
            background: var(--surface); border:1px solid var(--border); border-radius:11px;
            padding:.5rem .7rem; margin-bottom:.7rem; }
        .egx-chip { display:inline-flex; align-items:center; gap:.35rem; font-size:.76rem;
            font-weight:650; padding:.22rem .55rem; border-radius:999px; white-space:nowrap; }
        .egx-chip .k { color: var(--muted); font-weight:600; }
        .egx-sep { width:1px; height:16px; background: var(--border); margin:0 .1rem; }

        .egx-badge-pill { display:inline-block; padding:.08rem .35rem; border-radius:0;
            font-size:.65rem; font-weight:600; line-height:1.4;
            font-family:var(--font-mono); letter-spacing:.04em; }

        /* --- the three-state gate ----------------------------------------
           PASS, FAIL, and UNAVAILABLE. Never two. A gate that could not run
           reports UNAVAILABLE in its own colour and its own hatch, because
           this program has already shipped a gate that could not fire while
           reporting PASS -- `require_market_analyzer` read true for years over
           an index no provider served (docs/audits/strategies/
           DAILY_STRATEGY_DIAGNOSIS.md section 7). Two of the twelve generated
           screens honoured this; the rest could not tell the two apart. */
        .egx-gate { display:inline-flex; align-items:center; gap:.3rem;
            font-family:var(--font-mono); font-size:.68rem; font-weight:600;
            letter-spacing:.05em; text-transform:uppercase; padding:.15rem .4rem;
            border-radius:var(--r-chip); border:1px solid transparent; white-space:nowrap; }
        .egx-gate::before { content:""; width:5px; height:5px; flex-shrink:0; }
        .egx-gate.pass { color:var(--green); border-color:var(--green); background:transparent; }
        .egx-gate.pass::before { background:var(--green); }
        .egx-gate.fail { color:var(--red); border-color:rgba(248,113,113,.5); background:var(--red-bg); }
        .egx-gate.fail::before { background:var(--red); }
        /* Hatched, not filled: it is an absence of a result, not a result. */
        .egx-gate.na { color:var(--amber); border-color:rgba(251,191,36,.5);
            background:repeating-linear-gradient(45deg, rgba(251,191,36,.13) 0 3px,
                transparent 3px 6px); }
        .egx-gate.na::before { background:transparent; border:1px solid var(--amber); }

        /* --- unknown, which is not zero and not blank --------------------- */
        .egx-unknown { display:inline-flex; align-items:center; gap:.25rem;
            font-family:var(--font-mono); font-size:.78rem; color:var(--unknown);
            border:1px dashed var(--unknown-border); background:var(--unknown-hatch);
            padding:.05rem .35rem; border-radius:var(--r-chip); white-space:nowrap; }

        /* --- where a number came from ------------------------------------- */
        .egx-prov { font-family:var(--font-mono); font-size:.62rem; color:var(--text-low);
            letter-spacing:.02em; white-space:nowrap; }
        .egx-prov b { font-weight:500; color:var(--muted); }

        /* --- the action card, in four kinds -------------------------------
           The portfolio page has four things to say about a holding and they
           were three generic banners and a card. They are different facts and
           they send the reader to different places:

             act          something to do now, priced after costs
             unvalidated  a recommendation whose rule has never been measured
             withheld     no trustworthy price, so no advice is issued at all
             noplan       nothing was ever measured for this holding

           `withheld` is amber because it is a freshness problem and running
           the import fixes it. `noplan` is the unknown violet because nothing
           was ever computed -- the same distinction as everywhere else. */
        .egx-act { background:var(--surface); border:1px solid var(--border);
            border-left:3px solid var(--gray); border-radius:var(--r-card);
            padding:.5rem .7rem; margin-bottom:.3rem; }
        .egx-act.k-act.t-red { border-left-color:var(--red); }
        .egx-act.k-act.t-amber { border-left-color:var(--amber); }
        .egx-act.k-act.t-green { border-left-color:var(--green); }
        .egx-act.k-act.t-blue { border-left-color:var(--blue); }
        .egx-act.k-unvalidated { border-left-color:var(--blue);
            background:linear-gradient(var(--blue-bg), var(--blue-bg)), var(--surface); }
        .egx-act.k-withheld { border-left-color:var(--amber);
            background:linear-gradient(var(--amber-bg), var(--amber-bg)), var(--surface); }
        .egx-act.k-noplan { border-left-color:var(--unknown);
            background:var(--unknown-hatch), var(--surface); }
        .egx-act .top { display:flex; justify-content:space-between; align-items:center;
            gap:.6rem; flex-wrap:wrap; }
        .egx-act .tick { font-size:.92rem; font-weight:600; letter-spacing:-.01em; }
        .egx-act .name { font-size:.7rem; color:var(--muted); font-weight:400;
            margin-right:.4rem; }
        .egx-act .badges { display:flex; gap:.3rem; flex-wrap:wrap; }
        .egx-act .why { margin-top:.3rem; line-height:1.6; color:var(--text);
            font-size:.82rem; }
        /* The figures a decision is actually made on, in one row, monospaced
           so two cards can be compared down the column rather than read. */
        .egx-act .nums { display:flex; flex-wrap:wrap; gap:.1rem 1.2rem;
            margin-top:.35rem; padding-top:.3rem; border-top:1px solid var(--border);
            font-family:var(--font-mono); font-variant-numeric:tabular-nums;
            font-size:.72rem; }
        .egx-act .nums div { display:flex; gap:.4rem; }
        .egx-act .nums .k { color:var(--text-low); font-family:var(--font-sans);
            font-size:.66rem; }
        .egx-act .nums .v { color:var(--text); }
        .egx-act .nums .v.pos { color:var(--green); }
        .egx-act .nums .v.neg { color:var(--red); }

        /* --- the context strip --------------------------------------------
           Seven readings on one line, each `label ......... value`, bordered
           in its own tone. Replaces seven metric tiles: a tile is for a figure
           you stop and read, and these are read by sweeping across them. */
        .egx-strip { display:grid; grid-template-columns:repeat(7, minmax(0,1fr));
            gap:.45rem; margin:.2rem 0 .55rem; }
        .egx-strip .c { display:flex; align-items:center; justify-content:space-between;
            gap:.4rem; background:var(--surface); border:1px solid var(--border);
            border-radius:var(--r-chip); padding:.3rem .5rem; min-width:0; }
        .egx-strip .c .k { display:flex; align-items:center; gap:.35rem;
            font-size:.7rem; color:var(--muted); min-width:0;
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .egx-strip .c .k i { width:6px; height:6px; border-radius:50%;
            flex-shrink:0; display:block; }
        .egx-strip .c .v { font-family:var(--font-mono); font-size:.86rem;
            font-weight:600; color:var(--text); font-variant-numeric:tabular-nums;
            white-space:nowrap; }
        .egx-strip .c .v small { font-size:.64rem; color:var(--text-low);
            font-weight:400; margin-inline-start:.32rem; }
        @media (max-width:1400px) {
            .egx-strip { grid-template-columns:repeat(4, minmax(0,1fr)); }
        }

        /* --- the exclusion notice ------------------------------------------
           One line, a mono tag, the sentence, and where to go. It was a
           Streamlit warning box: three lines of chrome for one fact. */
        .egx-notice { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap;
            background:var(--amber-bg); border:1px solid rgba(251,191,36,.25);
            border-right:3px solid var(--amber); border-radius:var(--r-chip);
            padding:.4rem .6rem; margin:.1rem 0 .5rem; font-size:.78rem; }
        .egx-notice b { font-family:var(--font-mono); font-size:.62rem;
            letter-spacing:.05em; text-transform:uppercase; color:var(--amber);
            background:rgba(251,191,36,.14); border-radius:var(--r-chip);
            padding:.1rem .35rem; white-space:nowrap; }
        .egx-notice span { color:var(--text); line-height:1.6; }

        /* --- the opportunity card ------------------------------------------
           Three to a row. Ticker and name, the sector and regime it sits in,
           its decision, the four figures a trade is judged on, and the trade
           drawn to scale underneath. */
        .egx-opps { display:grid; grid-template-columns:repeat(3, minmax(0,1fr));
            gap:.6rem; margin:.2rem 0 .3rem; }
        @media (max-width:1250px) {
            .egx-opps { grid-template-columns:repeat(2, minmax(0,1fr)); }
        }
        .egx-opp { background:var(--surface); border:1px solid var(--border);
            border-right:4px solid var(--gray); border-radius:var(--r-card);
            padding:.65rem .75rem; display:flex; flex-direction:column; gap:.5rem; }
        .egx-opp.buy { border-right-color:var(--green); }
        .egx-opp.watch { border-right-color:var(--blue); }
        .egx-opp.avoid { border-right-color:var(--red); }
        .egx-opp .head { display:flex; align-items:flex-start;
            justify-content:space-between; gap:.5rem; }
        .egx-opp .tick { font-family:var(--font-mono); font-size:.98rem;
            font-weight:600; color:var(--text); }
        .egx-opp .name { font-size:.74rem; color:var(--muted);
            margin-inline-start:.4rem; }
        .egx-opp .where { font-family:var(--font-mono); font-size:.62rem;
            color:var(--text-low); margin-top:.15rem; }
        .egx-opp .sig { font-family:var(--font-mono); font-size:.68rem;
            font-weight:600; border-radius:var(--r-chip); padding:.1rem .35rem;
            white-space:nowrap; }
        /* The four figures a trade is judged on, in one recessed row. */
        .egx-opp .figs { display:grid; grid-template-columns:repeat(4, minmax(0,1fr));
            gap:.3rem; background:var(--surface-well); border:1px solid var(--border);
            border-radius:var(--r-chip); padding:.35rem .3rem; text-align:center; }
        .egx-opp .figs .k { font-size:.58rem; color:var(--text-low);
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .egx-opp .figs .v { font-family:var(--font-mono); font-size:.82rem;
            font-weight:600; margin-top:.1rem; font-variant-numeric:tabular-nums; }
        .egx-opp .figs .v.stop { color:var(--red); }
        .egx-opp .figs .v.rr { color:var(--green); }
        /* The trade to scale: stop at one end, second target at the other, the
           entry band and today's close where they really fall between them. A
           card with a level missing gets no rail rather than a drawn guess. */
        .egx-opp .legend { display:flex; justify-content:space-between;
            font-family:var(--font-mono); font-size:.62rem; color:var(--text-low); }
        .egx-opp .legend .lo { color:var(--red); }
        .egx-opp .legend .hi { color:var(--green); }
        .egx-opp .legend .mid { color:var(--text); }
        .egx-opp .scale { position:relative; height:9px; border-radius:var(--r-chip);
            background:var(--surface-well); border:1px solid var(--border);
            overflow:hidden; margin-top:.2rem; }
        .egx-opp .scale .risk { position:absolute; top:0; bottom:0; left:0;
            background:rgba(248,113,113,.22); }
        .egx-opp .scale .reward { position:absolute; top:0; bottom:0; right:0;
            background:rgba(52,211,153,.20); }
        .egx-opp .scale .band { position:absolute; top:0; bottom:0;
            background:rgba(96,165,250,.45);
            border-left:1px solid var(--blue); border-right:1px solid var(--blue); }
        .egx-opp .scale .now { position:absolute; top:-1px; bottom:-1px; width:2px;
            background:var(--text); }

        /* --- the attrition bar --------------------------------------------
           How a universe narrows to a handful, drawn to scale. One segment per
           refusing gate plus the survivors, each sized by its real share.

           Deliberately NOT a cascade of shrinking stages: the counts behind it
           record which gate refused each symbol *first*, so every symbol
           appears exactly once and the stages are a partition rather than a
           sequence. Drawing 230 -> 209 -> 74 -> 28 would imply an ordering the
           numbers do not carry. */
        .egx-funnel { display:flex; width:100%; height:22px; border-radius:var(--r-chip);
            overflow:hidden; border:1px solid var(--border); background:var(--surface-well); }
        .egx-funnel span { display:block; height:100%; }
        .egx-funnel span.survived { background:var(--green); }
        .egx-funnel span.refused { background:var(--border-active); }
        .egx-funnel span.refused.alt { background:var(--surface-2); }
        .egx-funnel-legend { display:grid;
            grid-template-columns:1fr auto auto; gap:.1rem .8rem; margin-top:.5rem;
            font-size:.8rem; }
        .egx-funnel-legend .g { display:flex; align-items:center; gap:.45rem;
            color:var(--muted); }
        .egx-funnel-legend .g i { width:8px; height:8px; border-radius:1px;
            flex-shrink:0; display:block; }
        .egx-funnel-legend .n, .egx-funnel-legend .p {
            font-family:var(--font-mono); font-variant-numeric:tabular-nums;
            text-align:right; color:var(--text); }
        .egx-funnel-legend .p { color:var(--text-low); }
        .egx-funnel-legend .row-survived .g { color:var(--green); font-weight:600; }
        .egx-funnel-legend .row-survived .n { color:var(--green); }

        /* --- evidence, not an instruction ---------------------------------
           Worn by any panel whose output is research rather than a trade to
           place. Blue, never green: green on this page means a position made
           money, and an advisory card must not borrow that. */
        .egx-advisory { display:inline-flex; align-items:center; gap:.35rem;
            font-family:var(--font-mono); font-size:.63rem; font-weight:600;
            letter-spacing:.06em; text-transform:uppercase; color:var(--blue);
            border:1px solid rgba(96,165,250,.45); background:var(--blue-bg);
            padding:.12rem .4rem; border-radius:var(--r-chip); white-space:nowrap; }

        .egx-metric { background: var(--surface); border:1px solid var(--border);
            border-radius:0; padding:.4rem .6rem; }
        .egx-metric .v { font-size:1.15rem; font-weight:600; line-height:1.05;
            font-family:var(--font-mono); letter-spacing:-.015em; }
        .egx-metric .lar { color: var(--muted); font-size:.74rem; font-weight:500;
            margin-top:.15rem; direction:rtl; }
        .egx-metric .len { color: var(--text-low); font-size:.6rem; font-weight:600;
            text-transform:uppercase; letter-spacing:.1em; margin-top:.02rem; }
        .egx-metric .s { color: var(--text-low); font-size:.63rem; margin-top:.15rem; }
        .egx-metric .tag { display:inline-block; font-size:.57rem; font-weight:700; padding:.03rem .3rem;
            border-radius:5px; background:rgba(148,163,184,.14); color:var(--muted); margin-top:.2rem;
            text-transform:uppercase; letter-spacing:.03em; }

        .egx-section { display:flex; align-items:baseline; justify-content:space-between;
            margin:.7rem 0 .3rem; border-bottom:1px solid var(--border);
            padding-bottom:.25rem; }
        .egx-section h3 { margin:0; font-size:.78rem; font-weight:600;
            letter-spacing:.13em; text-transform:uppercase; color:var(--muted); }
        .egx-section span { color: var(--text-low); font-size:.72rem; }
        .egx-empty { text-align:center; padding:1rem .8rem; border:1px dashed var(--border);
            border-radius:0; background: var(--surface); color: var(--muted);
            font-size:.8rem; }
        .egx-empty strong { display:block; color: var(--text); margin-bottom:.25rem; }

        /* --- a rule that ran and correctly produced nothing ----------------
           Not `.egx-empty`. That one is dashed and grey because it means
           absent: no search results, no saved list, nothing loaded yet. A
           strategy that fires ninety times a year is silent on most sessions,
           and its silence is the rule working. Dressing the two the same makes
           the commonest state on the page look like a fault every day until
           the reader stops believing either. Solid, bordered, and carrying its
           own count. */
        .egx-quiet { border:1px solid var(--border); border-left:2px solid var(--green);
            border-radius:0; background:var(--surface); padding:.6rem .75rem; }
        .egx-quiet .h { display:flex; align-items:baseline; gap:.6rem;
            flex-wrap:wrap; }
        .egx-quiet .h strong { font-size:1.02rem; color:var(--text); font-weight:600; }
        .egx-quiet .count { font-family:var(--font-mono);
            font-variant-numeric:tabular-nums; font-size:.78rem; color:var(--green);
            border:1px solid rgba(52,211,153,.35); border-radius:var(--r-chip);
            padding:.1rem .4rem; }
        .egx-quiet p { margin:.5rem 0 0; color:var(--muted); font-size:.88rem;
            line-height:1.7; max-width:78ch; }

        /* --- a panel that could not be computed ----------------------------
           The third member of the set, and the one the app was missing. There
           are three different nothings and they were all `.egx-empty`:

             empty        nothing is here -- no search results, nothing loaded
             quiet        it was measured and the answer is none
             unavailable  it could not be computed, so there is no answer

           The Sector Liquidity page alone had five of the third kind wearing
           the first's clothes: no complete session, no strength measurement,
           no rotation history, no forecast, no live forecast. Each is "not
           enough data yet", which is the unknown state of a whole panel --
           so it wears the same violet hatch a single unmeasured value does. */
        .egx-unavailable { border:1px solid var(--unknown-border);
            border-radius:0; background:var(--unknown-hatch); padding:.6rem .75rem; }
        .egx-unavailable strong { display:block; color:var(--unknown);
            font-size:.98rem; font-weight:600; }
        .egx-unavailable p { margin:.45rem 0 0; color:var(--muted);
            font-size:.88rem; line-height:1.7; max-width:78ch; }
        .egx-unavailable .need { display:inline-block; margin-top:.5rem;
            font-family:var(--font-mono); font-size:.72rem; color:var(--text-low);
            border:1px dashed var(--unknown-border); border-radius:var(--r-chip);
            padding:.1rem .4rem; }

        /* --- a projection, which is not a measurement ----------------------
           Sector share and a forecast of sector share were four tables of
           percentages in identical dress. Dashed, because every other border
           on these pages is solid and solid is what measured looks like. */
        .egx-projection { border:1px dashed rgba(96,165,250,.5);
            border-left:3px solid var(--blue); border-radius:var(--r-card);
            background:var(--blue-bg); padding:.6rem .85rem; margin:.1rem 0 .5rem;
            display:flex; gap:.6rem; align-items:baseline; flex-wrap:wrap; }
        .egx-projection b { font-family:var(--font-mono); font-size:.66rem;
            letter-spacing:.06em; text-transform:uppercase; color:var(--blue);
            white-space:nowrap; }
        .egx-projection span { color:var(--muted); font-size:.83rem;
            line-height:1.65; }

        .egx-rangebar { position:relative; height:8px; border-radius:999px;
            background: linear-gradient(90deg,#10233f,#12325a); border:1px solid var(--border); }
        .egx-rangebar .dot { position:absolute; top:-3px; width:12px; height:12px; border-radius:50%;
            background:#e6edf7; border:2px solid #0b1220; transform:translateX(-50%); }
        .egx-oppcard { background: var(--surface); border:1px solid var(--border);
            border-left:3px solid var(--green); border-radius:11px; padding:.7rem .85rem; height:100%; }
        .egx-system-link { display:inline-block; padding:.55rem .85rem; border-radius:9px;
            background:var(--surface-2); border:1px solid var(--border); color:var(--link) !important;
            font-weight:750; text-decoration:none; margin:.35rem 0 .65rem; }
        /* --- signal card: the trade drawn to scale ------------------------
           Stop, entry and target sit at their true relative distances, and the
           cost band is drawn from the entry outward at its real width. A
           target that falls inside that band is a trade that loses money at
           its own target -- the one fact this layout exists to make visible
           before any number is read. */
        .egx-sig { display:grid; grid-template-columns: 148px 1fr 232px; gap:1.3rem;
            align-items:center; background: var(--surface); border:1px solid var(--border);
            border-left:3px solid var(--gray); border-radius:12px;
            padding:.85rem 1.05rem; margin-bottom:.5rem; }
        .egx-sig.pos { border-left-color: var(--green); }
        .egx-sig.neg { border-left-color: var(--red); }
        .egx-sig.unk { border-left-color: var(--gray); }
        .egx-sig .tick { font-size:1.22rem; font-weight:650; letter-spacing:-.01em; line-height:1.15; }
        .egx-sig .sub { font-size:.78rem; color:var(--muted); margin-top:.12rem; line-height:1.35; }
        .egx-sig .when { font-family:var(--font-mono); font-size:.78rem;
            color:var(--muted); margin-top:.35rem; }

        .egx-rail { position:relative; height:30px; }
        .egx-rail .track { position:absolute; top:13px; left:0; right:0; height:3px;
            border-radius:2px; background:linear-gradient(90deg,
                var(--red) 0%, var(--border) 30%, var(--border) 64%, var(--green) 100%); }
        /* The slice of the move that is only cost. Hatched, not solid: it is a
           charge against the move, not a level the price visits. */
        .egx-rail .cost { position:absolute; top:9px; height:11px; border-radius:2px;
            background:repeating-linear-gradient(135deg, rgba(248,113,113,.30) 0 4px,
                rgba(248,113,113,.09) 4px 8px);
            border-left:1px solid rgba(248,113,113,.5); border-right:1px solid rgba(248,113,113,.5); }
        .egx-rail .mark { position:absolute; top:7px; width:2px; height:15px; transform:translateX(-50%); }
        .egx-rail .dot { position:absolute; top:4px; width:11px; height:11px; border-radius:50%;
            background:var(--text); border:3px solid var(--surface); transform:translateX(-50%); }
        .egx-legend { display:flex; justify-content:space-between; font-family:var(--font-mono);
            font-size:.8rem; color:var(--muted); margin-top:.1rem; }
        .egx-legend .mid { color:var(--text); }

        .egx-sig .nums { display:grid; grid-template-columns:repeat(3,minmax(0,1fr));
            gap:.6rem; text-align:right; font-family:var(--font-mono);
            font-variant-numeric:tabular-nums; }
        /* Same size as a metric's label: these are the same kind of thing, and
           the smallest text on a page should not be a one-off. */
        .egx-sig .nums .k { font-size:.74rem; letter-spacing:.05em; text-transform:uppercase;
            color:var(--muted); font-weight:600; font-family:var(--font-sans); }
        .egx-sig .nums .v { font-size:1.02rem; margin-top:.14rem; }
        .egx-sig .nums .v.big { font-weight:650; }
        .egx-note { font-size:.82rem; color:var(--muted); grid-column:1 / -1;
            border-top:1px solid var(--border); padding-top:.5rem; margin-top:.1rem; }

        @media (max-width: 1100px) {
            .egx-sig { grid-template-columns:1fr; gap:.7rem; }
            .egx-sig .nums { text-align:left; }
        }
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


def page_header(title, subtitle, icon="", badge=None):
    """The page's name and what it is for.

    ``icon`` is accepted and ignored. Every page passed one -- 📊 📈 🔭 🚀 🌊 ⭐
    🔎 🤖 🩺 ⚙️ -- and a row of emoji down a navigation is the single clearest
    tell that a screen is a consumer app rather than an instrument. The
    parameter stays so twenty call sites do not have to change in a pass about
    how the application looks; nothing renders it.
    """
    badge_markup = (f'<span class="egx-badge">{html.escape(str(badge))}</span>'
                    if badge else "")
    st.markdown(
        f"""<div class="egx-hero"><div>
            <h1>{html.escape(title)}</h1>
            <p>{html.escape(subtitle)}</p></div>{badge_markup}</div>""",
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


def quiet_state(title, message, count=""):
    """A rule that ran, refused everything, and was right to.

    Distinct from ``empty_state`` on purpose: that one means something is
    absent -- no saved list, no search results, nothing loaded. This one means
    the measurement happened and its answer was none. A strategy that fires
    about ninety times a year says none on most sessions, and rendering that in
    the same dashed grey as a missing file teaches the reader to read the
    commonest state on the page as a fault.
    """
    chip = (f'<span class="count">{html.escape(str(count))}</span>'
            if count else "")
    st.markdown(
        f'<div class="egx-quiet"><div class="h">'
        f'<strong>{html.escape(str(title))}</strong>{chip}</div>'
        f'<p>{html.escape(str(message))}</p></div>',
        unsafe_allow_html=True)


def unavailable_state(title, message, needs=""):
    """A panel that could not be computed -- not one that came back empty.

    "No complete session yet" and "no rows matched your filter" were the same
    grey dashed box. They are not the same fact: one is a gap in the record
    that will close, the other is an answer. ``needs`` states what would make
    it computable, because a reader who cannot see a panel should be able to
    tell whether to wait, to run something, or to stop expecting it.
    """
    need = (f'<span class="need">{html.escape(str(needs))}</span>'
            if needs else "")
    st.markdown(
        f'<div class="egx-unavailable">'
        f'<strong>{html.escape(str(title))}</strong>'
        f'<p>{html.escape(str(message))}</p>{need}</div>',
        unsafe_allow_html=True)


def projection_note(message, label="PROJECTION · تقدير"):
    """Marks what follows as a projection rather than a measurement.

    Sector share and a forecast of sector share are both tables of percentages
    and were rendered identically. This goes immediately above the projected
    one, so the distinction cannot be lost by scrolling past a caption.
    """
    st.markdown(
        f'<div class="egx-projection"><b>{html.escape(str(label))}</b>'
        f'<span>{html.escape(str(message))}</span></div>',
        unsafe_allow_html=True)


def sidebar_health(session_date=None) -> None:
    """One line at the top of every page: did this morning's run happen?

    It sits above the navigation because the failures this project has actually
    suffered were silences — a collector that died at 08:00 and stayed dead for
    sixteen hours, a finalizer that ran before the session was authoritative
    and wrote nothing, a clock that drifted past the point where "live" meant
    anything. None announced itself. All of them were three clicks inside
    System Health, on a page nobody opens on a good day.

    Every failure here is caught: a panel that raises would take down whichever
    page it is decorating, and a monitor that can break the thing it monitors
    is worse than no monitor. But it no longer catches by *disappearing*. A
    health panel that vanishes when it breaks is the failure mode it exists to
    prevent, wearing the panel's own clothes: the sidebar looks ordinary and
    nothing is being watched. It now renders its fourth state instead.

    Four states, because "the run failed" and "I could not find out whether the
    run happened" are different facts and send the reader to different places.
    An unreadable status file was reported as a failed run until now.
    """
    tone, headline, detail = "unknown", "Run state unknown", "—·—"
    try:
        from services.automation_status import (NEVER_RAN, RETIRED, RETIRED_REASON,
                                                UNREADABLE, AutomationStatus,
                                                is_retired, read_status)

        day = session_date or date.today().isoformat()
        # A retired run is neither a failure nor a quiet day. Checked before
        # the status file, because a missing file on a retired date would
        # otherwise read as "No run recorded today" in red every morning.
        status = (AutomationStatus(session_date=str(day), outcome=RETIRED,
                                   reason=RETIRED_REASON)
                  if is_retired(day) else read_status(day))

        if status.outcome == RETIRED:
            tone, headline = "retired", "Morning run retired"
        elif status.healthy:
            tone, headline = "ok", "This morning's run completed"
        elif status.outcome == UNREADABLE:
            tone, headline = "unknown", "Run state unreadable"
        elif status.outcome == NEVER_RAN:
            tone, headline = "bad", "No run recorded today"
        elif status.outcome == "RUNNING":
            tone, headline = "warn", "Run still in flight"
        else:
            tone, headline = "bad", f"Run {status.outcome.lower()}"

        detail = html.escape((status.reason or "")[:120]) or html.escape(str(day))
    except Exception:  # noqa: BLE001 - a broken monitor must not break the page
        logger.exception("sidebar health panel could not read the run status")
        detail = "—·— the status could not be read; this is not a healthy run"
    try:
        st.sidebar.markdown(
            f'<div class="egx-health {tone}"><div class="t"><i></i>'
            f'<span>{html.escape(headline)}</span></div>'
            f'<div class="d">{detail}</div></div>',
            unsafe_allow_html=True,
        )
    except Exception:  # noqa: BLE001 - nothing here may take down the page
        logger.exception("sidebar health panel could not be rendered")


#: The rail is inset so a marker at either extreme is not clipped by the card.
_RAIL_LOW, _RAIL_HIGH = 4.0, 96.0


def _rail_position(price, low, high):
    """Where ``price`` sits on the rail, as a percentage.

    Linear in price, so the drawing is to scale: a stop twice as far from the
    entry as the target draws twice as far away. Nothing here is normalised,
    ranked or eased -- the moment the geometry stops being proportional, the
    picture stops being evidence.
    """
    span = high - low
    if span <= 0:
        return (_RAIL_LOW + _RAIL_HIGH) / 2
    fraction = (price - low) / span
    return _RAIL_LOW + max(0.0, min(1.0, fraction)) * (_RAIL_HIGH - _RAIL_LOW)


def signal_card(*, ticker, entry, stop, target, subtitle="", when="",
                move_percent=None, cost_percent=None, net_percent=None,
                note=""):
    """One signal drawn as stop / entry / target on a single rail.

    ``cost_percent`` of ``None`` means the spread was never observed, so the
    cost is *unknown*. It is drawn as no band and labelled as unknown rather
    than as zero: an unmeasured cost is not a smaller one, and a card that
    silently treats it as zero is the same failure that once made thirteen
    signals look profitable.

    Presentation only. It proposes nothing, places nothing, and reports no
    fill -- these are the levels the engine recorded, drawn at their real
    distances.
    """
    values = [v for v in (entry, stop, target) if v is not None]
    if entry is None or len(values) < 2:
        # Without at least two real levels there is no scale, and a rail drawn
        # without one would be decoration pretending to be measurement.
        _signal_card_bare(ticker, subtitle, when, move_percent,
                          cost_percent, net_percent, note)
        return

    low, high = min(values), max(values)
    parts = ['<div class="egx-rail"><span class="track"></span>']

    if cost_percent is not None:
        # The band starts at the entry and runs the width of the round trip.
        cost_edge = entry * (1.0 + abs(cost_percent) / 100.0)
        left = _rail_position(entry, low, high)
        right = _rail_position(cost_edge, low, high)
        if right > left:
            parts.append(
                f'<span class="cost" style="left:{left:.1f}%;'
                f'width:{right - left:.1f}%"></span>'
            )

    if stop is not None:
        parts.append(
            f'<span class="mark" style="left:{_rail_position(stop, low, high):.1f}%;'
            f'background:var(--red)"></span>'
        )
    if target is not None:
        parts.append(
            f'<span class="mark" style="left:{_rail_position(target, low, high):.1f}%;'
            f'background:var(--green)"></span>'
        )
    parts.append(
        f'<span class="dot" style="left:{_rail_position(entry, low, high):.1f}%"></span></div>'
    )

    legend = (
        f'<div class="egx-legend"><span>stop {_price(stop)}</span>'
        f'<span class="mid">entry {_price(entry)}</span>'
        f'<span>target {_price(target)}</span></div>'
    )

    st.markdown(
        f'<div class="egx-sig {_net_tone(net_percent)}">'
        f'{_signal_identity(ticker, subtitle, when)}'
        f'<div>{"".join(parts)}{legend}</div>'
        f'{_signal_numbers(move_percent, cost_percent, net_percent)}'
        f'{_signal_note(note)}</div>',
        unsafe_allow_html=True,
    )


def _signal_card_bare(ticker, subtitle, when, move, cost, net, note):
    """The same card without a rail, for a signal whose levels were not kept."""
    st.markdown(
        f'<div class="egx-sig {_net_tone(net)}">'
        f'{_signal_identity(ticker, subtitle, when)}'
        f'<div class="egx-legend"><span>levels were not recorded for this '
        f'signal</span></div>'
        f'{_signal_numbers(move, cost, net)}{_signal_note(note)}</div>',
        unsafe_allow_html=True,
    )


def _signal_identity(ticker, subtitle, when):
    sub = f'<div class="sub">{html.escape(str(subtitle))}</div>' if subtitle else ""
    at = f'<div class="when">{html.escape(str(when))}</div>' if when else ""
    return f'<div><div class="tick">{html.escape(str(ticker))}</div>{sub}{at}</div>'


def _signal_numbers(move, cost, net):
    return (
        '<div class="nums">'
        f'<div><div class="k">Move</div><div class="v">{_percent(move)}</div></div>'
        f'<div><div class="k">Cost</div>'
        f'<div class="v" style="color:var(--muted)">{_percent(cost)}</div></div>'
        f'<div><div class="k">Net</div>'
        f'<div class="v big" style="color:var(--{_net_colour(net)})">'
        f'{_percent(net, signed=True)}</div></div></div>'
    )


def _signal_note(note):
    return f'<div class="egx-note">{html.escape(str(note))}</div>' if note else ""


def _net_tone(net):
    if net is None:
        return "unk"
    return "pos" if net > 0 else "neg"


def _net_colour(net):
    if net is None:
        return "muted"
    return "green" if net > 0 else "red"


def _price(value):
    return f"{value:,.3f}".rstrip("0").rstrip(".") if value is not None else "—"


def _percent(value, signed=False):
    if value is None:
        return "unknown"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


def badge_html(text, tone="gray", title=""):
    bg, border, fg = _TONE.get(tone, _TONE["gray"])
    t = f' title="{html.escape(str(title))}"' if title else ""
    return (f'<span class="egx-badge-pill"{t} style="background:{bg};'
            f'border:1px solid {border};color:{fg}">{html.escape(str(text))}</span>')


#: The three states of any check, and the class each is drawn with. A caller
#: with a boolean has two states and must say which of the three the third is;
#: that is the point of the constant rather than a bare string.
GATE_PASS, GATE_FAIL, GATE_UNAVAILABLE = "PASS", "FAIL", "UNAVAILABLE"
#: A gate the engine never got to, because an earlier one stopped the chain.
#: Not the same as UNAVAILABLE -- that one was reached and could not be
#: computed -- but the same thing to look at: no result. It shares the hatch
#: and says which it is in words.
GATE_NOT_REACHED = "NOT REACHED"
_GATE_CLASS = {GATE_PASS: "pass", GATE_FAIL: "fail",
               GATE_UNAVAILABLE: "na", GATE_NOT_REACHED: "na"}


def gate_html(state, label="", title=""):
    """One check's outcome as PASS / FAIL / UNAVAILABLE -- never as two states.

    ``state`` is one of the three constants. Anything else is drawn as
    UNAVAILABLE rather than guessed at, because the failure this component
    exists to prevent is a check whose result was unknown being shown as one
    that passed.
    """
    key = str(state).strip().upper()
    css = _GATE_CLASS.get(key)
    if css is None:
        css, key = "na", GATE_UNAVAILABLE
    text = f"{label} {key}".strip() if label else key
    tip = f' title="{html.escape(str(title))}"' if title else ""
    return f'<span class="egx-gate {css}"{tip}>{html.escape(text)}</span>'


def unknown_html(note="not measured"):
    """The value that was never measured. Never a zero, never an empty cell."""
    return (f'<span class="egx-unknown" title="{html.escape(str(note))}">'
            f'—·— <span style="font-size:.85em;opacity:.8">?</span></span>')


def provenance_html(source, label="src"):
    """Where a number came from, small enough to sit under it."""
    return (f'<span class="egx-prov"><b>{html.escape(str(label))}:</b> '
            f'{html.escape(str(source))}</span>')


def advisory_html(text="ADVISORY · بحث استرشادي"):
    """Marks a panel as evidence to read, not an instruction to act on."""
    return f'<span class="egx-advisory">{html.escape(str(text))}</span>'


def context_strip(readings):
    """Several readings on one line: ``(label, value, tone, sub)`` each.

    A metric tile is for a figure you stop and read. These are read by
    sweeping, so they are one line each with the value right-aligned and the
    tone on the dot and the border rather than on the number.
    """
    cells = []
    for label, value, tone, sub in readings:
        colour = _TONE.get(tone, _TONE["gray"])[2] if tone else "var(--muted)"
        border = (f"border-color:{_TONE[tone][1]}"
                  if tone in _TONE else "")
        # The separator is a character in the markup, not a margin in the
        # stylesheet. A count and its share are both numerals in the same
        # tabular face, and .2rem of margin between them was not a gap a reader
        # could see: 109 watched and 52% of the scan rendered as "10952%", and
        # 209/225 with 16 excluded as "209/225-16".
        extra = (f"<small> · {html.escape(str(sub))}</small>"
                 if sub else "")
        cells.append(
            f'<div class="c" style="{border}">'
            f'<span class="k"><i style="background:{colour}"></i>'
            f'{html.escape(str(label))}</span>'
            f'<span class="v" style="color:{colour}">{html.escape(str(value))}'
            f'{extra}</span></div>')
    return f'<div class="egx-strip">{"".join(cells)}</div>'


def notice(tag, message):
    """One line: a mono tag, the fact, and nothing else."""
    return (f'<div class="egx-notice"><b>{html.escape(str(tag))}</b>'
            f'<span>{html.escape(str(message))}</span></div>')


def _scale_positions(stop, low, high, price, target):
    """Where the entry band and today's close fall between stop and target.

    Returns ``None`` unless every level needed to place them exists and the
    span is real. A rail drawn from a missing level is a picture of a trade
    that was never computed.
    """
    try:
        values = [float(v) for v in (stop, low, high, price, target)]
    except (TypeError, ValueError):
        return None
    if any(v != v or v <= 0 for v in values):          # NaN or absent
        return None
    stop, low, high, price, target = values
    if high < low:
        low, high = high, low
    span = target - stop
    if span <= 0:
        return None

    def at(value):
        return max(0.0, min(100.0, (value - stop) / span * 100.0))

    band_left, band_right = at(low), at(high)
    return {"risk": at(low), "reward": 100.0 - at(high),
            "band_left": band_left,
            "band_width": max(0.6, band_right - band_left),
            "now": at(price)}


#: The pre-registered threshold from scripts/research/ema20_entry_gate.py: the
#: close at or below its own twenty-day average. Zero is EMA20's neutral point,
#: not a cut chosen from the data.
EMA20_THRESHOLD = 0.0

EMA20_AT_OR_BELOW, EMA20_ABOVE, EMA20_UNMEASURED = "at_or_below", "above", "unmeasured"


def ema20_side(distance):
    """Which side of EMA20 the signal's close sat on, or unmeasured.

    Unmeasured is its own state and never a side: a missing distance classified
    as "above" would read as a measured judgement about a number nobody had.
    """
    try:
        value = float(distance)
    except (TypeError, ValueError):
        return EMA20_UNMEASURED
    if value != value:
        return EMA20_UNMEASURED
    return EMA20_AT_OR_BELOW if value <= EMA20_THRESHOLD else EMA20_ABOVE


def ema20_marker_text(distance):
    """The marker as plain text, for a table cell. An em dash when unmeasured."""
    side = ema20_side(distance)
    if side == EMA20_UNMEASURED:
        return "—"
    label = "تحت EMA20" if side == EMA20_AT_OR_BELOW else "فوق EMA20"
    return f"{float(distance):+.1f}% · {label}"


#: Why the marker exists, in the words the page shows. A marker, not a rule:
#: nothing in the decision changed.
EMA20_MARKER_HELP = (
    "بُعد إغلاق الإشارة عن متوسط EMA20. على السجل التاريخي (2017-2026)، "
    "صفقات هذه الاستراتيجية التي دخلت عند EMA20 أو تحته: معامل ربح 2.42 "
    "مقابل 1.54 للكل، ونسبة نجاح 69% مقابل 39%، في الفترتين قبل 2023 وبعدها. "
    "علامة للمتابعة وليست قاعدة: القرار نفسه لم يتغير، والسجل الحي لم يؤكدها بعد."
)


def ema20_marker_html(distance):
    """A chip beside the decision: which side of EMA20 the signal closed on.

    Blue for at-or-below, because that is the side measured better and blue is
    this interface's advisory colour -- never green, which would read as an
    instruction to buy. Grey for above. The unknown mark when unmeasured.
    """
    side = ema20_side(distance)
    if side == EMA20_UNMEASURED:
        return (f'<span class="sig" title="{html.escape(EMA20_MARKER_HELP)}" '
                f'style="color:var(--unknown);border:1px dashed var(--unknown)">'
                f'EMA20 —·—</span>')
    tone = _TONE["blue"] if side == EMA20_AT_OR_BELOW else _TONE["gray"]
    return (f'<span class="sig" title="{html.escape(EMA20_MARKER_HELP)}" '
            f'style="color:{tone[2]};background:{tone[0]};border:1px solid {tone[1]}">'
            f'{html.escape(ema20_marker_text(distance))}</span>')


def _badge_group(badge, marker):
    """The decision badge, with the EMA20 marker beside it when there is one.

    With no marker the badge is returned exactly as before, so a card that
    carries no marker renders byte for byte as it did.
    """
    if not marker:
        return badge
    return ('<span style="display:flex;gap:.3rem;flex-wrap:wrap;'
            'justify-content:flex-end;align-items:flex-start">'
            f'{badge}{marker}</span>')


def opportunity_card(*, ticker, name="", sector="", regime="", signal="",
                     tone="gray", figures=(), stop=None, buy_low=None,
                     buy_high=None, price=None, target2=None, marker_html=""):
    """One candidate: what it is, what was decided, and the trade to scale.

    ``marker_html`` is trusted markup built by a component in this module
    (``ema20_marker_html``), placed beside the decision badge.
    """
    signal_colour = _TONE.get(tone, _TONE["gray"])
    head = (
        f'<div class="head"><div><div>'
        f'<span class="tick">{html.escape(str(ticker))}</span>'
        + (f'<span class="name">{html.escape(str(name))}</span>' if name else "")
        + '</div>'
        # Joined, not concatenated: a name with no sector rendered as "· BULL",
        # an orphan separator where a fact should be.
        + (f'<div class="where">'
           + " · ".join(html.escape(str(part)) for part in (sector, regime) if part)
           + '</div>' if (sector or regime) else "")
        + '</div>'
        + (_badge_group(
            (f'<span class="sig" style="color:{signal_colour[2]};'
             f'background:{signal_colour[0]};border:1px solid {signal_colour[1]}">'
             f'{html.escape(str(signal))}</span>' if signal else ""),
            marker_html))
        + '</div>')

    cells = "".join(
        f'<div><div class="k">{html.escape(str(label))}</div>'
        f'<div class="v {css or ""}">{html.escape(str(value))}</div></div>'
        for label, value, css in figures)
    figs = f'<div class="figs">{cells}</div>' if figures else ""

    rail = ""
    places = _scale_positions(stop, buy_low, buy_high, price, target2)
    if places:
        rail = (
            f'<div><div class="legend">'
            f'<span class="lo">STOP {float(stop):,.2f}</span>'
            f'<span class="mid">[{float(buy_low):,.2f} – {float(buy_high):,.2f}]</span>'
            f'<span class="hi">TGT2 {float(target2):,.2f}</span></div>'
            f'<div class="scale">'
            f'<span class="risk" style="width:{places["risk"]:.2f}%"></span>'
            f'<span class="reward" style="width:{places["reward"]:.2f}%"></span>'
            f'<span class="band" style="left:{places["band_left"]:.2f}%;'
            f'width:{places["band_width"]:.2f}%"></span>'
            f'<span class="now" style="left:{places["now"]:.2f}%"></span>'
            f'</div></div>')

    css_tone = str(signal).strip().lower().split("/")[0].strip()
    css_tone = css_tone if css_tone in ("buy", "watch", "avoid") else ""
    return (f'<div class="egx-opp {css_tone}">{head}{figs}{rail}</div>')


def opportunity_grid(cards):
    return f'<div class="egx-opps">{"".join(cards)}</div>'


def attrition_bar(stages, survived_label="survived", total=None):
    """How a universe narrowed, drawn to scale.

    ``stages`` is ``(label, count)`` pairs for each gate that refused symbols;
    ``survived_label`` names the final segment, whose count is whatever is left
    of ``total``. Segments are ordered largest refusal first, because the
    question the bar answers is what removed most of the universe.

    Returns the markup; it renders nothing itself.

    A stage of zero is dropped from the bar but kept in the legend: a gate that
    refused nobody this week is a fact about the week, and a legend that omits
    it silently reads as a gate that does not exist.
    """
    rows = [(str(label), max(0, int(count or 0))) for label, count in stages]
    refused = sum(count for _, count in rows)
    universe = int(total) if total is not None else refused
    survivors = max(0, universe - refused)
    if universe <= 0:
        return ""

    def share(count):
        return count / universe * 100.0

    segments, alternate = [], False
    for _, count in sorted(rows, key=lambda row: -row[1]):
        if not count:
            continue
        css = "refused alt" if alternate else "refused"
        alternate = not alternate
        segments.append(f'<span class="{css}" style="width:{share(count):.3f}%"></span>')
    if survivors:
        segments.append(
            f'<span class="survived" style="width:{share(survivors):.3f}%"></span>')

    legend = [
        f'<div class="g row-survived"><i style="background:var(--green)"></i>'
        f'{html.escape(str(survived_label))}</div>'
        f'<div class="n row-survived">{survivors:,}</div>'
        f'<div class="p">{share(survivors):.1f}%</div>'
    ]
    for label, count in sorted(rows, key=lambda row: -row[1]):
        legend.append(
            f'<div class="g"><i style="background:var(--border-active)"></i>'
            f'{html.escape(label)}</div>'
            f'<div class="n">{count:,}</div>'
            f'<div class="p">{share(count):.1f}%</div>'
        )
    return (f'<div class="egx-funnel">{"".join(segments)}</div>'
            f'<div class="egx-funnel-legend">{"".join(legend)}</div>')


#: What a holding's card is saying. Four kinds, because "sell this now", "the
#: rule behind this was never measured", "no price I trust, so no advice" and
#: "nothing was ever measured for this holding" are four different facts and
#: were three generic banners and a card.
ACT, UNVALIDATED, WITHHELD_KIND, NO_PLAN = "act", "unvalidated", "withheld", "noplan"


def action_card_html(ticker, *, kind=ACT, tone="gray", name="", badges=(),
                     why="", figures=()):
    """One holding's card. ``figures`` is ``(label, value, tone)`` triples.

    ``tone`` colours the leading rail for an ``ACT`` card only; the other three
    kinds carry their own, because what they mean does not vary by action.
    """
    head = f'<span class="tick">{html.escape(str(ticker))}</span>'
    if name:
        head += f'<span class="name">{html.escape(str(name))}</span>'
    numbers = ""
    if figures:
        cells = []
        for label, value, figure_tone in figures:
            css = f" {figure_tone}" if figure_tone in ("pos", "neg") else ""
            cells.append(f'<div><span class="k">{html.escape(str(label))}</span>'
                         f'<span class="v{css}">{html.escape(str(value))}</span></div>')
        numbers = f'<div class="nums">{"".join(cells)}</div>'
    return (
        f'<div class="egx-act k-{html.escape(kind)} t-{html.escape(tone)}">'
        f'<div class="top"><div>{head}</div>'
        f'<div class="badges">{"".join(badges)}</div></div>'
        f'<div class="why">{why}</div>{numbers}</div>'
    )


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
    """The mark, in the terminal's own voice.

    It carried a 📈 and two hardcoded hexes that no longer exist in the palette
    -- the last emoji left in the chrome after the navigation lost twelve.
    """
    st.sidebar.markdown(
        """<div style="padding:.5rem .35rem .7rem;border-bottom:1px solid var(--border);
             margin-bottom:.5rem">
          <div style="font-family:var(--font-mono);font-size:.72rem;font-weight:600;
             letter-spacing:.22em;color:var(--text)">EGX AI TRADER</div>
          <div style="font-family:var(--font-ar);font-size:.72rem;color:var(--text-low);
             margin-top:.25rem">متداول البورصة المصرية · بحث كمي ومتابعة آمنة</div>
        </div>""", unsafe_allow_html=True)
