"""Compact tabbed stock details with honest frozen/live snapshot provenance.

Target-state and promoted-target figures are display-only.  The Classic
decision trace, entry, stop, targets and original R/R are never recalculated.
"""

import pandas as pd
import streamlit as st

from core.level_status import (
    BASIS_COMPLETED_CLOSE,
    BASIS_FROZEN_CLOSE,
    BASIS_LIVE,
    LONG,
    SHORT,
    STALE_LEVEL,
    ALL_TARGETS_DONE_EN,
    assess_levels,
    reached_headline,
    select_comparison_price,
    state_label,
)
from core.watchlist import Watchlist
from dashboard.provenance_panel import (
    FROZEN,
    MIXED,
    Provenance,
    render_provenance_panel,
)
from dashboard.formatting import company_name, symbol_option_label
from dashboard.ui import section_header
from portfolio.sizing import PositionSizer


def show_stock_details(stock):
    watchlist = Watchlist()
    symbols = watchlist.load()
    title_col, action_col, count_col = st.columns([2, 1, 1])
    title_col.subheader(
        f"{symbol_option_label(stock['Ticker'])} · "
        f"{stock['Rating']} · {stock['Signal']}"
    )
    title_col.caption(company_name(stock["Ticker"]))
    if stock["Ticker"] in symbols:
        if action_col.button("✕ Remove", use_container_width=True):
            watchlist.remove(stock["Ticker"])
            st.rerun()
    else:
        if action_col.button("☆ Add to Watchlist", use_container_width=True):
            watchlist.add(stock["Ticker"])
            st.rerun()
    count_col.metric("Watchlist", len(symbols))

    overview, decision, sizing_tab, chart_tab = st.tabs([
        "Overview", "Decision Trace", "Position Sizing", "Chart & Indicators"
    ])
    context = stock_level_context(stock)
    # Remember only observed display prices for the same immutable evidence
    # hash.  This makes PREVIOUSLY_REACHED truthful across Streamlit reruns
    # without writing to trading state or changing the frozen decision.
    if (
        context["comparison"].status == BASIS_LIVE
        and not context["legacy"]
    ):
        memory = st.session_state.setdefault("_stock_target_observations", {})
        key = str(context["provenance"].evidence_hash)
        observed = dict(memory.get(key, {}))
        if (
            observed.get("high") is None
            or context["live_price"] > observed["high"]
        ):
            observed["high"] = context["live_price"]
            observed["high_timestamp"] = context["live_timestamp"]
        if (
            observed.get("low") is None
            or context["live_price"] < observed["low"]
        ):
            observed["low"] = context["live_price"]
            observed["low_timestamp"] = context["live_timestamp"]
        memory[key] = observed
        context = stock_level_context(
            stock,
            observed_high=observed["high"],
            observed_low=observed["low"],
            observed_high_timestamp=observed.get("high_timestamp"),
            observed_low_timestamp=observed.get("low_timestamp"),
            observed_high_verified=True,
            observed_low_verified=True,
        )
    with overview:
        _overview(stock, context)
    with decision:
        _decision_trace(stock)
    with sizing_tab:
        _position_sizing(stock)
    with chart_tab:
        _chart_and_indicators(stock)


