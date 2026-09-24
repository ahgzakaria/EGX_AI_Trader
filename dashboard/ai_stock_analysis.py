"""AI Stock Analysis — manual, one-symbol, on-demand analysis page.

تحليل سهم بالذكاء الاصطناعي · AI Stock Analysis

The page renders ALREADY-COMPUTED typed evidence. It performs no calculation of its own:
no provider is contacted, no indicator is computed, no universe is scanned, and nothing at
all is analysed until the user picks one symbol and presses *Analyze Stock*. All numbers
come from dedicated typed fields on the contract objects — never from narrative prose and
never from a machine recommendation reason.

The default runner is the real one-symbol Core service. Tests may inject a fixture runner,
but the production page never imports a fixture and never contacts a provider directly.
"""

from __future__ import annotations

import html
import re

import streamlit as st

from core.ai_analysis_evidence import EVIDENCE_ENGINE_VERSION
from core.analysis_card_generator import CARD_SIZES, DEFAULT_CARD_SIZE
from core.analysis_export import (
    CARD_TYPE_LABELS,
    CARD_TYPES,
    COMPACT,
    LANGUAGE_LABELS as EXPORT_LANGUAGE_LABELS,
    RESOLUTION_LABELS,
    ExportRequest,
    ExportUnavailable,
    export_cache_key,
    export_filename,
    render_export,
    resolutions_for,
)
from core.analysis_infographic import LANGUAGES as EXPORT_LANGUAGES
from core.analysis_chart import (
    DEFAULT_TIMEFRAME,
    TIMEFRAME_LABELS,
    TIMEFRAMES,
    build_daily_figure,
    figure_to_png,
)
from core.analysis_presentation import build_presentation
from core.detailed_analysis_card import (
    DEFAULT_DETAILED_SIZE,
    DEFAULT_LANGUAGE,
    DETAILED_CARD_SIZES,
    DETAILED_SIZE_LABELS,
    LANGUAGE_LABELS,
    LANGUAGES,
    render_detailed_card_png,
)
from core.symbols import (
    ApprovedSymbol,
    load_approved_symbol_options,
    resolve_approved_symbol,
)
from dashboard.ai_stock_analysis_components import (
    CARD_KIND_LABELS,
    CARD_SIZE_LABELS,
    EM_DASH,
    NARRATIVE_CSS,
    RECOMMENDATION_LABELS,
    SAFETY_BADGES,
    VOLUME_UNAVAILABLE_LABELS,
    card_cache_key,
    confidence_rows,
    data_mode,
    data_quality_warnings,
    fmt_score,
    generate_card_bytes,
    include_live_in_session_range,
    indicator_groups,
    indicator_readings,
    is_auction,
    isolate_ltr,
    key_level_rows,
    market_phase_labels,
    momentum_reading,
    narrative_section_views,
    narrative_source_block,
    narrative_summary_cells,
    narrative_technical_rows,
    normalize_symbol,
    price_summary_rows,
    provenance_rows,
    pullback_scenario_view,
    scenario_view,
    trend_reading,
    volume_analysis_available,
)
from dashboard.ui import (
    apply_global_style,
    badge_html,
    empty_state,
    metric_card,
    page_header,
    section_header,
    TONE_VARS,
    unavailable_state,
    status_bar,
)
from dashboard.formatting import company_name, symbol_option_label
from dashboard.provenance_panel import (
    FROZEN,
    MIXED,
    Provenance,
    render_provenance_panel,
)

# Session-state keys. Analysis and the rendered card both live here so a Streamlit rerun
# never re-runs an analysis and never re-renders a card that has not changed.
STATE_BUNDLE = "_ai_analysis_bundle"
STATE_SYMBOL = "_ai_analysis_symbol"
STATE_CARD = "_ai_analysis_card"
STATE_CARD_KEY = "_ai_analysis_card_key"
STATE_HISTORY_OPEN = "_ai_analysis_history_open"
STATE_SELECTED = "_ai_analysis_selected_symbol"
STATE_PICKER = "_ai_analysis_symbol_picker"

PAGE_TITLE_AR = "تحليل سهم بالذكاء الاصطناعي"
PAGE_TITLE_EN = "AI Stock Analysis"


# --------------------------------------------------------------------------- #
# Analysis runner
# --------------------------------------------------------------------------- #

def _default_runner(symbol: str):
    """Run the real service and append one immutable history record."""
    from core.ai_stock_analysis_history import AnalysisHistoryStore
    from core.ai_stock_analysis_service import analyze_symbol

    return analyze_symbol(symbol, history_store=AnalysisHistoryStore())


def analysis_is_permitted(context):
    """Whether the AI engine may be invoked for a current analysis.

    Checked BEFORE the runner, so a stale symbol never spends an external
    request only to be refused afterwards.
    """

    return bool(context is not None and context.may_invoke_ai)


def run_analysis(symbol, runner=None, *, context=None):
    """Invoke the AI engine, or refuse without invoking it.

    ``context`` is an ``AnalysisFreshnessContext``. When it is supplied and the
    symbol is not current, the runner is never called: the audit found this
    path had no freshness gate at all, so a 2026-07-30 candle produced a
    current-looking advisory and consumed an external request doing it.
    """

    if context is not None and not analysis_is_permitted(context):
        return {
            "blocked": True,
            "reason": context.blocking_reason,
            "message": context.ai_blocked_message(),
            "freshness": context,
        }
    """Analyze exactly ONE symbol. Raises on a collection — there is no batch path here."""
    ticker = normalize_symbol(symbol)
    return (runner or _default_runner)(ticker)


STATE_FRESHNESS_IDENTITY = "ai_analysis_freshness_identity"


def _discard_outdated_analysis(selected_symbol, context=None):
    """Drop a cached bundle whose freshness identity no longer holds.

    Comparing the symbol alone was not enough: an analysis produced while
    2026-07-30 was current stayed cached and was re-rendered under a
    2026-08-03 heading, because the symbol had not changed.
    """

    from services.analysis_freshness_service import cached_result_is_current

    _discard_stale_analysis(selected_symbol)
    if context is None:
        return
    stored = st.session_state.get(STATE_FRESHNESS_IDENTITY)
    if not cached_result_is_current(context, stored):
        st.session_state[STATE_BUNDLE] = None
        st.session_state[STATE_CARD] = None
        st.session_state[STATE_CARD_KEY] = None
        st.session_state[STATE_FRESHNESS_IDENTITY] = None


