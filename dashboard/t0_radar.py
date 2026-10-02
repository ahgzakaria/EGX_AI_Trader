"""رادار T+0 · T+0 Radar — the names worth watching for a same-session trade tomorrow.

Read after the close, never during the session. **It decides nothing:**
`t0_radar/radar.py` gates, ranks and writes every reason; this formats. A
sentence on screen is the one the module wrote from the row's own numbers, so
the page cannot describe a name differently from the saved file.

Like Breakout Watch it does not rebuild on every rerun. The default view is the
newest saved list with both of its dates on screen, and a fresh run is a button
-- the same `t0_radar.service.run` the daily update calls, so a list built here
is recorded exactly as one built there.
"""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.ui import attrition_bar, empty_state, page_header, section_header
from t0_radar import forward, radar

VISIBLE = ("rank", "symbol", "name", "forecast_pct", "forecast_low_pct", "forecast_high_pct",
           "forecast_cost_multiple", "round_trip_pct", "recent_range_pct", "median_range_pct",
           "close", "turnover_today_egp", "relative_turnover", "t0_status")

LABELS = {
    "rank": "#", "symbol": "Symbol", "name": "Name",
    "forecast_pct": "Forecast range %", "forecast_low_pct": "Half the time ≥ %",
    "forecast_high_pct": "… and ≤ %", "forecast_cost_multiple": "× cost",
    "round_trip_pct": "Cost %", "recent_range_pct": "Last 5 sessions %",
    "median_range_pct": "Typical (20) %", "close": "Close",
    "turnover_today_egp": "Turnover (M)", "relative_turnover": "vs avg ×",
    "t0_status": "T+0",
}

_RTL = """
<style>
.t0-why { direction: rtl; text-align: right; margin: .2rem 0 1rem; }
.t0-why h4 { margin: 0 0 .25rem; font-size: .95rem; direction: ltr; text-align: right; }
.t0-why ul { margin: .1rem 1.1rem .2rem 0; padding: 0; }
.t0-why li { margin: .05rem 0; }
.t0-why .caution { color: var(--warning, #c98a1b); }
.t0-why .context, .t0-why .levels { color: var(--muted, #8a94a6); font-size: .86rem; }
</style>
"""


@st.cache_data(show_spinner=False)
def _fresh_run(_key: str):
    """One run, cached per click key so a rerun does not rebuild the list."""

    from t0_radar import service

    result, summary = service.run()
    return radar.as_frame(result), dict(result.funnel), summary


def _load_saved(path):
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception as error:                              # noqa: BLE001
        st.error(f"Could not read {path.name}: {type(error).__name__}")
        return None


def _t0_banner():
    from core.sector_context import intraday_eligibility_provenance

    provenance = intraday_eligibility_provenance()
    if provenance.get("available"):
        st.caption(f"T+0: {provenance.get('eligible_count', 0)} names marked eligible in "
                   f"{provenance.get('path')} — only those can appear on the list.")
        return
    st.caption("T+0 eligibility is not filtered here — check it yourself before a "
               "same-session trade. Filling data/universe/egx_intraday_eligibility.csv "
               "would make the radar show eligible names only.")


def _render_table(frame, top):
    view = frame.head(top)[[c for c in VISIBLE if c in frame.columns]].copy()
    if "turnover_today_egp" in view:
        view["turnover_today_egp"] = view["turnover_today_egp"] / 1e6
    st.dataframe(view.rename(columns=LABELS).round(2), hide_index=True, width="stretch")


def _levels(row):
    def number(key, digits=2):
        value = row.get(key)
        return "—" if pd.isna(value) else f"{value:,.{digits}f}"

    return (f"إغلاق {number('close')} · أعلى/أقل آخر جلسة {number('high_today')}/"
            f"{number('low_today')} · أعلى/أقل 20 جلسة {number('high_20')}/{number('low_20')}"
            f" · ATR {number('atr14')}")