def _overview(stock, context=None):
    context = context or stock_level_context(stock)
    assessment = context["assessment"]
    ai_probability = stock.get("AIProbability")
    ai_label = f"{ai_probability}%" if ai_probability is not None else "Not evaluated"
    metrics = st.columns(3)
    metrics[0].metric("Signal", stock["Signal"])
    metrics[1].metric("Confidence", f"{stock['Confidence']}%")
    metrics[2].metric("Strategy Score", stock["Score"])
    metrics = st.columns(3)
    metrics[0].metric("AI Advisory", ai_label)
    metrics[1].metric("AI Level", stock["AILevel"])
    metrics[2].metric("Frozen Original Risk / Reward", f"{stock['RR']:.2f}")

    section_header("Snapshot & Price", "Frozen Classic evidence is never relabelled as live")
    snapshot = st.columns(6)
    snapshot[0].metric("Frozen Signal Snapshot Price", _price(context["frozen_price"]))
    snapshot[1].metric("Rubix Overlay Price", _price(context["live_price"]))
    snapshot[2].metric(
        "Selected Comparison Price",
        _price(context["comparison"].value),
        delta=context["comparison"].status,
        delta_color="off",
    )
    snapshot[3].metric("Signal Created", _short_timestamp(context["signal_timestamp"]))
    snapshot[4].metric("Last Completed Session", context["completed_session"] or "—")
    snapshot[5].metric("Levels Version", context["levels_version"])

    if context["uses_live_price"]:
        st.warning(
            "Live/frozen timestamp warning: the current Rubix price is being compared "
            "only when its timestamp is compatible; the levels were not recalculated. "
            f"Rubix overlay: {_price(context['live_price'])} @ "
            f"{context['live_timestamp'] or '—'} · Frozen calculation: "
            f"{context['signal_timestamp'] or '—'} · Last completed session: "
            f"{context['level_timestamp'] or '—'} · Selected: "
            f"{context['comparison'].status} ({context['comparison'].reason})"
        )
    live_assessment = context.get("live_assessment")
    if live_assessment is not None and live_assessment.stale:
        st.error(
            "STALE_LEVEL — the live price and frozen levels cannot be compared safely: "
            f"{live_assessment.staleness.reason}. Both timestamps are shown above. "
            "Target cards therefore show their frozen-snapshot state, not a live verdict."
        )

    section_header("Price Levels", "Frozen values plus display-only target state")
    levels = st.columns(3)
    levels[0].metric("Frozen Buy Range", f"{stock['BuyLow']} – {stock['BuyHigh']}")
    levels[1].metric("Frozen Stop Loss", stock["StopLoss"])
    levels[2].metric(
        "Comparison Price Basis",
        f"{context['price_basis']} · {context['comparison'].provider}",
    )

    target_columns = st.columns(max(1, len(assessment.targets)))
    for column, target_status in zip(target_columns, assessment.targets):
        ar, en, _tone = state_label(target_status.state)
        suffix = " · Next Active" if target_status.is_next_active else ""
        column.metric(
            f"{target_status.label}{suffix}",
            _price(target_status.value),
            delta=f"{en} · {ar}",
            delta_color="off",
        )

    reached = [status for status in assessment.targets if status.reached]
    if reached:
        st.success(" · ".join(reached_headline(status) for status in reached))
    if assessment.next_active is not None:
        promoted = st.columns(3)
        promoted[0].metric(
            "Next Active Target",
            f"{assessment.next_active.label} · {_price(assessment.next_active.value)}",
        )
        promoted[1].metric(
            "Remaining Room to Next Target",
            _percent(assessment.remaining_room_next),
        )
        promoted[2].metric(
            "Remaining R/R to Next Target",
            _ratio(assessment.risk_reward_next),
        )
        st.caption(
            "Promotion is presentation-only. Remaining R/R uses the selected full-precision "
            "comparison price and the original frozen stop against the next configured "
            "target; the original decision trace is unchanged."
        )
    elif assessment.all_reached_or_stale:
        st.error(
            f"{ALL_TARGETS_DONE_EN}. Active-entry language is suppressed; run a new "
            "analysis before presenting another target."
        )

    support = st.columns(2)
    support[0].metric("Support", stock["Support"])
    support[1].metric("Resistance", stock["Resistance"])

    render_provenance_panel(context["provenance"])

    section_header("Decision Summary", "AI is advisory and never overrides Strategy Only")
    if assessment.all_reached_or_stale:
        st.warning(
            f"Frozen strategy decision: {stock['Signal']}. No active future target is "
            "being presented from this snapshot; a new analysis is required."
        )
    elif stock["Signal"] == "BUY":
        st.success(
            f"Strategy BUY · Rating {stock['Rating']} · AI advisory {ai_label} · "
            f"next-target display R/R {_ratio(assessment.risk_reward_next)}"
        )
    elif stock["Signal"] == "WATCH":
        st.warning("Promising setup, but the frozen strategy requires more confirmation.")
    else:
        st.error("The current setup does not satisfy the frozen strategy entry rules.")

    section_header("Key Reasons", "Decision-engine evidence")
    reasons = [item.strip() for item in str(stock["Reasons"]).split("|") if item.strip()]
    if reasons:
        st.write(" · ".join(f"`{reason}`" for reason in reasons))


