"""Swing breakout candidates — a read-only research view.

Shows what the volume-breakout engine found on the last completed session, and
the measurement each of its three conditions came from. It places no order,
reports no fill, and holds no position.

Two things are on this page that most signal screens leave off, because the
week that produced this engine turned on them:

* **the cost, next to the move.** The round trip is 0.46% in fees plus the
  measured spread, and an intraday version of this strategy failed entirely
  because its targets were smaller than that.
* **what the numbers are not.** Four of fourteen years lost money and the
  median trade returns 0.88%. A page that shows only the average is showing
  the good half.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from dashboard.ui import page_header

from services.swing_breakout import (
    ENGINE_VERSION,
    MEASUREMENT_PROVENANCE,
    SwingConfig,
    load_universe_histories,
    most_traded,
    scan,
)

#: How long the measurement held for. Shown, never acted on.
MEASURED_HOLD = SwingConfig().measured_holding_days


def _candidate_frame(scan_result) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Ticker": c.symbol,
            "Close": c.close,
            "Breakout level": c.breakout_level,
            "Past level %": c.extension_percent,
            "Volume ×": c.volume_ratio,
            "Momentum 12-1 %": c.momentum_12_1,
            "Momentum rank": c.momentum_rank,
            "ATR %": c.atr_percent,
        }
        for c in scan_result.candidates
    ])


def _column_config() -> dict:
    return {
        "Ticker": st.column_config.TextColumn("Ticker", width="small", pinned=True),
        "Close": st.column_config.NumberColumn("Close", format="%.3f", width="small"),
        "Breakout level": st.column_config.NumberColumn(
            "Level", format="%.3f", width="small",
            help="The highest high of the previous 20 sessions. Today's own "
                 "bar is excluded, or every close would break out of itself.",
        ),
        "Past level %": st.column_config.NumberColumn(
            "Past level %", format="%.2f%%", width="small",
            help="How far beyond the level the close already sits. Reported, "
                 "not filtered: no threshold here has been measured.",
        ),
        "Volume ×": st.column_config.NumberColumn(
            "Volume ×", format="%.1f×", width="small",
            help="Against its own 20-day average. Below 1.5× a breakout "
                 "returned -0.11% over twenty days, worse than not trading; "
                 "1.5-2.5× returned +2.26%, no better than sitting out; above "
                 "2.5×, +5.73% at a 59% win rate.",
        ),
        "Momentum 12-1 %": st.column_config.NumberColumn(
            "Momentum 12-1", format="%.1f%%", width="small",
            help="The eleven months ending one month ago. The most recent "
                 "month is skipped because it tends to reverse.",
        ),
        "Momentum rank": st.column_config.ProgressColumn(
            "Rank", min_value=0.0, max_value=1.0, format="%.2f",
            help="Where this name sits against the whole universe that day. "
                 "The gate keeps the top third.",
        ),
        "ATR %": st.column_config.NumberColumn(
            "ATR %", format="%.2f%%", width="small",
            help="Daily true range as a percent of price. Context only.",
        ),
    }


def show_swing_signals() -> None:
    """Streamlit page: today's swing breakout candidates."""

    page_header("Swing Breakout", "Three conditions, each from a measurement rather than a judgement. It never places an order and never reports a fill.", icon="📈")
    config = SwingConfig()
    if not st.button("افحص السوق · Scan the market", type="primary"):
        st.info(
            "The scan reads a year of daily history for every name in the "
            "universe, so it takes a couple of minutes. Run it after the "
            "close, when the session's own bar is final.",
            icon="🔎",
        )
        _show_basis(config)
        return

    failures = {}
    with st.spinner("Reading daily history for the universe…"):
        histories = load_universe_histories(
            on_error=lambda symbol, reason: failures.setdefault(symbol, reason)
        )
    if not histories:
        st.error("No history could be read for any symbol.")
        return

    universe = most_traded(histories, count=config.universe_size)
    session = max(
        (str(frame.index[-1])[:10] for frame in universe.values()),
        default=date.today().isoformat(),
    )
    result = scan(universe, session_date=session, config=config)

    columns = st.columns(4)
    columns[0].metric("Candidates", result.candidate_count)
    columns[1].metric("Universe scanned", result.symbols_considered)
    columns[2].metric("Session", result.session_date)
    columns[3].metric("Unreadable", len(failures))

    if failures:
        with st.expander(f"{len(failures)} symbols could not be read"):
            st.dataframe(
                pd.DataFrame(
                    [{"Ticker": s, "Reason": r} for s, r in sorted(failures.items())]
                ),
                hide_index=True, width="stretch",
            )

    if result.candidate_count == 0:
        st.info(
            f"Nothing met all three conditions on {result.session_date}. The "
            f"strategy fires about sixty times a year across "
            f"{config.universe_size} names, so most sessions produce nothing "
            f"and that is the design working, not a fault.",
        )
    else:
        st.dataframe(
            _candidate_frame(result), hide_index=True, width="stretch",
            column_config=_column_config(),
        )
        st.caption(
            f"Measured holding period was **{MEASURED_HOLD} sessions**. That is "
            f"what the numbers below were measured over, not an instruction to "
            f"exit on day {MEASURED_HOLD}."
        )

    _show_basis(config)