def _render_reasons(frame, top):
    section_header("ليه مرشح · Why each name is on the list",
                   "Written by the radar from the row's own numbers: the forecast and what "
                   "it rests on. The last line is direction, shown and never used.")
    blocks = [_RTL]
    for _, row in frame.head(top).iterrows():
        name = f" — {html.escape(str(row['name']))}" if isinstance(row.get("name"), str) and row["name"] else ""
        items = "".join(f"<li>{html.escape(s)}</li>"
                        for s in radar.split_sentences(row.get("reasons")))
        items += "".join(f'<li class="caution">تنبيه: {html.escape(s)}</li>'
                         for s in radar.split_sentences(row.get("cautions")))
        blocks.append(
            f'<div class="t0-why"><h4>{int(row["rank"])}. {html.escape(str(row["symbol"]))}'
            f'{name} · forecast {row["forecast_pct"]:.1f}%</h4><ul>{items}</ul>'
            f'<div class="context">{html.escape(str(row.get("context") or ""))}</div>'
            f'<div class="levels">{html.escape(_levels(row))}</div></div>')
    st.markdown("".join(blocks), unsafe_allow_html=True)


def _render_funnel(funnel, candidates):
    refused = sum(int(funnel.get(gate, 0) or 0) for gate, _ in radar.GATES)
    section_header("قمع البوابات · Gate funnel",
                   f"What refused each of {refused + candidates:,} symbols first.")
    st.markdown(attrition_bar([(label, funnel.get(gate, 0)) for gate, label in radar.GATES],
                              survived_label="مرشح · candidate",
                              total=refused + candidates), unsafe_allow_html=True)


def _render_record(top):
    section_header("السجل الحي · Forward record",
                   "Each list graded on the session after it, for this rule version only. "
                   "Inside the band should be near half; direction is shown, not claimed.")
    report = forward.RadarStore().report(top=top)
    if not report.get("sessions_graded"):
        st.caption(f"{report.get('sessions_recorded', 0)} list(s) recorded under "
                   f"{report.get('rule_version')}, none graded yet — a list is graded once "
                   "the session after it has closed and been imported.")
        return

    def line(label, group):
        def value(key, percent=False):
            number = group.get(key)
            return None if number is None else round(number * 100 if percent else number, 2)

        return {"Group": label, "Picks": group.get("picks", 0),
                "Next range % (median)": value("range_median"),
                "Inside the band %": value("inside_band_share", True),
                "Actual / forecast (median)": value("actual_over_forecast_median"),
                "Moved ≥ 5× cost %": value("over_5x_cost_share", True),
                "Closed up %": value("closed_up_share", True),
                "Open → close % (mean)": value("open_to_close_mean")}

    st.dataframe(pd.DataFrame([line(f"Top {top}", report["top"]),
                               line("Rest of the list", report["rest"])]),
                 hide_index=True, width="stretch")
    st.caption(f"{report['sessions_graded']} session(s) graded of "
               f"{report['sessions_recorded']} recorded under {report['rule_version']}. "
               f"Too few sessions decide nothing.")


def show_t0_radar() -> None:
    page_header("رادار T+0 · T+0 Radar",
                "Built after the close: how far each liquid name is likely to move in the "
                "next session, and which names are testing a rising 200-day average.")
    radar_tab, sma_tab = st.tabs(["رادار T+0 · next-session range",
                                  "اختبار متوسط 200 يوم · 200-day average tests"])
    with radar_tab:
        _show_radar()
    with sma_tab:
        _show_sma200()


# --- the 200-day average tab ---------------------------------------------------

SMA_VISIBLE = ("symbol", "name", "close", "sma", "dist", "low_dist", "peak", "slope", "rsi",
               "turnover")
SMA_LABELS = {
    "symbol": "Symbol", "name": "Name", "close": "Close", "sma": "SMA200",
    "dist": "Close vs SMA200 %", "low_dist": "Low vs SMA200 %",
    "peak": "Was above, max %", "slope": "SMA200 20d slope %", "rsi": "RSI14",
    "turnover": "Turnover 20d (M)",
}


def _store_key():
    """Changes whenever the daily import rewrites the measured store."""

    from sector_flow import measured_turnover

    path = Path(measured_turnover.DEFAULT_DATABASE)
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"