def _decision_trace(stock):
    regime = stock.get("Regime", "N/A")
    index_regime = stock.get("IndexRegime", "N/A")
    regime_labels = {
        "BULL": "🟢 BULL", "SIDEWAYS": "🟡 SIDEWAYS", "BEAR": "🔴 BEAR"
    }
    columns = st.columns(2)
    columns[0].metric("EGX30 Regime", regime_labels.get(index_regime, index_regime))
    columns[1].metric("Stock Regime", regime_labels.get(regime, regime))

    trace = stock.get("DecisionTrace", {})
    section_header("Frozen Decision Values", "Original values retained exactly")
    st.dataframe(
        pd.DataFrame([{
            "Entry": stock.get("BuyHigh"),
            "Stop": stock.get("StopLoss"),
            "Target 1": stock.get("Target1"),
            "Target 2": stock.get("Target2"),
            "Original R/R": stock.get("RR"),
        }]),
        hide_index=True,
        use_container_width=True,
    )
    section_header("Gate Results", "The exact unified decision path")
    if trace:
        trace_frame = pd.DataFrame([
            {"Gate": gate, "Result": result} for gate, result in trace.items()
        ])

        def color_result(value):
            text = str(value)
            if "PASS" in text:
                return "color:#059669;font-weight:700"
            if "FAIL" in text or "LOW" in text:
                return "color:#dc2626;font-weight:700"
            return "color:#64748b"

        styled = trace_frame.style
        try:
            styled = styled.map(color_result, subset=["Result"])
        except AttributeError:
            styled = styled.applymap(color_result, subset=["Result"])
        st.dataframe(styled, hide_index=True, use_container_width=True)

    breakdown = stock.get("ConfidenceBreakdown", {})
    if breakdown:
        section_header("Confidence Breakdown", "Contribution by decision component")
        st.bar_chart(pd.Series(breakdown, name="Confidence"), color="#2563eb")

    section_header("Strategy Module Scores", "Raw module contribution")
    scores = pd.DataFrame({
        "Module": ["Trend", "Volume", "Momentum", "Candles", "Breakout"],
        "Score": [
            stock["Trend"], stock["Volume"], stock["Momentum"],
            stock["Candles"], stock["Breakout"],
        ],
    })
    st.dataframe(
        scores, hide_index=True, use_container_width=True,
        column_config={
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=30)
        },
    )


def _position_sizing(stock):
    section_header("Risk Inputs", "Calculator only; it does not place an order")
    capital_col, risk_col = st.columns(2)
    capital = capital_col.number_input(
        "Capital", min_value=1000.0, value=100000.0, step=1000.0
    )
    risk = risk_col.slider("Risk Per Trade (%)", 0.25, 5.0, 1.0, 0.25)
    summary = PositionSizer(capital, risk).calculate(
        stock["BuyHigh"], stock["StopLoss"]
    )
    metrics = st.columns(3)
    metrics[0].metric("Suggested Shares", summary["Shares"])
    metrics[1].metric("Position Value", f"{summary['PositionValue']:,.2f}")
    metrics[2].metric("Maximum Loss", f"{summary['MaximumLoss']:,.2f}")
    metrics = st.columns(2)
    metrics[0].metric("Cash Remaining", f"{summary['CashRemaining']:,.2f}")
    metrics[1].metric("Capital Check", "PASS" if summary["EnoughCapital"] else "INSUFFICIENT")


def _chart_and_indicators(stock):
    frame = stock["Data"]
    last = frame.iloc[-1]
    section_header(
        "Price & Decision Levels",
        "Display-only overlay; frozen entry, target, stop and indicators are unchanged",
    )
    chart = frame[[name for name in ("Close", "EMA20", "EMA50", "EMA200", "VWAP") if name in frame]].copy()
    overlays = {
        "Entry": stock.get("BuyHigh"),
        "Target 1": stock.get("Target1"),
        "Target 2": stock.get("Target2"),
        "Stop": stock.get("StopLoss"),
        "Support": stock.get("Support"),
        "Resistance": stock.get("Resistance"),
    }
    for name, value in overlays.items():
        if value is not None:
            chart[name] = float(value)
    st.line_chart(chart.tail(180))
    if "Volume" in frame:
        section_header("Volume", "Source volume; no transformation")
        st.bar_chart(frame[["Volume"]].tail(180), color="#64748b")
    section_header("Latest Indicators", "Last available source candle")
    names = ["EMA20", "EMA50", "EMA200", "RSI", "MACD", "ADX", "ATR", "OBV"]
    indicators = pd.DataFrame({
        "Indicator": names,
        "Value": [
            round(float(last[name]), 2) if name in last and pd.notna(last[name]) else None
            for name in names
        ],
    })
    st.dataframe(indicators, hide_index=True, use_container_width=True)


