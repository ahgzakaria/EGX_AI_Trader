"""The daily analysis chart, built once from the unified presentation model.

Timeframe selection changes ONLY how many completed candles are displayed. Every
annotated level comes from :class:`~core.analysis_presentation.AnalysisPresentation`
and is therefore identical on every timeframe — the chart never recalculates a
support, a resistance, a breakout, an invalidation or a pullback measurement.

The figure the page shows is the same object the PNG download renders, so the
export can never disagree with the screen.

No provider, network or database access. The Daily chart carries EODHD completed
sessions only; Rubix intraday is a separate series and never enters here.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.analysis_presentation import EM_DASH, AnalysisPresentation, fmt_value

#: Timeframe -> number of completed sessions (``None`` = everything supplied).
TIMEFRAMES = {
    "3M": 63,
    "6M": 126,
    "1Y": 252,
    "FULL": None,
}
#: Roughly six months of completed sessions, so current structure is legible
#: instead of being flattened by a year of older range.
DEFAULT_TIMEFRAME = "6M"
DEFAULT_SESSIONS = 120

TIMEFRAME_LABELS = {
    "3M": "٣ أشهر · 3M",
    "6M": "٦ أشهر · 6M",
    "1Y": "سنة · 1Y",
    "FULL": "الكل · Full",
}

#: Visual hierarchy. A minor level must never read as loudly as an invalidation.
LEVEL_STYLE = {
    "major":      {"width": 3.0, "dash": "solid", "opacity": 1.00},
    "medium":     {"width": 2.0, "dash": "dash", "opacity": 0.90},
    "minor":      {"width": 1.2, "dash": "dot", "opacity": 0.65},
    "diagnostic": {"width": 1.6, "dash": "dashdot", "opacity": 0.80},
}

LEVEL_COLOURS = {
    "support_1": "#34d399", "support_2": "#10b981",
    "resistance_1": "#fbbf24", "resistance_2": "#f59e0b",
    "breakout": "#60a5fa", "invalidation": "#f87171",
    "research_trigger": "#c084fc", "major_resistance": "#a78bfa",
}

SUPPORT_BAND_KEYS = ("support_1", "support_2")


@dataclass(frozen=True)
class ChartWindow:
    """The candles a timeframe displays, plus the levels (which never change)."""

    timeframe: str
    candles: tuple
    levels: tuple
    last_session: str


def select_window(presentation: AnalysisPresentation,
                  timeframe: str = DEFAULT_TIMEFRAME) -> ChartWindow:
    """Slice the candle history. Levels are passed through untouched."""

    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected {sorted(TIMEFRAMES)}")
    count = TIMEFRAMES[timeframe]
    candles = presentation.candles
    window = candles if count is None else candles[-count:]
    return ChartWindow(timeframe=timeframe, candles=tuple(window),
                       levels=presentation.levels,
                       last_session=presentation.last_completed_session)


def _ema(values, span):
    """Display-only EMA over the plotted closes. Never feeds a decision."""

    usable = [v for v in values if v is not None]
    if len(usable) < span:
        return []
    multiplier = 2.0 / (span + 1)
    out, running = [], None
    for value in values:
        if value is None:
            out.append(running)
            continue
        running = float(value) if running is None else (
            (float(value) - running) * multiplier + running)
        out.append(running)
    return out


def resolve_label_lanes(levels, *, min_gap: float) -> dict:
    """Assign each level a vertical slot so close labels never overlap.

    Deterministic: levels are sorted by price and any label closer than
    ``min_gap`` to the previous one is pushed up by exactly the remaining gap.
    Given 7.37 / 7.50 / 7.54 / 7.57 / 7.66 this yields five distinct anchors in a
    stable order, on every render.
    """

    present = sorted(((key, float(price)) for key, price in levels if price is not None),
                     key=lambda item: item[1])
    lanes, previous = {}, None
    for key, price in present:
        anchor = price if previous is None else max(price, previous + min_gap)
        lanes[key] = anchor
        previous = anchor
    return lanes


def build_daily_figure(presentation: AnalysisPresentation, *,
                       timeframe: str = DEFAULT_TIMEFRAME, height: int = 620):
    """Return a Plotly figure for the Daily tab, or ``None`` without Plotly."""

    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception:
        return None

    window = select_window(presentation, timeframe)
    candles = window.candles
    if not candles:
        return None

    dates = [c.date for c in candles]
    closes = [c.close for c in candles]

    figure = make_subplots(rows=2, cols=1, shared_xaxes=True,
                           row_heights=[0.76, 0.24], vertical_spacing=0.04)

    figure.add_trace(go.Candlestick(
        x=dates, open=[c.open for c in candles], high=[c.high for c in candles],
        low=[c.low for c in candles], close=closes, name="EODHD Daily",
        increasing_line_color="#34d399", decreasing_line_color="#f87171",
        hovertext=[f"{c.date}<br>O {fmt_value(c.open)} H {fmt_value(c.high)}"
                   f"<br>L {fmt_value(c.low)} C {fmt_value(c.close)}"
                   f"<br>Vol {fmt_value(c.volume, 0)}" for c in candles],
        hoverinfo="text"), row=1, col=1)

    for span, colour in ((20, "#60a5fa"), (50, "#fbbf24"), (200, "#a78bfa")):
        series = _ema(closes, span)
        if series and any(v is not None for v in series):
            figure.add_trace(go.Scatter(
                x=dates, y=series, mode="lines", name=f"EMA{span}",
                line=dict(color=colour, width=1.6), hoverinfo="skip"), row=1, col=1)

    figure.add_trace(go.Bar(
        x=dates, y=[c.volume for c in candles], name="Volume",
        marker_color=["#34d399" if (c.close or 0) >= (c.open or 0) else "#f87171"
                      for c in candles],
        opacity=0.55, hovertemplate="%{x}<br>Vol %{y:,.0f}<extra></extra>"),
        row=2, col=1)

    prices = [c.high for c in candles if c.high is not None] + \
             [c.low for c in candles if c.low is not None]
    span_price = (max(prices) - min(prices)) if prices else 1.0
    min_gap = span_price * 0.035

    pullback = presentation.pullback
    if pullback.support_zone_low is not None and pullback.support_zone_high is not None:
        figure.add_hrect(y0=pullback.support_zone_low, y1=pullback.support_zone_high,
                         fillcolor="#c084fc", opacity=0.16, line_width=0,
                         annotation_text="Pullback zone (research)",
                         annotation_position="top left", row=1, col=1)

    # Supports render as translucent BANDS, not hairlines.
    for key in SUPPORT_BAND_KEYS:
        level = presentation.level(key)
        if level and level.present:
            half = span_price * 0.006
            figure.add_hrect(y0=level.price - half, y1=level.price + half,
                             fillcolor=LEVEL_COLOURS[key], opacity=0.18,
                             line_width=0, row=1, col=1)

    lanes = resolve_label_lanes(
        [(lv.key, lv.price) for lv in presentation.levels], min_gap=min_gap)
    for level in presentation.levels:
        if not level.present:
            continue
        style = LEVEL_STYLE.get(level.weight, LEVEL_STYLE["medium"])
        colour = LEVEL_COLOURS.get(level.key, "#94a3b8")
        figure.add_hline(y=level.price, line=dict(
            color=colour, width=style["width"], dash=style["dash"]), row=1, col=1)
        # The annotation lane keeps close labels legible; the LINE stays on the
        # true price, only its label is nudged.
        figure.add_annotation(
            x=1.0, xref="paper", y=lanes.get(level.key, level.price), yref="y",
            text=f"{level.label_en} {level.display}", showarrow=False,
            xanchor="left", align="left", font=dict(size=11, color=colour),
            bgcolor="rgba(11,18,32,0.72)", borderpad=3)

    if presentation.close is not None:
        figure.add_hline(y=presentation.close, line=dict(color="#e6edf7", width=2.4),
                         row=1, col=1)
        figure.add_annotation(
            x=1.0, xref="paper", y=presentation.close, yref="y",
            text=f"Last {fmt_value(presentation.close)}", showarrow=False,
            xanchor="left", font=dict(size=12, color="#0b1220"),
            bgcolor="#e6edf7", borderpad=4)

    for value, label, colour in (
            (pullback.swing_high, "Swing high", "#fbbf24"),
            (pullback.impulse_low, "Impulse low", "#34d399")):
        if value is not None:
            figure.add_trace(go.Scatter(
                x=[dates[-1]], y=[value], mode="markers", name=label,
                marker=dict(symbol="diamond", size=10, color=colour),
                hovertemplate=f"{label} %{{y:.2f}}<extra></extra>"), row=1, col=1)

    figure.update_layout(
        template="plotly_dark", height=height,
        margin=dict(l=60, r=210, t=42, b=36),
        paper_bgcolor="#0b1220", plot_bgcolor="#0e1729",
        xaxis_rangeslider_visible=False, hovermode="x unified",
        showlegend=True,
        legend=dict(orientation="h", y=1.10, bgcolor="rgba(0,0,0,0)"),
        title=dict(text=f"{presentation.ticker} · EODHD daily · "
                        f"last completed session {window.last_session} · {timeframe}",
                   font=dict(size=13, color="#8ea1bd"), x=0, xanchor="left"))
    figure.update_yaxes(title_text=presentation.currency, gridcolor="#223049", row=1, col=1)
    figure.update_yaxes(title_text="Volume", gridcolor="#223049", row=2, col=1)
    figure.update_xaxes(showgrid=False, row=2, col=1)
    return figure


def figure_to_png(figure, *, scale: int = 2) -> bytes | None:
    """High-resolution PNG of the SAME figure shown on screen (needs kaleido)."""

    if figure is None:
        return None
    try:
        return figure.to_image(format="png", scale=scale)
    except Exception:
        return None


__all__ = [
    "DEFAULT_SESSIONS", "DEFAULT_TIMEFRAME", "LEVEL_COLOURS", "LEVEL_STYLE",
    "TIMEFRAMES", "TIMEFRAME_LABELS", "ChartWindow", "build_daily_figure",
    "figure_to_png", "resolve_label_lanes", "select_window",
]