@st.cache_data(show_spinner=False)
def _sma200_scan(_store_key: str):
    from t0_radar import sma200

    result = sma200.scan()
    return result.session_date, result.below_sma20_share, result.selloff, result.names


def _sma200_table(frame):
    view = frame[[c for c in SMA_VISIBLE if c in frame.columns]].copy()
    for column in ("dist", "low_dist", "peak", "slope"):
        view[column] = view[column] * 100
    view["turnover"] = view["turnover"] / 1e6
    st.dataframe(view.rename(columns=SMA_LABELS).round(2), hide_index=True, width="stretch")


def _show_sma200():
    from t0_radar import sma200

    with st.spinner("Reading the record…"):
        session, below_share, selloff, names = _sma200_scan(_store_key())
    evidence = sma200.EVIDENCE
    good, bad, normal = (evidence["selloff_on_or_above"], evidence["selloff_below"],
                         evidence["normal_on_or_above"])
    st.info(
        "الاختبار لوحده قرعة: على 2014–2026 السهم اللي نزل لمتوسط 200 يوم سبق السهم العادي "
        f"بعد 20 جلسة في {normal['beat']} من {normal['episodes']} موجة في السوق العادي. "
        "الفرق بييجي من حاجتين مع بعض: هبوط عام (75% أو أكتر من الأسهم تحت متوسط 20 يوم)، "
        "وإن السهم يقفل على المتوسط أو فوقه. في الحالة دي سبق السوق في "
        f"{good['beat']} من {good['episodes']} موجة بفارق {good['lift_median']:+.1f}% وارتد "
        f"{good['bounce']}% من المرات. ولو قفل تحته: {bad['beat']} من {bad['episodes']} موجة، "
        f"{bad['lift_median']:+.1f}%، وكسر {bad['break']}% من المرات. "
        "ده دليل على 20 جلسة، مش على جلسة واحدة، ومش توصية.")

    if session is None:
        empty_state("No record", "The measured store could not be read.", icon="⌕")
        return

    header = st.columns(3)
    header[0].metric("Data closed on", session)
    header[1].metric("Liquid names below SMA20",
                     "unknown" if below_share is None else f"{below_share:.0%}")
    header[2].metric("Broad sell-off", "yes" if selloff else "no",
                     help=f"At least {sma200.SELLOFF_SHARE:.0%} of liquid names below their "
                          "own 20-day average. The evidence for a test that closes on or "
                          "above the average holds only in a broad sell-off.")
    if not selloff:
        st.caption("Not a broad sell-off today: on the record, a test in a normal market is "
                   "a coin flip whichever side it closed on.")

    parts = {key: (names[names["state"] == key] if len(names) else names)
             for key in (sma200.ON_OR_ABOVE, sma200.BELOW, sma200.APPROACHING, sma200.BROKE)}

    section_header("قفلت على المتوسط أو فوقه · Closed on or above the average",
                   f"{len(parts[sma200.ON_OR_ABOVE])} names. The side the record favours, "
                   "in a broad sell-off.")
    if len(parts[sma200.ON_OR_ABOVE]):
        _sma200_table(parts[sma200.ON_OR_ABOVE])
    else:
        st.caption("None on this session.")

    section_header("قفلت تحت المتوسط · Closed below the average",
                   f"{len(parts[sma200.BELOW])} names. On the record these broke first more "
                   "often than they bounced.")
    if len(parts[sma200.BELOW]):
        _sma200_table(parts[sma200.BELOW])
    else:
        st.caption("None on this session.")

    with st.expander(f"قريبة من المتوسط · Approaching, 2–6% above "
                     f"({len(parts[sma200.APPROACHING])})"):
        if len(parts[sma200.APPROACHING]):
            _sma200_table(parts[sma200.APPROACHING])
        else:
            st.caption("None on this session.")
    with st.expander(f"كسرته · Already through it, 3–10% below "
                     f"({len(parts[sma200.BROKE])})"):
        if len(parts[sma200.BROKE]):
            _sma200_table(parts[sma200.BROKE])
        else:
            st.caption("None on this session.")

    st.caption(
        f"A test: the 200-day average is rising over {sma200.SLOPE_SESSIONS} sessions, the "
        f"name closed at least {sma200.CAME_FROM_ABOVE:.0%} above it within the last "
        f"{sma200.CAME_FROM} sessions, the low reached within {sma200.ZONE_ABOVE:.0%} above "
        f"it and the close is no more than {sma200.ZONE_BELOW:.0%} below it, turnover is at "
        f"least {sma200.LIQUIDITY_EGP / 1e6:g}M EGP a session, and no move past the ±20% "
        "limit in 200 sessions. Evidence: docs/audits/strategies/SMA200_PULLBACK.md.")
    if len(names):
        st.download_button("Download the 200-day list as CSV",
                           data=names.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"sma200_tests_{session}.csv", mime="text/csv")