def _show_basis(config: SwingConfig) -> None:
    """Where every threshold came from, and what the strategy is not."""

    with st.expander("على أي أساس · What this rests on", expanded=False):
        st.caption(MEASUREMENT_PROVENANCE)
        st.markdown(
            f"""
**The three conditions.** A close above the highest high of the previous
{config.breakout_lookback} sessions, on at least **{config.minimum_volume_ratio:g}×**
its own 20-day average volume, in a name whose 12-1 momentum sits in the top
**{(1 - config.minimum_momentum_rank) * 100:.0f}%** of the universe that day.

**Lift over owning every name in the universe on the same days**, which is the
only honest benchmark because the validation era was a strong bull market:

| | training | validation | win rate |
| --- | --- | --- | --- |
| breakout on volume alone | +3.17% | +2.79% | 59% |
| **and momentum in the top third** | **+3.90%** | **+5.22%** | **62%** |

**The universe is {config.universe_size} names, and that was measured too.**
Swept from 40 to 200 with no gate touched, so this widens the opportunity set
rather than loosening a threshold:

| universe | trades/year | training | validation | win rate |
| --- | --- | --- | --- | --- |
| 40 | 25 | +2.53% | +2.71% | 64% |
| 60 | 34 | +2.57% | +4.42% | 62% |
| **100** | **61** | **+3.74%** | **+3.56%** | **57%** |
| 150 | 90 | +2.57% | +2.42% | 53% |
| 200 | 118 | +2.59% | +2.40% | 54% |

At sixty the validation lift is nearly double the training one, which reads
that era rather than the strategy. At a hundred the two agree and the trade
count nearly doubles. Past that the edge decays toward a coin. Liquidity is
ranked on recent turnover and applied backwards, so every row above carries the
same survivorship flattery — the comparison between them is fair, the absolute
level is optimistic.

**The momentum filter was not fitted.** Tightening it strengthens the result
monotonically — top 75% gives +3.52%, top half +4.44%, top third +5.22% — and
inverting it weakens it, with the bottom half at +1.49% and the bottom quarter
at +1.14%. Momentum measured on its own over this universe is worth nothing.

**What this is not.** Four of fourteen years lost money, and the median trade
returns 0.88% — barely more than the round trip. The profit sits in a thin
tail of large winners, which is what a breakout strategy is and what makes it
hard to hold through a losing run. It is not a probability, not a
recommendation, and not an order.

**Value was tested, and did not earn a place.** The frontier-market literature
rates it the strongest factor of all. EODHD returns HTTP 403 on fundamentals
here, but Yahoo carries them for EGX, and annual equity read months after
publication does not care that Yahoo's daily candle arrives late.

| P/B quintile | training | validation |
| --- | --- | --- |
| cheapest | +11.20% | +5.67% |
| | +1.15% | +7.61% |
| | +3.52% | +3.20% |
| | +10.19% | +3.10% |
| dearest | −1.93% | +3.59% |
| **cheapest half** | **+5.23%** | **+6.14%** |
| **dearest half** | **+5.31%** | +2.94% |

In training the two halves are the same number — value separates nothing. In
validation the cheap half leads by 3.2 points, but neither era grades smoothly
and the quintiles disagree about where the effect even lives. That is what
noise looks like. Momentum, which was kept, strengthened monotonically as it
was tightened and weakened when inverted, in both eras.

Coverage settles it regardless: only **23% of breakouts** can be scored at all,
because Yahoo's annual history begins around 2022 for most of this universe and
25 of the 60 names carry no usable equity. Two years of validation against the
fourteen behind momentum, on a quarter of the signals, cannot justify a gate —
and Yahoo serves restated figures, which flatters even that.

Re-run it with `scripts/research/value_factor.py`.

Re-derive anything above with `scripts/research/swing_candidates.py` and
`scripts/research/breakout_filters.py`. Engine `{ENGINE_VERSION}`.
"""
        )
