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
    DRAWDOWN_FREQUENCY,
    ENGINE_VERSION,
    EXCURSION_QUANTILES,
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

    page_header("Swing Breakout",
            "Four conditions, each from a measurement rather than a "
            "judgement. It never places an order and never reports a fill.",
            icon="📈")
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

    universe = most_traded(histories)
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
            f"strategy fires about eighty times a year across the names liquid "
            f"enough to trade, so most sessions produce nothing and that is "
            f"the design working, not a fault.",
        )
    else:
        st.dataframe(
            _candidate_frame(result), hide_index=True, width="stretch",
            column_config=_column_config(),
        )
        _show_where_trades_went(result)

    _show_basis(config)


def _show_where_trades_went(result) -> None:
    """The measured distribution, priced against each candidate's own close.

    Deliberately not a target and not a stop. The strategy has neither -- seven
    exit rules were tested against the fixed hold and none beat it -- so there
    is no measured level to propose. What exists is where 1,897 historical
    trades actually went, and quoting its quartiles is the difference between
    reporting a measurement and inventing a price.
    """
    st.subheader("Where the trades went")
    st.caption(
        f"Not a target and not a stop: this strategy has neither. These are "
        f"the quartiles of **1,897 historical signals** over {MEASURED_HOLD} "
        f"sessions, priced against each close. A single trade lands anywhere "
        f"in that range, including outside it."
    )

    rows = []
    for candidate in result.candidates:
        row = {"Ticker": candidate.symbol, "Close": candidate.close}
        for label, key in (("Worst point", "worst_point"),
                           ("Best point", "best_point"),
                           (f"Day {MEASURED_HOLD}", "at_day_20")):
            quantiles = EXCURSION_QUANTILES[key]
            row[f"{label} (typical)"] = candidate.close * (1 + quantiles["median"] / 100)
            row[f"{label} (range)"] = (
                f"{candidate.close * (1 + quantiles['lower'] / 100):.2f}"
                f" – {candidate.close * (1 + quantiles['upper'] / 100):.2f}"
            )
        rows.append(row)

    price = st.column_config.NumberColumn(format="%.2f", width="small")
    st.dataframe(
        pd.DataFrame(rows), hide_index=True, width="stretch",
        column_config={
            "Ticker": st.column_config.TextColumn(width="small", pinned=True),
            "Close": price,
            "Worst point (typical)": st.column_config.NumberColumn(
                "Worst (typical)", format="%.2f", width="small",
                help="The median trade's lowest point in the window. Half of "
                     "them went lower.",
            ),
            "Worst point (range)": st.column_config.TextColumn(
                "Worst (middle half)", width="small"),
            "Best point (typical)": st.column_config.NumberColumn(
                "Best (typical)", format="%.2f", width="small",
                help="The median trade's highest point. Reaching it and "
                     "keeping it are different things.",
            ),
            "Best point (range)": st.column_config.TextColumn(
                "Best (middle half)", width="small"),
            f"Day {MEASURED_HOLD} (typical)": st.column_config.NumberColumn(
                f"Day {MEASURED_HOLD} (typical)", format="%.2f", width="small",
                help="Where the median trade actually closed the window, "
                     "which is well below its best point.",
            ),
            f"Day {MEASURED_HOLD} (range)": st.column_config.TextColumn(
                f"Day {MEASURED_HOLD} (middle half)", width="small"),
        },
    )

    falls = " · ".join(
        f"**{share:.0f}%** fall more than {threshold}%"
        for threshold, share in sorted(DRAWDOWN_FREQUENCY.items())
    )
    st.warning(
        f"Being underwater is the normal case, not a broken trade: {falls} at "
        f"some point inside the window. The median trade's worst moment is "
        f"**{EXCURSION_QUANTILES['worst_point']['median']:+.2f}%** before it "
        f"does anything.",
        icon="📉",
    )
    st.caption(
        f"{MEASURED_HOLD} sessions is what the measurement held for, not an "
        f"instruction to exit on day {MEASURED_HOLD}."
    )


def _show_basis(config: SwingConfig) -> None:
    """Where every threshold came from, and what the strategy is not."""

    with st.expander("على أي أساس · What this rests on", expanded=False):
        st.caption(MEASUREMENT_PROVENANCE)
        st.markdown(
            f"""
**The four conditions.** A close above the highest high of the previous
{config.breakout_lookback} sessions, on at least **{config.minimum_volume_ratio:g}×**
its own 20-day average volume, in a name whose 12-1 momentum sits in the top
**{(1 - config.minimum_momentum_rank) * 100:.0f}%** of the universe that day, and
whose close is above its own **{config.long_trend_window}-day average**.

**The long-trend condition was the last thing to earn a place**, out of eight
exit- and entry-side rules tested against a fixed 20-day hold. Trailing stops,
hard stops, longer holds and a trend-break exit all lost; a market-regime
filter did nothing at all, because the drawdown comes from forty positions
falling together rather than from any one breaking down. The name's own trend
was different: **+2.99% to +3.66% training, +3.35% to +3.44% validation**, with
the worst fall from −19.5% to −16.7%. It removes 8% of trades, and those trades
carry a lift of **−2.64%** on their own — it is cutting a loss, not trimming a
win.

**Lift over owning every name in the universe on the same days**, which is the
only honest benchmark because the validation era was a strong bull market:

| | training | validation | win rate |
| --- | --- | --- | --- |
| breakout on volume alone | +3.17% | +2.79% | 59% |
| **and momentum in the top third** | **+3.90%** | **+5.22%** | **62%** |

**The universe is bounded by liquidity, not by a count.** A count is arbitrary
and goes stale; a turnover floor scales with the position it has to absorb.
Swept by floor with no gate touched:

| floor | names | trades/yr | lift/trade | **annual** | held at once | win |
| --- | --- | --- | --- | --- | --- | --- |
| 20M | 53 | 30 | +3.98% | +120% | 2.4 | 61% |
| 10M | 95 | 58 | +3.74% | +215% | 4.6 | 57% |
| **5M** | **139** | **83** | **+3.35%** | **+277%** | **6.6** | **54%** |
| 3M | 162 | 97 | +2.61% | +252% | 7.7 | 53% |
| 2M | 176 | 104 | +2.60% | +269% | 8.3 | 53% |

The column that matters to a portfolio is **annual** — trades times lift, not
lift alone. Optimising lift per trade instead picks 10M and leaves a quarter of
the year's edge unclaimed for a prettier per-trade number.

It is also where execution stays real. At a 100,000 EGP position the 5M floor
means never taking more than 2% of a name's daily turnover. Ranked by count
instead, the 200th name on the exchange trades 20,000 EGP a day: a backtest can
buy it, you cannot, and the cost assumed for it is fiction. Liquidity is ranked
on recent turnover and applied backwards, so every row shares the same
survivorship flattery — the comparison between them is fair, the absolute level
is optimistic.

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