# --- the radar tab -------------------------------------------------------------


def _show_radar():
    st.info(radar.HEADER_NOTE)
    _t0_banner()

    saved = radar.available_csvs()
    controls = st.columns([3, 1])
    with controls[0]:
        if saved:
            labels = {p.name: "{}  ·  run {}  ·  session {}".format(
                p.name, *(d or "unknown" for d in radar.dates_from_name(p))) for p in saved}
            chosen = st.selectbox("Saved list", [p.name for p in saved],
                                  format_func=lambda n: labels.get(n, n))
            selected = next(p for p in saved if p.name == chosen)
        else:
            selected = None
            st.caption("No saved list yet — build one below.")
    with controls[1]:
        top = int(st.number_input("Show", min_value=5, max_value=40,
                                  value=forward.TOP, step=5))

    frame, funnel, session, file_name = None, {}, None, "t0_radar.csv"
    if st.button("Build tomorrow's radar", type="primary"):
        st.session_state["_t0_radar_key"] = str(pd.Timestamp.now())
        with st.spinner("Reading the record and grading earlier lists…"):
            frame, funnel, summary = _fresh_run(st.session_state["_t0_radar_key"])
        session = summary.get("session_date")
        file_name = Path(summary.get("csv") or file_name).name
        st.success(f"Saved to {summary.get('csv')} · recorded {summary.get('recorded')} · "
                   f"graded {summary.get('graded')} earlier pick(s)")
    elif selected is not None:
        frame = _load_saved(selected)
        run_date, session = radar.dates_from_name(selected)
        funnel = forward.RadarStore().run_info(session).get("funnel", {}) if session else {}
        file_name = selected.name
        st.caption(f"Showing a saved list — built {run_date or 'unknown'} from the session "
                   f"that closed {session or 'unknown'}.")

    if frame is None:
        empty_state("No radar yet", "Press 'Build tomorrow's radar', or run "
                    "scripts/run_t0_radar.py after the daily update.", icon="⌕")
        return

    header = st.columns(3)
    header[0].metric("Candidates", len(frame))
    header[1].metric("Data closed on", session or "unknown")
    cost_through = None
    if "cost_measured_through" in frame.columns and frame["cost_measured_through"].notna().any():
        cost_through = str(frame["cost_measured_through"].dropna().iloc[0])
    elif session:
        cost_through = forward.RadarStore().run_info(session).get("cost_measured_through")
    header[2].metric("Cost measured through", cost_through or "unknown",
                     help="Each name's round trip is the published commission plus its "
                          "own crossing cost, measured from trades up to this date.")

    if not len(frame):
        st.info("Nothing cleared the gates for this session. The funnel says what refused "
                "each name.")
    else:
        section_header("المرشحون · Candidates",
                       "Ranked by the forecast size of the next session's range. Not a "
                       "ranking of which way anything will move.")
        _render_table(frame, top)
        _render_reasons(frame, top)
    if funnel:
        _render_funnel(funnel, len(frame))
    _render_record(top)

    st.download_button("Download this list as CSV",
                       data=frame.to_csv(index=False).encode("utf-8-sig"),
                       file_name=file_name, mime="text/csv")