def stock_level_context(
    stock,
    *,
    observed_high=None,
    observed_low=None,
    observed_high_timestamp=None,
    observed_low_timestamp=None,
    observed_high_verified=False,
    observed_low_verified=False,
    now=None,
    holidays=None,
):
    """Build the display-only target/provenance model for one scanner row.

    The function is public for focused regression tests.  It reads raw values
    from the frozen row and ``DataFrame.attrs`` and never mutates either.
    """

    frame = stock.get("Data")
    metadata = (
        dict(frame.attrs.get("market_data", {}))
        if frame is not None and hasattr(frame, "attrs")
        else {}
    )
    required_provenance = (
        "SignalTimestamp", "EvidenceHash", "LevelsCalculationVersion",
        "HistoricalProvider",
    )
    legacy = any(not stock.get(name) for name in required_provenance)

    frozen_price = _number(stock.get("FrozenSnapshotPrice"))
    if frozen_price is None and frame is not None and len(frame):
        frozen_price = _number(frame.iloc[-1].get("Close"))
    if frozen_price is None:
        frozen_price = _number(stock.get("Price"))

    level_timestamp = (
        stock.get("FrozenDataTimestamp")
        or (None if legacy else metadata.get("latest_completed_candle"))
    )
    signal_timestamp = stock.get("SignalTimestamp") if not legacy else None
    completed_close = _number(stock.get("CompletedSessionClose"))
    completed_timestamp = stock.get("CompletedSessionTimestamp")
    completed_provider = stock.get("CompletedSessionProvider")
    if not legacy:
        completed_close = completed_close if completed_close is not None else frozen_price
        completed_timestamp = completed_timestamp or level_timestamp
        completed_provider = (
            completed_provider or stock.get("HistoricalProvider")
            or metadata.get("provider")
        )
    live_price = _number(
        stock.get("LivePrice")
        if stock.get("LivePrice") is not None
        else metadata.get("live_quote_last")
    )
    live_timestamp = (
        stock.get("LivePriceTimestamp") or metadata.get("live_quote_timestamp")
    )
    uses_live = live_price is not None
    freshness = str(
        stock.get("LivePriceStatus")
        or
        metadata.get("live_quote_freshness")
        or metadata.get("freshness")
        or ""
    ).upper()
    provider = (
        stock.get("HistoricalProvider")
        or (None if legacy else metadata.get("provider"))
        or "Legacy Snapshot"
    )
    live_provider = (
        stock.get("LiveProvider")
        or (None if legacy else metadata.get("live_quote_provider"))
        or ("rubix" if uses_live and not legacy else "")
    )
    comparison = select_comparison_price(
        live_price=live_price,
        live_provider=live_provider,
        live_timestamp=live_timestamp,
        live_status=freshness,
        completed_close=completed_close,
        completed_provider=completed_provider,
        completed_timestamp=completed_timestamp,
        frozen_price=frozen_price,
        frozen_timestamp=level_timestamp,
        signal_timestamp=signal_timestamp,
        now=now,
        holidays=holidays,
    )

    direction = (
        SHORT if str(stock.get("Direction", LONG)).upper() == SHORT else LONG
    )
    legacy_reason = (
        "Legacy Snapshot: signal timestamp/provenance is incomplete"
        if legacy else ""
    )
    frozen_assessment = assess_levels(
        frozen_price,
        (("Target 1", stock.get("Target1")), ("Target 2", stock.get("Target2"))),
        direction=direction,
        entry=stock.get("BuyHigh"),
        stop=stock.get("StopLoss"),
        signal_timestamp=level_timestamp,
        price_timestamp=level_timestamp,
        price_basis=BASIS_FROZEN_CLOSE,
        stale_reason=legacy_reason,
    )
    live_assessment = None
    if uses_live:
        live_stale_reason = ""
        if comparison.status != BASIS_LIVE:
            live_stale_reason = comparison.reason or "Rubix overlay is not comparable"
        live_assessment = assess_levels(
            live_price,
            (("Target 1", stock.get("Target1")), ("Target 2", stock.get("Target2"))),
            direction=direction,
            stop=stock.get("StopLoss"),
            high_water=(
                observed_high
                if observed_high is not None
                else stock.get("ObservedHighSinceSignal")
            ),
            low_water=(
                observed_low
                if observed_low is not None
                else stock.get("ObservedLowSinceSignal")
            ),
            high_water_timestamp=observed_high_timestamp,
            low_water_timestamp=observed_low_timestamp,
            high_water_verified=observed_high_verified,
            low_water_verified=observed_low_verified,
            signal_created_timestamp=signal_timestamp,
            signal_timestamp=signal_timestamp,
            price_timestamp=live_timestamp,
            price_basis=BASIS_LIVE,
            stale_reason=live_stale_reason,
        )
    if comparison.status == BASIS_LIVE:
        assessment = live_assessment
    elif comparison.status == BASIS_COMPLETED_CLOSE:
        assessment = assess_levels(
            comparison.value,
            (("Target 1", stock.get("Target1")), ("Target 2", stock.get("Target2"))),
            direction=direction,
            stop=stock.get("StopLoss"),
            signal_created_timestamp=signal_timestamp,
            signal_timestamp=level_timestamp,
            price_timestamp=comparison.timestamp,
            price_basis=BASIS_COMPLETED_CLOSE,
            stale_reason=legacy_reason,
        )
    else:
        assessment = assess_levels(
            comparison.value,
            (("Target 1", stock.get("Target1")), ("Target 2", stock.get("Target2"))),
            direction=direction,
            stop=stock.get("StopLoss"),
            signal_created_timestamp=signal_timestamp,
            signal_timestamp=level_timestamp,
            price_timestamp=comparison.timestamp,
            price_basis=BASIS_FROZEN_CLOSE,
            stale_reason=legacy_reason or (
                comparison.reason if comparison.status == STALE_LEVEL else ""
            ),
        )

    levels_version = (
        stock.get("LevelsCalculationVersion")
        if not legacy else "Legacy Snapshot"
    )
    evidence_hash = stock.get("EvidenceHash") if not legacy else None
    completed_session = _session_date(level_timestamp)
    provenance = Provenance(
        data_timestamp=str(comparison.timestamp or ""),
        signal_timestamp=str(signal_timestamp or ""),
        session=completed_session,
        provider=str(provider),
        live_provider=str(live_provider or ""),
        engine=str(levels_version or "Legacy Snapshot"),
        evidence_hash=str(evidence_hash or "Legacy Snapshot"),
        status=(
            "LEGACY SNAPSHOT"
            if legacy else MIXED if uses_live else FROZEN
        ),
    )
    return {
        "assessment": assessment,
        "frozen_assessment": frozen_assessment,
        "live_assessment": live_assessment,
        "frozen_price": frozen_price,
        "live_price": live_price,
        "live_timestamp": live_timestamp,
        "uses_live_price": uses_live,
        "comparison": comparison,
        "price_basis": assessment.price_basis,
        "price_timestamp": comparison.timestamp,
        "level_timestamp": level_timestamp,
        "signal_timestamp": signal_timestamp,
        "completed_session": completed_session,
        "levels_version": levels_version,
        "provenance": provenance,
        "legacy": legacy,
    }


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def _session_date(value):
    if value in (None, ""):
        return ""
    try:
        return pd.Timestamp(value).date().isoformat()
    except (TypeError, ValueError):
        return str(value)


def _short_timestamp(value):
    if value in (None, ""):
        return "—"
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value)


def _price(value):
    number = _number(value)
    return "Not available" if number is None else f"{number:.2f}"


def _percent(value):
    number = _number(value)
    return "—" if number is None else f"{number:.2f}%"


def _ratio(value):
    number = _number(value)
    return "—" if number is None else f"{number:.2f}"