def _discard_stale_analysis(selected_symbol):
    """Never render a stored bundle under a newly selected ticker."""
    if not selected_symbol:
        return
    bundle = st.session_state.get(STATE_BUNDLE)
    stored_symbol = getattr(getattr(getattr(bundle, "result", None), "request", None),
                            "symbol", None)
    if bundle is not None and stored_symbol != normalize_symbol(selected_symbol):
        st.session_state[STATE_BUNDLE] = None
        st.session_state[STATE_SYMBOL] = None
        st.session_state[STATE_CARD] = None
        st.session_state[STATE_CARD_KEY] = None


# --------------------------------------------------------------------------- #
# Small render helpers
# --------------------------------------------------------------------------- #

#: A value is "numeric" when it is only digits, separators and units — those keep the
#: LTR tabular presentation. Anything containing Arabic or prose is narrative and is
#: rendered right-to-left so it wraps on word boundaries.
_NUMERIC_VALUE = re.compile(r"^[\s\d.,%/:+\-–—()A-Za-z]*$")


def _is_numeric_value(text):
    return bool(_NUMERIC_VALUE.fullmatch(str(text)))


def _isolate_identifiers(text):
    """Bidi-isolate technical ASCII identifiers inside Arabic narrative.

    Without isolation an ``EMA20_ABOVE_EMA50`` style token embedded in a
    right-to-left sentence reorders around the surrounding Arabic.
    """
    escaped = html.escape(str(text))
    # A technical token is kept WHOLE: "ai-narrative@2" and "COMI.EGX" must not be
    # fragmented into separate isolates, or a reader (or a grep) sees them split.
    return re.sub(r"([A-Za-z][A-Za-z0-9_]*(?:[.@\-][A-Za-z0-9_]+)*)",
                  r'<bdi dir="ltr">\1</bdi>', escaped)


def _kv_table(rows):
    """A compact right-to-left label/value table.

    Numeric values keep the LTR tabular look; narrative values render RTL and wrap
    normally, so Arabic never degrades into one glyph per line.
    """
    cells = []
    for label_ar, label_en, value in rows:
        numeric = _is_numeric_value(value)
        klass = "v num" if numeric else "v"
        rendered = (html.escape(str(value)) if numeric
                    else _isolate_identifiers(value))
        cells.append(
            f'<tr><td class="k">{html.escape(str(label_ar))}'
            f'<span class="en">{html.escape(str(label_en))}</span></td>'
            f'<td class="{klass}">{rendered}</td></tr>')
    st.markdown(f'<table class="egx-kv">{"".join(cells)}</table>',
                unsafe_allow_html=True)


def _text_metric_card(label_ar, value, label_en=""):
    """A metric card whose value is a PHRASE rather than a number.

    Uses the smaller ``.text`` value style so a long localized label wraps on word
    boundaries instead of force-breaking and inflating the whole row's height.
    """
    st.markdown(
        f'<div class="egx-metric"><div class="v text">'
        f'{_isolate_identifiers(value)}</div>'
        f'<div class="lar">{html.escape(str(label_ar))}</div>'
        + (f'<div class="len">{html.escape(str(label_en))}</div>' if label_en else "")
        + "</div>", unsafe_allow_html=True)


def _page_style():
    """Page-scoped additions to the shared dark terminal theme."""
    st.markdown(
        """
        <style>
        .egx-kv { width:100%; border-collapse:collapse; direction:rtl;
            table-layout:fixed; }
        .egx-kv td { padding:.34rem .55rem; border-bottom:1px solid var(--border); font-size:.86rem;
            vertical-align:top; }
        .egx-kv tr:last-child td { border-bottom:0; }
        /* The key column must YIELD rather than pin itself wide: a nowrap key kept
           its full intrinsic width and squeezed the value column until long Arabic
           narrative had almost no room to wrap in. */
        .egx-kv td.k { color:var(--muted); width:34%; white-space:normal;
            word-break:normal; overflow-wrap:break-word; }
        .egx-kv td.k .en { display:block; font-size:.62rem; letter-spacing:.03em;
            text-transform:uppercase; opacity:.72; direction:ltr; text-align:right; }
        /* Default value cell is NARRATIVE-safe: Arabic reads right-to-left and wraps
           on word boundaries. break-all/anywhere are deliberately NOT used — they are
           what shattered Arabic into one glyph per line. */
        .egx-kv td.v { color:var(--text); font-weight:700; direction:rtl;
            text-align:right; white-space:normal; word-break:normal;
            overflow-wrap:break-word; }
        /* Numeric/technical values keep the tabular LTR presentation. */
        .egx-kv td.v.num { direction:ltr; text-align:left;
            font-variant-numeric:tabular-nums; }
        .egx-kv td.v bdi { unicode-bidi:isolate; }
        .egx-panel { background:var(--surface); border:1px solid var(--border);
            border-radius:var(--r-card); padding:.7rem .85rem; height:100%; }
        .egx-panel h4 { margin:0 0 .45rem; font-size:.86rem; direction:rtl; }
        .egx-panel h4 span { display:block; font-size:.62rem; color:var(--muted);
            text-transform:uppercase; letter-spacing:.04em; direction:ltr; text-align:right; }
        .egx-scenario { background:var(--surface); border:1px solid var(--border);
            border-left:3px solid var(--blue); border-radius:var(--r-card); padding:.8rem .95rem; }
        .egx-scenario .t { font-weight:800; font-size:.98rem; direction:rtl; }
        .egx-conf { display:flex; align-items:center; gap:.6rem; margin:.28rem 0; }
        .egx-conf .lbl { min-width:150px; font-size:.8rem; direction:rtl; }
        .egx-conf .bar { flex:1; height:8px; border-radius:999px; background:var(--surface-2);
            border:1px solid var(--border); overflow:hidden; }
        .egx-conf .bar i { display:block; height:100%; background:linear-gradient(90deg,#2563eb,#34d399); }
        .egx-conf .num { min-width:52px; text-align:left; font-weight:750; font-size:.82rem;
            font-variant-numeric:tabular-nums; }
        .egx-req { direction:rtl; font-size:.83rem; color:var(--muted); margin:.12rem 0;
            white-space:normal; word-break:normal; overflow-wrap:break-word; }
        /* A metric whose value is a PHRASE, not a number. The default .v is 1.45rem,
           which forces a long token to break mid-word inside a ~200px card and adds
           a phantom second line to every card in the row. */
        .egx-metric .v.text { font-size:.92rem; font-weight:700; line-height:1.28;
            direction:rtl; text-align:right; white-space:normal; word-break:normal;
            overflow-wrap:break-word; }
        .egx-metric .v.text bdi { unicode-bidi:isolate; }
        /* A reading: what two supplied numbers say when compared, with the
           comparison named at the far end of the same line. One line each, so
           five readings cost five rows rather than ten. The tone is carried on
           a rule at the row's leading edge rather than on the label, so an
           amber row stays as legible as a grey one.

           The label column is a fixed width, not a percentage: at 40% of a
           1300px page it was a 520px column holding a four-word label, which
           left the reading itself stranded in the middle of the row. */
        .egx-reads { display:flex; flex-direction:column; gap:1px;
            background:var(--border); border:1px solid var(--border); }
        .egx-read { display:flex; align-items:baseline; gap:.7rem; direction:rtl;
            background:var(--surface); padding:.42rem .7rem;
            border-right:3px solid var(--tone); }
        .egx-read .k { flex:0 0 13rem; font-size:.8rem; color:var(--muted); }
        .egx-read .r { flex:0 0 auto; min-width:11rem; font-weight:700;
            font-size:.88rem; color:var(--tone); }
        /* The basis follows the reading rather than being flung to the far
           edge: left-aligned in an 880px track it began 890px away from the
           number it explains. */
        .egx-read .b { flex:1; text-align:right; font-size:.7rem;
            color:var(--text-low); }
        .egx-read .r bdi, .egx-read .b bdi { unicode-bidi:isolate; }
        </style>
        """, unsafe_allow_html=True)


