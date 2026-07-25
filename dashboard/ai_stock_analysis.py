"""AI Stock Analysis — manual, one-symbol, on-demand analysis page (UI only).

تحليل سهم بالذكاء الاصطناعي · AI Stock Analysis

The page renders ALREADY-COMPUTED typed evidence. It performs no calculation of its own:
no provider is contacted, no indicator is computed, no universe is scanned, and nothing at
all is analysed until the user picks one symbol and presses *Analyze Stock*. All numbers
come from dedicated typed fields on the contract objects — never from narrative prose and
never from a machine recommendation reason.

Until the Core service is wired in, the analysis runner is a fixture-backed stand-in
(:func:`dashboard.ai_stock_analysis_components.fixture_analysis`); swapping it for
``core.ai_stock_analysis_service.analyze_symbol`` is a one-line change because both return
the same response shape.
"""

from __future__ import annotations

import html

import streamlit as st

from core.analysis_card_generator import CARD_SIZES, DEFAULT_CARD_SIZE
from dashboard.ai_stock_analysis_components import (
    CARD_SIZE_LABELS,
    EM_DASH,
    RECOMMENDATION_LABELS,
    SAFETY_BADGES,
    VOLUME_UNAVAILABLE_LABELS,
    card_cache_key,
    confidence_rows,
    data_mode,
    data_quality_warnings,
    fixture_analysis,
    fmt_score,
    generate_card_bytes,
    include_live_in_session_range,
    indicator_groups,
    is_auction,
    key_level_rows,
    market_phase_labels,
    momentum_reading,
    narrative_source,
    normalize_symbol,
    price_summary_rows,
    provenance_rows,
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
    status_bar,
)

# Session-state keys. Analysis and the rendered card both live here so a Streamlit rerun
# never re-runs an analysis and never re-renders a card that has not changed.
STATE_BUNDLE = "_ai_analysis_bundle"
STATE_SYMBOL = "_ai_analysis_symbol"
STATE_CARD = "_ai_analysis_card"
STATE_CARD_KEY = "_ai_analysis_card_key"
STATE_HISTORY_OPEN = "_ai_analysis_history_open"

# A short, static picker list. This is a convenience list of well-known EGX tickers for the
# selector only — the page never iterates it, scans it, or analyses more than the single
# symbol the user chose.
SUGGESTED_SYMBOLS = ("COMI", "HRHO", "TMGH", "SWDY", "EFIH", "ETEL", "ABUK", "ESRS",
                     "MFPC", "ORWE", "JUFO", "EAST")

PAGE_TITLE_AR = "تحليل سهم بالذكاء الاصطناعي"
PAGE_TITLE_EN = "AI Stock Analysis"


# --------------------------------------------------------------------------- #
# Analysis runner
# --------------------------------------------------------------------------- #

def _default_runner(symbol: str):
    """Fixture-backed stand-in for the Core service. One symbol, no I/O beyond a local file."""
    return fixture_analysis(symbol)


def run_analysis(symbol, runner=None):
    """Analyze exactly ONE symbol. Raises on a collection — there is no batch path here."""
    ticker = normalize_symbol(symbol)
    return (runner or _default_runner)(ticker)


# --------------------------------------------------------------------------- #
# Small render helpers
# --------------------------------------------------------------------------- #

def _kv_table(rows):
    """A compact right-to-left label/value table."""
    body = "".join(
        f'<tr><td class="k">{html.escape(str(label_ar))}'
        f'<span class="en">{html.escape(str(label_en))}</span></td>'
        f'<td class="v">{html.escape(str(value))}</td></tr>'
        for label_ar, label_en, value in rows)
    st.markdown(f'<table class="egx-kv">{body}</table>', unsafe_allow_html=True)