def _confidence_bar(label_ar, label_en, score_text, value, supplied):
    width = 0 if not supplied or value is None else max(0.0, min(100.0, float(value)))
    fill = (f'<i style="width:{width:.1f}%"></i>' if supplied else "")
    st.markdown(
        f'<div class="egx-conf"><span class="lbl">{html.escape(label_ar)} '
        f'<small style="opacity:.6">{html.escape(label_en)}</small></span>'
        f'<span class="bar">{fill}</span>'
        f'<span class="num">{html.escape(score_text)}</span></div>',
        unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def _symbol_selector():
    """Return one approved symbol and button state without touching market data.

    Streamlit 1.58 performs fuzzy client-side filtering for every keystroke.
    The complete approved list is supplied to that one control; accepting typed
    text only lets us validate exact ticker/name input and never forwards an
    arbitrary value to Core.
    """

    section_header("اختيار السهم", "Symbol Selection — one symbol per analysis")
    options = load_approved_symbol_options()
    picker_col, button_col = st.columns([4.8, 1.2])
    with picker_col:
        picked = st.selectbox(
            "اختر أو ابحث عن سهم · Search or select a symbol",
            options,
            index=None,
            format_func=lambda option: (
                option.display_label
                if isinstance(option, ApprovedSymbol)
                else str(option)
            ),
            placeholder="اكتب رمز السهم أو اسم الشركة",
            key=STATE_PICKER,
            accept_new_options=True,
            filter_mode="fuzzy",
            width="stretch",
        )
    selected = resolve_approved_symbol(picked, options)
    if picked not in (None, "") and selected is None:
        st.warning("لم يتم العثور على سهم مطابق · No matching symbol found")
    if selected is not None:
        st.session_state[STATE_SELECTED] = selected.ticker

    with button_col:
        st.markdown('<div style="height:1.75rem"></div>', unsafe_allow_html=True)
        pressed = st.button("▶ تحليل السهم · Analyze Stock", type="primary",
                            width="stretch", key="_ai_analysis_go",
                            disabled=selected is None)
    return (selected.ticker if selected else None), bool(pressed and selected)


def _status_section(result):
    phase_ar, phase_en, phase_tone = market_phase_labels(result.market_phase)
    mode_ar, mode_en, mode_tone = data_mode(result)
    quality = result.data_quality
    status_bar([
        ("الجلسة · Session", f"{phase_ar} · {phase_en}", phase_tone),
        ("البيانات · Data", f"{mode_ar} · {mode_en}", mode_tone),
        ("المزود · Provider", str(quality.provider or EM_DASH), "blue"),
        ("آخر جلسة · Last Session", str(quality.latest_completed_session or EM_DASH), "gray"),
        ("الحداثة · Freshness", str(quality.freshness_status or EM_DASH), "gray"),
        ("الاستخدام · Use",
         "للاطلاع فقط · READ ONLY" if not quality.automatic_use_permitted
         else "مسموح · PERMITTED",
         "red" if not quality.automatic_use_permitted else "green"),
    ])
    if not quality.automatic_use_permitted:
        # Above the fold and not inside the warnings list further down the page.
        # This does not qualify one figure, it qualifies every conclusion drawn
        # from the whole screen, and a reader who has scrolled past it is a
        # reader who has already read the numbers.
        st.error(
            "**محجوز للمراجعة اليدوية · HELD FOR MANUAL REVIEW** — هذا السهم "
            "مصنّف ضمن الطبقة المحجوزة، والأرقام تحته معروضة للاطلاع فقط: لا "
            "يُبنى عليها قرار آلي ولا تحجيم مركز. · The numbers below are shown "
            "for reading only; no automatic decision and no position sizing may "
            "be taken from them."
            + (f"\n\n`{quality.held_reason}`" if quality.held_reason else ""))
    if is_auction(result.market_phase):
        st.info("مزاد الإغلاق (14:15–14:25): تُعرض بيانات المزاد منفصلة ولا تُدمج مع نطاق الجلسة "
                "المستمرة. · Closing auction — auction data is shown separately and is never "
                "merged into the continuous-session range.")


def analysis_provenance(result):
    """Return the typed AI snapshot provenance without deriving market values."""

    quality = result.data_quality
    live = bool(quality.live_available and result.price.last is not None)
    data_timestamp = (
        result.price.quote_timestamp
        if live
        else result.price.session_date or quality.latest_completed_session
    )
    return Provenance(
        data_timestamp=str(data_timestamp or ""),
        signal_timestamp=str(result.generated_at or ""),
        session=str(quality.latest_completed_session or result.price.session_date or ""),
        provider=str(quality.provider or ""),
        live_provider=str(quality.live_provider or "") if live else "",
        engine=EVIDENCE_ENGINE_VERSION,
        evidence_hash=str(result.evidence_hash or result.evidence_version or ""),
        status=MIXED if live else FROZEN,
    )


def _price_section(result):
    section_header("ملخص السعر", "Price Summary — typed evidence fields")
    rows = price_summary_rows(result)
    headline = rows[0]
    session_rows = [row for row in rows[1:] if row[4] == "session"]
    # Do not render an empty "live" block after close/holiday: that could imply an old
    # stored quote is current. Missing live evidence remains absent, not relabelled.
    live_rows = [
        row for row in rows[1:] if row[4] == "live" and row[2] != EM_DASH
    ]

    for start in range(0, len([headline] + session_rows), 4):
        chunk = ([headline] + session_rows)[start:start + 4]
        for column, (label_ar, label_en, value, tone, _) in zip(st.columns(4), chunk):
            with column:
                metric_card(label_ar, value, label_en=label_en, tone=tone)

    if live_rows:
        auction = is_auction(result.market_phase)
        caption = ("لقطة مزاد الإغلاق — منفصلة عن الجلسة المستمرة · Closing-auction snapshot — "
                   "kept separate from the continuous session" if auction else
                   "تراكب التسعيرة الحية · Live quote overlay")
        section_header("التسعيرة الحية" if not auction else "بيانات مزاد الإغلاق", caption)
        for column, (label_ar, label_en, value, tone, _) in zip(st.columns(4), live_rows):
            with column:
                metric_card(label_ar, value, label_en=label_en, tone=tone)
        if not include_live_in_session_range(result.market_phase):
            st.caption("هذه القيم لا تدخل في نطاق الجلسة المستمرة أعلاه. · These values are not "
                       "folded into the continuous-session range above.")


def _chart_section(result, presentation=None):
    """Render the typed daily series. All levels come from the presentation model.

    The intraday tab stays because the result type still carries the series,
    but it is labelled for what it is: the Rubix feed that produced it was
    retired on 2026-09-10, so there is no current intraday data to show.
    """
    section_header("الرسم البياني", "Typed daily series")
    try:
        import plotly.graph_objects as go                                  # noqa: F401
    except Exception:
        st.caption("Plotly is unavailable in this environment; the chart is skipped.")
        return

    presentation = presentation or build_presentation(result, None)
    daily = result.daily_chart_series
    intraday = result.intraday_chart_series
    if daily is None or not daily.points:
        empty_state("لا توجد بيانات كافية للرسم", "No typed daily chart series was supplied.")
        return

    daily_tab, intraday_tab = st.tabs(("Daily · يومي", "Intraday · لحظي (متوقف)"))

    with daily_tab:
        timeframe = st.radio(
            "المدى الزمني · Timeframe", list(TIMEFRAMES),
            format_func=lambda key: TIMEFRAME_LABELS[key], horizontal=True,
            key="_ai_analysis_timeframe",
            index=list(TIMEFRAMES).index(DEFAULT_TIMEFRAME))
        st.caption(
            "المدى الزمني يغيّر عدد الشموع المعروضة فقط ولا يعيد حساب أي مستوى · "
            "Timeframe changes the displayed candles only; analysis levels are unchanged."
        )
        figure = build_daily_figure(presentation, timeframe=timeframe)
        if figure is None:
            empty_state("لا توجد بيانات كافية للرسم", "No candles in the selected window.")
        else:
            st.plotly_chart(figure, width="stretch")
            st.caption(
                "خطوط متصلة: اختراق/إبطال · متقطعة: دعم ومقاومة · منقّطة: مستويات ثانوية · "
                "نطاق بنفسجي: منطقة تصحيح بحثية · Solid: breakout/invalidation · "
                "Dashed: support/resistance · Dotted: minor · Purple band: research pullback zone"
            )
            png = figure_to_png(figure)
            if png:
                # The SAME figure object that is displayed is the one exported.
                st.download_button(
                    "⬇ تنزيل الرسم PNG · Download chart PNG", data=png,
                    file_name=f"{presentation.ticker}_daily_{timeframe.lower()}.png",
                    mime="image/png", key="_ai_analysis_chart_dl")

    with intraday_tab:
        if intraday is None or not intraday.points:
            empty_state("لا توجد بيانات لحظية · No intraday data",
                        "أُوقف مصدر البيانات اللحظية (Rubix) في 2026-09-10، "
                        "ولا يوجد مصدر لحظي حالياً. · The intraday source (Rubix) "
                        "was retired on 2026-09-10; there is no live intraday data.")
        else:
            points = intraday.points
            intraday_figure = go.Figure()
            intraday_figure.add_trace(go.Candlestick(
                x=[point.timestamp for point in points],
                open=[point.open for point in points],
                high=[point.high for point in points],
                low=[point.low for point in points],
                close=[point.close for point in points],
                name=f"{intraday.source} · {intraday.timeframe}",
                increasing_line_color="#34d399", decreasing_line_color="#f87171"))
            intraday_figure.update_layout(
                template="plotly_dark", height=470,
                margin=dict(l=70, r=130, t=24, b=34),
                paper_bgcolor="#0b1220", plot_bgcolor="#0e1729",
                xaxis=dict(showgrid=False), yaxis=dict(gridcolor="#223049", title="EGP"),
                xaxis_rangeslider_visible=False, showlegend=True,
                legend=dict(orientation="h", y=1.12, bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(intraday_figure, width="stretch")


def _readings_list(readings):
    """Render `(label, reading, tone, basis)` rows.

    Each row is a comparison between two numbers the evidence engine already
    supplied, and the small line under it names which two. Nothing is computed
    here.
    """
    rows = []
    for label_ar, reading, tone, basis in readings:
        colour = TONE_VARS.get(tone, "var(--muted)")
        rows.append(
            f'<div class="egx-read" style="--tone:{colour}">'
            f'<span class="k">{html.escape(str(label_ar))}</span>'
            f'<span class="r">{_isolate_identifiers(reading)}</span>'
            f'<span class="b">{_isolate_identifiers(basis)}</span></div>')
    st.markdown(f'<div class="egx-reads">{"".join(rows)}</div>',
                unsafe_allow_html=True)


def _technical_section(result):
    section_header("النظرة الفنية", "Technical Overview — supplied typed fields only")
    indicators = result.indicators
    groups = indicator_groups(indicators)

    trend_ar, trend_en, trend_tone, trend_basis = trend_reading(result)
    momentum_ar, momentum_en, momentum_tone, momentum_basis = momentum_reading(result)
    left, right = st.columns(2)
    with left:
        metric_card("الاتجاه", trend_ar, label_en=f"Trend · {trend_en}", tone=trend_tone,
                    sub=trend_basis)
    with right:
        metric_card("الزخم", momentum_ar, label_en=f"Momentum · {momentum_en}",
                    tone=momentum_tone, sub=momentum_basis)

    # The readings lead, and the sixteen raw values sit behind one disclosure.
    # The panels used to BE the section: five columns of numbers with nothing
    # said about any of them, including two panels of moving averages a tenth of
    # a pound apart and an OBV total whose absolute value carries no meaning.
    # The completed session's close, not the live print: every indicator
    # here was computed on completed sessions, and comparing a live quote
    # against them would compare two different clocks.
    close = result.price.close if result.price else None
    readings = indicator_readings(indicators, close)
    if readings:
        _readings_list(readings)
    else:
        unavailable_state("لا توجد قراءة", "No reading can be made",
                          "لم تصل أي من قيم المؤشرات المطلوبة للمقارنة.")

    with st.expander("القيم الخام للمؤشرات · Raw indicator values", expanded=False):
        columns = st.columns(len(groups))
        for column, (title_ar, title_en, rows) in zip(columns, groups):
            with column:
                st.markdown(f'<div class="egx-panel"><h4>{html.escape(title_ar)}'
                            f'<span>{html.escape(title_en)}</span></h4>',
                            unsafe_allow_html=True)
                _kv_table(rows)
                st.markdown("</div>", unsafe_allow_html=True)

    if not volume_analysis_available(indicators):
        arabic, english = VOLUME_UNAVAILABLE_LABELS
        st.warning(f"{arabic} · {english} — نسبة الحجم و OBV غير معروضة لأن الحجم غير موثوق ضمن "
                   f"نافذة المراجعة. Volume Ratio and OBV are withheld because volume is not "
                   f"lookback-safe.")
        st.caption(f"volume_series: {indicators.volume_series} · "
                   f"volume_adjustment_policy: {indicators.volume_adjustment_policy}")


def _levels_section(result):
    section_header("المستويات الرئيسية", "Key Levels — as supplied, never recalculated")
    rows = key_level_rows(result)
    if not any(row["present"] for row in rows):
        empty_state("لا توجد مستويات", "The evidence engine supplied no key levels.")
        return
    import pandas as pd
    frame = pd.DataFrame([{
        "المستوى / Level": f'{row["label_ar"]} · {row["label_en"]}',
        "القيمة / Value": row["value"],
        "الأساس / Basis": row["basis"],
        "الإطار الزمني / Timeframe": row["timeframe"],
        "القوة / Strength": row["strength"],
        "اللمسات / Touches": row["touches"],
        "آخر لمسة / Last Touch": row["last_touch_date"],
        "المسافة / Distance": row["distance"],
    } for row in rows])
    st.dataframe(frame, width="stretch", hide_index=True)
    st.caption("كل الحقول محسوبة في Core ومورّدة عبر العقد؛ لا تستنتج الواجهة أي مستوى. · "
               "All fields are Core-calculated and contract-supplied; the UI estimates none.")


def _scenario_section(result):
    section_header("السيناريوهات", "Scenario Cards — typed scenario fields")
    if not result.scenarios:
        empty_state("لا توجد سيناريوهات", "The evidence engine supplied no scenarios.")
        return
    for scenario in result.scenarios:
        view = scenario_view(scenario)
        state_badge = badge_html(f'{view["state_ar"]} · {view["state_en"]}', view["tone"])
        st.markdown(
            f'<div class="egx-scenario"><div class="t">{html.escape(view["title"])}</div>'
            f'<div style="margin:.4rem 0">{state_badge}</div></div>',
            unsafe_allow_html=True)
        columns = st.columns(7)
        cells = (("التفعيل", "Trigger", view["trigger"]),
                 ("نطاق الدخول", "Entry", view["entry"]),
                 ("الهدف", "Target", view["target"]),
                 ("الوقف", "Stop", view["stop"]),
                 ("المساحة المتبقية", "Remaining Room", view["remaining_room"]),
                 ("العائد/المخاطرة", "Risk / Reward", view["risk_reward"]),
                 ("ثقة السيناريو", "Confidence", view["confidence"]))
        for column, (label_ar, label_en, value) in zip(columns, cells):
            with column:
                metric_card(label_ar, value, label_en=label_en)
        left, right = st.columns(2)
        with left:
            st.markdown('<div class="egx-req"><b>شروط التأكيد · Confirmation</b></div>',
                        unsafe_allow_html=True)
            for requirement in view["confirmations"] or ("—",):
                st.markdown(f'<div class="egx-req">• {html.escape(str(requirement))}</div>',
                            unsafe_allow_html=True)
        with right:
            st.markdown('<div class="egx-req"><b>شروط الإبطال · Invalidation</b></div>',
                        unsafe_allow_html=True)
            for condition in view["invalidations"] or ("—",):
                st.markdown(f'<div class="egx-req">• {html.escape(str(condition))}</div>',
                            unsafe_allow_html=True)
        st.markdown("<div style='height:.5rem'></div>", unsafe_allow_html=True)


def _pullback_section(result):
    section_header("تحليل جودة التصحيح", "Pullback Health Analysis")
    view = pullback_scenario_view(getattr(result, "pullback_scenario", None))
    if view is None:
        empty_state("السيناريو غير متاح", "Pullback evidence was not supplied.")
        return
    state_badge = badge_html(f'{view["state_ar"]} · {view["state_en"]}', view["tone"])
    research_badge = badge_html("Research Only — غير معتمد كإشارة دخول", "amber")
    st.markdown(
        f'<div class="egx-scenario"><div class="t">تحليل جودة التصحيح · '
        f'Pullback Health Analysis</div><div style="margin:.4rem 0">'
        f'<span class="egx-req">الحالة الحالية · Current State:</span> '
        f'{state_badge} {research_badge}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.warning(
        "Historical calibration did not demonstrate a positive managed-trade edge. "
        "This analysis describes correction quality and does not represent a BUY signal.\n\n"
        "لم تثبت المعايرة التاريخية أفضلية إيجابية بعد تطبيق الوقف والأهداف.\n\n"
        "هذا التحليل يصف جودة التصحيح ولا يمثل إشارة شراء."
    )
    if view["calculation_error"]:
        st.error(
            "تعذر حساب هذا الجزء محليًا؛ بقيت بقية صفحة التحليل متاحة. · "
            "Local Pullback diagnostic error; the rest of AI Analysis is unaffected."
        )
    # Trend quality is a PHRASE — it gets its own wider cell and the text style, so a
    # localized label never force-breaks the way the raw enum did.
    trend_column, *first = st.columns([2, 1, 1, 1, 1, 1])
    with trend_column:
        _text_metric_card("جودة الاتجاه", view["trend"], label_en="Trend Quality")
    first_cells = (
        ("آخر قمة مؤكدة", "Last Confirmed Swing High", view["swing_high"]),
        ("قاع بداية الموجة", "Impulse Start Low", view["impulse_low"]),
        ("نسبة التصحيح", "Pullback", view["pullback_percent"]),
        ("عمق ATR", "Pullback ATR", view["pullback_atr"]),
        ("ارتداد الدفعة", "Impulse Retracement", view["retracement"]),
    )
    for column, cell in zip(first, first_cells):
        with column:
            metric_card(cell[0], cell[2], label_en=cell[1])
    second = st.columns([1, 1, 1, 2, 1.4, 1])
    second_cells = (
        ("عدد الجلسات", "Correction Bars", view["correction_bars"], False),
        ("EMA20", "EMA20", view["ema20"], False),
        ("EMA50", "EMA50", view["ema50"], False),
        ("علاقة المتوسطات", "EMA Relationship", view["ema_relation"], True),
        ("منطقة الدعم", "Support Zone", view["support_zone"], False),
        ("وصل للدعم؟", "Support Reached", view["support_reached"], True),
    )
    for column, (label_ar, label_en, value, is_text) in zip(second, second_cells):
        with column:
            if is_text:
                _text_metric_card(label_ar, value, label_en=label_en)
            else:
                metric_card(label_ar, value, label_en=label_en)
    details = [
        ("عناصر تداخل الدعم", "Support Confluence", view["confluence"]),
        ("سلوك حجم التداول", "Volume Behaviour", view["volume"]),
        ("دليل الارتداد", "Reversal Evidence", view["confirmation"]),
        ("المقاومة القريبة", "Nearby Resistance", view["minor_resistance"]),
        ("سبب الإبطال أو الفشل", "Invalidation / Failure Reason", view["invalidation"]),
        ("تجميد البيانات", "Historical Cutoff", view["cutoff"]),
    ]
    _kv_table(details)
    with st.expander(
            "مستويات المحاكاة البحثية · Research simulation levels", expanded=False):
        st.caption(
            "محاكاة بحثية منفصلة عن مستويات التداول الإنتاجية · "
            "Research simulation only; separate from production trade levels."
        )
        simulation = st.columns(6)
        simulation_cells = (
            ("التفعيل البحثي", "Research Trigger", view["trigger"]),
            ("الوقف الهيكلي البحثي", "Research Structural Stop", view["stop"]),
            ("الهدف البحثي الأول", "Research Target 1", view["target_1"]),
            ("الهدف البحثي الثاني", "Research Target 2", view["target_2"]),
            ("المقاومة البحثية الرئيسية", "Research Major Resistance",
             view["major_resistance"]),
            ("ع/م تشخيصي", "Diagnostic R/R", view["risk_reward"]),
        )
        for column, cell in zip(simulation, simulation_cells):
            with column:
                metric_card(cell[0], cell[2], label_en=cell[1])
        _kv_table([
            ("ع/م للهدف الهيكلي", "Meaningful-target diagnostic R/R",
             view["risk_reward_meaningful"]),
            ("ع/م للهدف الأوسع", "Broader-target diagnostic R/R",
             view["risk_reward_broader"]),
            ("المقاومة الهيكلية", "Meaningful resistance",
             view["meaningful_resistance"]),
            ("المقاومة الأوسع", "Broader resistance", view["broader_resistance"]),
        ])
    with st.expander(
            "تفاصيل تشخيصية · Diagnostic details (internal codes)", expanded=False):
        st.caption(
            "رموز داخلية للمراجعة الفنية فقط · Internal codes for technical review "
            "only; the labels above are the user-facing meaning."
        )
        _kv_table([
            ("رمز سبب الإبطال", "Invalidation code", view["invalidation_code"]),
            ("رمز جودة الاتجاه", "Trend status code", view["trend_code"]),
            ("رمز علاقة المتوسطات", "EMA alignment code", view["ema_relation_code"]),
            ("رمز الهيكل السعري", "Structure code", view["structure_code"]),
        ])
    if view["missing"]:
        st.caption("قياسات غير موثوقة/غير متاحة · Missing or unreliable: "
                   + ", ".join(view["missing"]))


def _regenerate_narrative_only(bundle):
    """Re-run Layer 2 ONLY. No indicator, provider or full analysis is re-executed."""
    from core.ai_stock_analysis_service import regenerate_narrative

    return regenerate_narrative(bundle, force_refresh=True)


# Tone → the theme colour used for the summary strip and the source state. Status is always
# spelled out in words as well, so nothing is communicated by colour alone.
_TONE_COLORS = {
    "green": "var(--green)", "amber": "var(--amber)", "red": "var(--red)",
    "blue": "var(--blue)", "gray": "var(--text)",
}


def _narrative_source_html(narrative):
    """The one source block: who wrote it, on what model, and whether it validated."""
    block = narrative_source_block(narrative)
    color = _TONE_COLORS.get(block["state_tone"], "var(--text)")
    detail = (f'<span class="meta">{isolate_ltr(block["detail"])}</span>'
              if block["detail"] else "")
    return (
        '<div class="egx-narrative"><div class="egx-narr-source">'
        f'<span class="who">{isolate_ltr(block["title_ar"])}'
        f'<span class="en">{isolate_ltr(block["title_en"])}</span></span>{detail}'
        f'<span class="state" style="color:{color}">'
        f'<span class="dot" style="background:{color}" aria-hidden="true"></span>'
        f'{isolate_ltr(block["state_ar"])}'
        f'<span class="en">{isolate_ltr(block["state_en"])}</span></span>'
        '</div></div>')


def _narrative_summary_html(result):
    """Four separate cells — the crowded one-line headline is never rebuilt here."""
    cells = "".join(
        f'<div class="cell"><div class="k">{isolate_ltr(label_ar)}'
        f'<span class="en">{isolate_ltr(label_en)}</span></div>'
        f'<div class="v" style="color:{_TONE_COLORS.get(tone, "var(--text)")}">'
        f'{isolate_ltr(value)}</div></div>'
        for label_ar, label_en, value, tone in narrative_summary_cells(result))
    return f'<div class="egx-narrative"><div class="egx-narr-strip">{cells}</div></div>'


def _narrative_card_html(view):
    """One section card: header, then AI prose, then deterministic facts — never mixed."""
    english = (f'<span class="en">{isolate_ltr(view["title_en"])}</span>'
               if view["title_en"] else "")
    parts = [f'<h3>{isolate_ltr(view["title_ar"])}{english}</h3>',
             f'<div class="accent" style="background:{view["accent_color"]}" '
             'aria-hidden="true"></div>']
    if view["prose"]:
        parts.append(f'<p class="prose">{isolate_ltr(view["prose"])}</p>')
    if view["rows"]:
        # A short numeric value keeps the single-line tabular presentation; a
        # narrative-length value must wrap, or it becomes one unbreakable line that
        # overflows the card and collapses the label column.
        rows = "".join(
            f'<div class="row"><span class="lbl">{isolate_ltr(label)}</span>'
            f'<span class="val{" num" if _is_numeric_value(value) else ""}">'
            f'{isolate_ltr(value)}</span></div>'
            for label, value in view["rows"])
        parts.append(f'<div class="egx-narr-facts">{rows}</div>')
    if view["chips"]:
        chips = "".join(f'<span class="chip">{isolate_ltr(chip)}</span>'
                        for chip in view["chips"])
        parts.append(f'<div class="egx-narr-chips">{chips}</div>')
    return f'<article class="egx-narr-card {view["width"]}">{"".join(parts)}</article>'


def _narrative_section(narrative, result, bundle=None, regenerator=None):
    """Render the narrative as one card per section — presentation only.

    Nothing here generates, re-derives or re-formats a value: the model's prose and the
    application's fact lines arrive already composed, and this only lays them out. No
    provider, model or health probe is reached, so a plain rerender costs nothing.
    """
    st.markdown(NARRATIVE_CSS, unsafe_allow_html=True)
    title_column, action_column = st.columns([2.4, 1.6])
    with title_column:
        st.markdown(
            '<div class="egx-narrative"><h2 class="egx-narr-title">السرد التحليلي'
            '<span class="en">AI Narrative</span></h2></div>',
            unsafe_allow_html=True)
    with action_column:
        if bundle is not None:
            # Narrative-only regeneration. The evidence object is reused unchanged: no
            # indicator is recomputed, no provider is contacted, no analysis is re-run.
            st.markdown('<div style="height:.85rem"></div>', unsafe_allow_html=True)
            if st.button(
                    "♻ إعادة توليد الشرح بالذكاء الاصطناعي",
                    key="_ai_analysis_regen_narrative", width="stretch",
                    help="يعيد توليد الشرح فقط دون إعادة حساب المؤشرات أو استدعاء أي "
                         "مزود بيانات."):
                with st.spinner("جارٍ إعادة توليد الشرح… · Regenerating narrative only…"):
                    try:
                        refreshed = (regenerator or _regenerate_narrative_only)(bundle)
                    except Exception as error:
                        # The provider layer already degrades safely; this guards the call
                        # itself. Only the exception TYPE is shown — never a provider
                        # message.
                        st.warning("تعذر توليد شرح جديد؛ يبقى الشرح الحتمي معروضاً. · "
                                   f"Narrative regeneration failed: {type(error).__name__}")
                    else:
                        st.session_state[STATE_BUNDLE] = refreshed
                        narrative = refreshed.narrative

    st.markdown(_narrative_source_html(narrative), unsafe_allow_html=True)
    if narrative is None:
        empty_state("السرد غير متاح", "No narrative was produced for this analysis.")
        return
    st.markdown(_narrative_summary_html(result), unsafe_allow_html=True)

    cards = "".join(_narrative_card_html(view)
                    for view in narrative_section_views(narrative))
    st.markdown(f'<div class="egx-narrative"><div class="egx-narr-grid">{cards}</div></div>',
                unsafe_allow_html=True)
    st.markdown(f'<div class="egx-narrative"><p class="egx-narr-note">'
                f'{isolate_ltr(str(narrative.disclaimer))}</p></div>',
                unsafe_allow_html=True)

    # Everything below is audit material, not reading material: it stays collapsed.
    with st.expander("تفاصيل تقنية للسرد · Narrative technical details", expanded=False):
        st.markdown('<span class="egx-narr-tech"></span>', unsafe_allow_html=True)
        _kv_table(narrative_technical_rows(narrative))
        st.caption(f"derived from evidence {narrative.derived_from_evidence_version} · "
                   "numbers are renderings of evidence fields, never new facts.")
        st.caption("لا تُعرض المفاتيح ولا نص التعليمات ولا رسائل المزود الخام. · API keys, "
                   "prompt text and raw provider messages are never shown.")


def _confidence_section(result):
    confidence = result.confidence
    section_header("تفصيل الثقة", f"Confidence Breakdown — {confidence.method_version}")
    left, right = st.columns([1, 3])
    with left:
        metric_card("الثقة الإجمالية", f"{fmt_score(confidence.overall, 0)} / 100",
                    label_en="Overall Confidence", tone="blue")
    with right:
        for row in confidence_rows(confidence):
            _confidence_bar(row["label_ar"], row["label_en"], row["score"], row["value"],
                            row["supplied"])
    st.caption("تُعرض القيم كما وردت من محرك الثقة دون إعادة حساب؛ المكوّن غير المُورَّد يظهر كشرطة. · "
               "Values are shown exactly as supplied; a component the engine did not supply "
               "renders as an em dash.")


def _warnings_section(result):
    section_header("المخاطر وجودة البيانات", "Risk & Data-Quality Warnings")
    warnings = data_quality_warnings(result)
    if not warnings:
        st.success("لا توجد تحذيرات على جودة البيانات. · No data-quality warnings.")
    for severity, arabic, english in warnings:
        text = f"{arabic} · {english}" if arabic != english else str(arabic)
        {"error": st.error, "warning": st.warning}.get(severity, st.info)(text)

    with st.expander("التفاصيل الفنية والمصدر · Technical details & provenance"):
        _kv_table(provenance_rows(result))
        st.caption("ياهو ليس مزوداً حالياً ولا مصدر مقارنة؛ أي بذرة تاريخية مجمّدة تظهر هنا فقط "
                   "كتفصيل فني. · Yahoo is never a current provider or comparison source; a "
                   "frozen historical bootstrap seed appears here as a technical detail only.")


def _card_section(result, narrative):
    section_header("بطاقة التحليل",
                   "Export card — compact, infographic or extended infographic")
    presentation = build_presentation(result, narrative)
    left, right = st.columns([1.2, 1])
    with left:
        card_type = st.radio(
            "نوع البطاقة · Card type", CARD_TYPES,
            format_func=lambda key: CARD_TYPE_LABELS[key],
            key="_ai_analysis_card_kind")

        company = ""
        if card_type == COMPACT:
            resolution = st.radio(
                "الدقة · Resolution", list(CARD_SIZES),
                format_func=lambda key: CARD_SIZE_LABELS[key], horizontal=True,
                key="_ai_analysis_card_size",
                index=list(CARD_SIZES).index(DEFAULT_CARD_SIZE))
            language = "AR"
            company = st.text_input(
                "اسم الشركة بالعربية (اختياري) · Arabic company name (optional)",
                value="", key="_ai_analysis_company").strip()
        else:
            language = st.radio(
                "اللغة · Language", list(EXPORT_LANGUAGES),
                format_func=lambda key: EXPORT_LANGUAGE_LABELS[key],
                horizontal=True, key="_ai_analysis_card_language")
            # Only the resolutions this card type genuinely supports are offered.
            options = resolutions_for(card_type)
            resolution = st.radio(
                "الدقة · Resolution", list(options),
                format_func=lambda key: RESOLUTION_LABELS[key], horizontal=True,
                key=f"_ai_analysis_res_{card_type}")
            st.caption(f"{presentation.display_label} · "
                       f"جلسة {presentation.last_completed_session}")
        generate = st.button("🖼 إنشاء البطاقة · Generate Analysis Card",
                             key="_ai_analysis_card_go")

    request = ExportRequest(card_type=card_type, language=language,
                            resolution=resolution)
    # The key carries ticker, analysis identity, session, type, language and
    # canvas size, so changing ANY control invalidates the previous image and a
    # card from a previously selected stock can never be shown.
    cache_key = export_cache_key(presentation, request)
    if card_type == COMPACT:
        cache_key = f"{cache_key}|{resolution}|{company}"

    if generate and st.session_state.get(STATE_CARD_KEY) != cache_key:
        with st.spinner("جارٍ إنشاء البطاقة… · Rendering card…"):
            try:
                # Rendered ONCE. The same bytes feed the preview and the download.
                st.session_state[STATE_CARD] = render_export(
                    presentation, request,
                    compact_renderer=lambda: generate_card_bytes(
                        result, narrative, size=resolution,
                        company_name=company or None))
                st.session_state[STATE_CARD_KEY] = cache_key
            except ExportUnavailable as error:
                st.session_state[STATE_CARD] = None
                st.session_state[STATE_CARD_KEY] = None
                st.error(f"تعذر إنشاء البطاقة · Card export failed: {error}")

    card = st.session_state.get(STATE_CARD)
    # The image is shown ONLY when its key matches the current selection, so a
    # stale card is never displayed after a control changes.
    if card and st.session_state.get(STATE_CARD_KEY) == cache_key:
        with right:
            st.image(card, caption=f"{presentation.ticker} · "
                                   f"{CARD_TYPE_LABELS[card_type]}",
                     width="stretch")
            st.download_button(
                "⬇ تنزيل PNG · Download PNG", data=card,
                file_name=export_filename(presentation, request),
                mime="image/png", key="_ai_analysis_card_dl")
    elif not card:
        with right:
            empty_state("لم تُنشأ بطاقة بعد",
                        "Press Generate Analysis Card to render a PNG.", icon="🖼")


def _history_section(symbol):
    """History is loaded lazily — only when the user opens the expander."""
    section_header("سجل التحليلات", "Analysis History — loaded on demand")
    opened = st.checkbox("عرض السجل · Show history", value=False, key=STATE_HISTORY_OPEN)
    if not opened:
        st.caption("السجل لا يُحمّل إلا عند الطلب. · History is not read until you ask for it.")
        return
    try:
        from core.ai_stock_analysis_history import AnalysisHistoryStore
        records = AnalysisHistoryStore().records(symbol)
    except Exception as error:
        st.info(f"لا يمكن قراءة السجل الآن · History unavailable: {error}")
        return
    if not records:
        empty_state("لا يوجد سجل", f"No stored analyses for {symbol} yet.", icon="🗂")
        return
    import pandas as pd
    frame = pd.DataFrame([{
        "التاريخ / Created": record.created_at,
        "الرمز / Symbol": record.symbol,
        "اسم السهم / Company": company_name(record.symbol),
        "التوصية / Recommendation":
            RECOMMENDATION_LABELS.get(record.recommendation,
                                      (record.recommendation.value, "", ""))[0],
        "الثقة / Confidence": fmt_score(record.confidence_overall, 0),
        "حالة البيانات / Data": record.data_status.value,
        "الجلسة / Phase": record.market_phase.value,
        "النموذج / Model": record.narrative_model or EM_DASH,
        "مصدر الشرح / Narrative": getattr(record, "narrative_source", "") or EM_DASH,
        "التحقق / Validation": getattr(record, "narrative_validation_status", "") or EM_DASH,
        "الأدلة / Evidence": record.evidence_version,
    } for record in reversed(records)])
    st.dataframe(frame, width="stretch", hide_index=True)


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

def show_ai_stock_analysis(runner=None):
    """Render the AI Stock Analysis page. Nothing is analysed on initial load."""
    apply_global_style()
    _page_style()
    page_header(PAGE_TITLE_AR, f"{PAGE_TITLE_EN} · تحليل يدوي لسهم واحد عند الطلب — دعم قرار فقط",
                icon="🤖", badge="DECISION SUPPORT")
    st.markdown(" ".join(badge_html(f"{arabic} · {english}", tone)
                         for arabic, english, tone in SAFETY_BADGES),
                unsafe_allow_html=True)

    symbol, pressed = _symbol_selector()
    _discard_stale_analysis(symbol)

    if pressed and symbol:
        try:
            bundle = run_analysis(symbol, runner)
        except ValueError as error:
            st.error(f"رمز غير صالح · Invalid symbol: {error}")
            bundle = None
        if bundle is not None:
            st.session_state[STATE_BUNDLE] = bundle
            st.session_state[STATE_SYMBOL] = normalize_symbol(symbol)
            # A new analysis invalidates any previously rendered card.
            st.session_state[STATE_CARD] = None
            st.session_state[STATE_CARD_KEY] = None

    bundle = st.session_state.get(STATE_BUNDLE)
    if bundle is None:
        empty_state("لم يبدأ أي تحليل",
                    "اختر رمزاً واحداً ثم اضغط «تحليل السهم». لا يتم فحص السوق كاملاً ولا يُطلب أي "
                    "مزود بيانات قبل ذلك. · Pick one symbol and press Analyze Stock. No universe "
                    "scan and no provider call happens before that.", icon="🤖")
        return

    result, narrative = bundle.result, bundle.narrative
    recommendation_ar, recommendation_en, recommendation_tone = RECOMMENDATION_LABELS.get(
        result.recommendation, ("البيانات غير كافية", "Data Insufficient", "red"))

    recommendation_badge = badge_html(f"{recommendation_ar} · {recommendation_en}",
                                      recommendation_tone)
    st.markdown(f'### {html.escape(result.request.symbol)} &nbsp; {recommendation_badge}',
                unsafe_allow_html=True)
    if getattr(bundle, "source", "") == "fixture":
        st.caption("مصدر البيانات: تجهيزة اختبار محلية (لم يُستدعَ أي مزود). · Source: local test "
                   "fixture — no provider was contacted.")

    presentation = build_presentation(result, narrative)
    _status_section(result)
    render_provenance_panel(analysis_provenance(result))
    _price_section(result)
    _chart_section(result, presentation)
    _technical_section(result)
    _levels_section(result)
    _pullback_section(result)
    _scenario_section(result)
    _narrative_section(narrative, result, bundle=bundle)
    _confidence_section(result)
    _warnings_section(result)
    _card_section(result, narrative)
    _history_section(result.request.symbol)