def _page_style():
    """Page-scoped additions to the shared dark terminal theme."""
    st.markdown(
        """
        <style>
        .egx-kv { width:100%; border-collapse:collapse; direction:rtl; }
        .egx-kv td { padding:.34rem .55rem; border-bottom:1px solid var(--border); font-size:.86rem; }
        .egx-kv tr:last-child td { border-bottom:0; }
        .egx-kv td.k { color:var(--muted); white-space:nowrap; }
        .egx-kv td.k .en { display:block; font-size:.62rem; letter-spacing:.03em;
            text-transform:uppercase; opacity:.72; direction:ltr; text-align:right; }
        .egx-kv td.v { color:var(--text); font-weight:700; text-align:left;
            direction:ltr; font-variant-numeric:tabular-nums; }
        .egx-panel { background:var(--surface); border:1px solid var(--border);
            border-radius:12px; padding:.7rem .85rem; height:100%; }
        .egx-panel h4 { margin:0 0 .45rem; font-size:.86rem; direction:rtl; }
        .egx-panel h4 span { display:block; font-size:.62rem; color:var(--muted);
            text-transform:uppercase; letter-spacing:.04em; direction:ltr; text-align:right; }
        .egx-scenario { background:var(--surface); border:1px solid var(--border);
            border-left:3px solid var(--blue); border-radius:12px; padding:.8rem .95rem; }
        .egx-scenario .t { font-weight:800; font-size:.98rem; direction:rtl; }
        .egx-narr { background:var(--surface-2); border:1px solid var(--border);
            border-right:3px solid var(--amber); border-radius:12px; padding:.85rem 1rem;
            direction:rtl; }
        .egx-narr h4 { margin:0 0 .4rem; font-size:1.02rem; }
        .egx-narr p { margin:.25rem 0; color:var(--muted); font-size:.88rem; line-height:1.75; }
        .egx-conf { display:flex; align-items:center; gap:.6rem; margin:.28rem 0; }
        .egx-conf .lbl { min-width:150px; font-size:.8rem; direction:rtl; }
        .egx-conf .bar { flex:1; height:8px; border-radius:999px; background:var(--surface-2);
            border:1px solid var(--border); overflow:hidden; }
        .egx-conf .bar i { display:block; height:100%; background:linear-gradient(90deg,#2563eb,#34d399); }
        .egx-conf .num { min-width:52px; text-align:left; font-weight:750; font-size:.82rem;
            font-variant-numeric:tabular-nums; }
        .egx-req { direction:rtl; font-size:.83rem; color:var(--muted); margin:.12rem 0; }
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
    """Symbol input. Returns (symbol, analyze_pressed). Nothing runs without the button."""
    section_header("اختيار السهم", "Symbol Selection — one symbol per analysis")
    left, middle, right = st.columns([2, 2, 1.2])
    with left:
        picked = st.selectbox("اختر رمزاً · Select symbol", SUGGESTED_SYMBOLS, index=0,
                              key="_ai_analysis_pick")
    with middle:
        typed = st.text_input("أو اكتب الرمز · Or type a symbol", value="",
                              placeholder="COMI", key="_ai_analysis_typed").strip()
    with right:
        st.markdown('<div style="height:1.75rem"></div>', unsafe_allow_html=True)
        pressed = st.button("▶ تحليل السهم · Analyze Stock", type="primary",
                            use_container_width=True, key="_ai_analysis_go")
    return (typed or picked), pressed


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
    ])
    if is_auction(result.market_phase):
        st.info("مزاد الإغلاق (14:15–14:25): تُعرض بيانات المزاد منفصلة ولا تُدمج مع نطاق الجلسة "
                "المستمرة. · Closing auction — auction data is shown separately and is never "
                "merged into the continuous-session range.")


def _price_section(result):
    section_header("ملخص السعر", "Price Summary — typed evidence fields")
    rows = price_summary_rows(result)
    headline = rows[0]
    session_rows = [row for row in rows[1:] if row[4] == "session"]
    live_rows = [row for row in rows[1:] if row[4] == "live"]

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


def _chart_section(result):
    """An interactive chart built ONLY from supplied typed values."""
    section_header("الرسم البياني", "Interactive Chart — plotted from supplied values only")
    try:
        import plotly.graph_objects as go
    except Exception:
        st.caption("Plotly is unavailable in this environment; the chart is skipped.")
        return

    price = result.price
    if price.open is None or price.high is None or price.low is None or price.close is None:
        empty_state("لا توجد بيانات كافية للرسم", "No OHLC evidence supplied for this symbol.")
        return

    session = result.data_quality.latest_completed_session or "session"
    figure = go.Figure()
    figure.add_trace(go.Candlestick(
        x=[session], open=[price.open], high=[price.high], low=[price.low],
        close=[price.close], name="Session OHLC",
        increasing_line_color="#34d399", decreasing_line_color="#f87171"))

    lines = [(row["label_en"], row) for row in key_level_rows(result) if row["present"]]
    palette = {"Support 1": "#34d399", "Support 2": "#10b981", "Resistance 1": "#fbbf24",
               "Resistance 2": "#f59e0b", "Breakout": "#60a5fa", "Invalidation": "#f87171"}
    drawn = []
    for label, row in lines:
        try:
            value = float(str(row["value"]).replace(",", ""))
        except ValueError:
            continue
        # Two levels may share a price (a resistance that is also the breakout trigger);
        # nudge the label so the annotations stay readable instead of stacking.
        shift = 14 * sum(1 for seen in drawn if abs(seen - value) < 1e-9)
        drawn.append(value)
        figure.add_hline(y=value, line_dash="dot", line_color=palette.get(label, "#94a3b8"),
                         annotation_text=f"{label} {row['value']}",
                         annotation_position="right", annotation_yshift=shift,
                         annotation_font_color=palette.get(label, "#94a3b8"))

    indicators = result.indicators
    for label, value, colour in (("SMA 20", indicators.sma_20, "#60a5fa"),
                                 ("SMA 50", indicators.sma_50, "#818cf8"),
                                 ("EMA 20", indicators.ema_20, "#a78bfa")):
        if value is not None:
            figure.add_hline(y=value, line_dash="dash", line_color=colour, opacity=.55,
                             annotation_text=label, annotation_position="left",
                             annotation_font_color=colour)

    if price.last is not None and include_live_in_session_range(result.market_phase):
        figure.add_trace(go.Scatter(x=[session], y=[price.last], mode="markers",
                                    name="Last (live)",
                                    marker=dict(size=12, color="#e6edf7",
                                                line=dict(width=2, color="#0b1220"))))

    figure.update_layout(
        template="plotly_dark", height=430, margin=dict(l=104, r=150, t=24, b=34),
        paper_bgcolor="#0b1220", plot_bgcolor="#0e1729",
        # Categorical: one completed session, not a time series — a date axis would
        # invent sub-second ticks around a single point.
        xaxis=dict(type="category", showgrid=False),
        yaxis=dict(gridcolor="#223049", title=dict(text=price.currency, standoff=26)),
        xaxis_rangeslider_visible=False, showlegend=True,
        legend=dict(orientation="h", y=1.12, bgcolor="rgba(0,0,0,0)"))
    st.plotly_chart(figure, use_container_width=True)
    st.caption("كل القيم معروضة كما وردت من محرك الأدلة — لا يحسب هذا الرسم أي مؤشر. · Every value "
               "is plotted exactly as supplied; the chart computes nothing.")


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

    columns = st.columns(len(groups))
    for column, (title_ar, title_en, rows) in zip(columns, groups):
        with column:
            st.markdown(f'<div class="egx-panel"><h4>{html.escape(title_ar)}'
                        f'<span>{html.escape(title_en)}</span></h4>', unsafe_allow_html=True)
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
        "المسافة / Distance": row["distance"],
    } for row in rows])
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.caption("الإطار الزمني وعدد اللمسات غير مضمّنة في العقد الحالي وتظهر كشرطة. · Timeframe and "
               "touch count are not part of the current contract and render as an em dash.")


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


def _narrative_section(narrative):
    source_ar, source_en, tone = narrative_source(narrative)
    section_header("السرد التحليلي", f"AI Narrative — source: {source_en}")
    st.markdown(badge_html(f"{source_ar} · {source_en}", tone), unsafe_allow_html=True)
    if narrative is None:
        empty_state("السرد غير متاح", "No narrative was produced for this analysis.")
        return
    st.markdown(
        f'<div class="egx-narr"><h4>{html.escape(str(narrative.headline))}</h4>'
        f'<p>{html.escape(str(narrative.summary))}</p>'
        f'<p>{html.escape(str(narrative.rationale))}</p>'
        f'<p><b>المخاطر · Risks:</b> {html.escape(str(narrative.risks))}</p>'
        f'<p style="opacity:.7">{html.escape(str(narrative.disclaimer))}</p></div>',
        unsafe_allow_html=True)
    st.caption(f"model: {narrative.model} · derived from evidence "
               f"{narrative.derived_from_evidence_version} · numbers are renderings of "
               f"evidence fields, never new facts.")


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
    section_header("بطاقة التحليل", "Generate Analysis Card — original Arabic PNG")
    left, right = st.columns([1.2, 1])
    with left:
        size = st.radio("مقاس البطاقة · Card size", list(CARD_SIZES),
                        format_func=lambda key: CARD_SIZE_LABELS[key],
                        horizontal=True, key="_ai_analysis_card_size",
                        index=list(CARD_SIZES).index(DEFAULT_CARD_SIZE))
        company = st.text_input("اسم الشركة (اختياري) · Company name (optional)", value="",
                                key="_ai_analysis_company").strip()
        generate = st.button("🖼 إنشاء البطاقة · Generate Analysis Card",
                             key="_ai_analysis_card_go")

    cache_key = card_cache_key(result, size) + f"|{company}"
    if generate and st.session_state.get(STATE_CARD_KEY) != cache_key:
        # Rendering is deliberately gated on the button AND on the cache key, so a rerun
        # (a widget change elsewhere on the page) never re-renders the same card.
        with st.spinner("جارٍ إنشاء البطاقة… · Rendering card…"):
            try:
                st.session_state[STATE_CARD] = generate_card_bytes(
                    result, narrative, size=size, company_name=company or None)
                st.session_state[STATE_CARD_KEY] = cache_key
            except Exception as error:
                st.session_state[STATE_CARD] = None
                st.session_state[STATE_CARD_KEY] = None
                st.error(f"تعذر إنشاء البطاقة · Card generation failed: {error}")

    card = st.session_state.get(STATE_CARD)
    if card and st.session_state.get(STATE_CARD_KEY) == cache_key:
        with right:
            st.image(card, caption=f"{result.request.symbol} · {CARD_SIZE_LABELS[size]}",
                     use_container_width=True)
            st.download_button(
                "⬇ تنزيل PNG · Download PNG", data=card,
                file_name=f"{result.request.symbol}_ai_analysis_{size.lower()}.png",
                mime="image/png", key="_ai_analysis_card_dl")
    elif not card:
        with right:
            empty_state("لم تُنشأ بطاقة بعد", "Press Generate Analysis Card to render a PNG.",
                        icon="🖼")


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
        "التوصية / Recommendation":
            RECOMMENDATION_LABELS.get(record.recommendation,
                                      (record.recommendation.value, "", ""))[0],
        "الثقة / Confidence": fmt_score(record.confidence_overall, 0),
        "حالة البيانات / Data": record.data_status.value,
        "الجلسة / Phase": record.market_phase.value,
        "النموذج / Model": record.narrative_model or EM_DASH,
        "الأدلة / Evidence": record.evidence_version,
    } for record in reversed(records)])
    st.dataframe(frame, use_container_width=True, hide_index=True)


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

    if pressed:
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

    _status_section(result)
    _price_section(result)
    _chart_section(result)
    _technical_section(result)
    _levels_section(result)
    _scenario_section(result)
    _narrative_section(narrative)
    _confidence_section(result)
    _warnings_section(result)
    _card_section(result, narrative)
    _history_section(result.request.symbol)
