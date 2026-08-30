"""اختراق مؤكد · Confirmed Volume Breakout — a read-only research view.

Shows what the rule found on the last completed session, the plan each signal
implies, and the measurement behind every one of its seven conditions. It places
no order, reports no fill and holds no position.

Three things are on this page that a signal screen usually leaves off, and each
one is here because the strategy beside it shipped without them:

* **the whole funnel, not only the survivors.** A quiet day is the design
  working — the rule fires about ninety times a year across a 190-name universe
  — and a page that shows an empty table without saying why reads as a fault.
* **what the rule refuses to tell you.** There is no target and no score. The
  Daily Dashboard strategy has both, and its score's correlation with the
  outcome is r = -0.032 while its first target lands *below* the entry on a
  breakout. Absent numbers are marked as absent rather than filled with
  something confident-looking.
* **the benchmark.** Over the tested decade the median EGX name returned
  +403% while this rule returned +136%. Both are nominal EGP in a currency that
  lost most of its value; the claim being made is about selection, not about
  beating the market, and the page says so.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.formatting import company_name
from dashboard.scan_memory import recall, remember, scan_caption
from dashboard.ui import empty_state, page_header, section_header
from strategy_momentum_breakout.config import load as load_config
from strategy_momentum_breakout.scan import scan

#: Names this page's entry in the per-browser-session scan memory. A scan reads
#: bars that are already final, so it survives navigating away and back.
SCAN_KEY = "confirmed_breakout"

#: Read from the measured record in `docs/audits/strategies/`. Shown so the page
#: never quotes a number that no longer has a run behind it.
MEASURED = {
    "trades": 634,
    "signals": 927,
    "win_rate": 52.05,
    "profit_factor": 1.93,
    "total_return": 135.82,
    "cagr": 10.16,
    "max_drawdown": 11.54,
    "max_drawdown_closed": 8.79,
    "sharpe": 1.89,
    "median_trade": 0.80,
    "top5_share": 18.8,
    "lift_train": 3.85,
    "lift_valid": 1.92,
    "benchmark_median": 403.02,
}

GATE_LABELS = {
    "PriceIntegrity": "سلامة السعر · Price integrity",
    "Liquidity": "السيولة · Liquidity",
    "LongTermTrend": "الاتجاه طويل المدى · Above EMA200",
    "Calm": "الهدوء · Calm for itself",
    "Breakout": "الاختراق · 20-day breakout",
    "VolumeConfirmation": "تأكيد الحجم · Volume ≥ 2.5×",
    "ClosePosition": "قوة الإغلاق · Close near the high",
    "InsufficientHistory": "تاريخ غير كافٍ · Not enough history",
    "InvalidRisk": "مخاطرة غير صالحة · Stop not below the close",
    "Unusable": "بيانات غير صالحة · Unreadable",
}


def show_confirmed_breakout() -> None:
    """Streamlit page: today's confirmed volume breakouts."""

    cfg = load_config()
    page_header(
        "اختراق مؤكد · Confirmed Breakout",
        "Seven conditions, each one measured against owning the market on the "
        "same days. No score, no target, no trailing stop.",
        icon="🚀",
    )

    rescan = st.button("افحص السوق · Scan the market", type="primary")
    remembered = None if rescan else recall(SCAN_KEY, cfg)

    if rescan:
        with st.spinner("Reading daily history for the universe…"):
            result = scan(cfg=cfg)
        if not result.considered:
            st.error("No history could be read for any symbol.")
            return
        remember(SCAN_KEY, result, cfg)
        remembered = recall(SCAN_KEY, cfg)
    elif remembered is not None:
        result = remembered.result
    else:
        # Shown before the scan as well as after it. Running a two-minute scan
        # should not be the price of seeing which of these numbers are
        # in-sample and which are not.
        _show_forward_record()
        _show_plan(cfg)
        _show_basis(cfg)
        return

    if remembered is not None:
        st.caption(scan_caption(remembered))

    columns = st.columns(4)
    columns[0].metric("إشارات · Signals", result.count)
    columns[1].metric("الأسهم المفحوصة · Scanned", result.considered)
    columns[2].metric("الجلسة · Session", result.session_date or "—")
    columns[3].metric("غير مقروء · Unreadable", len(result.unreadable))

    if result.count:
        _show_signals(result, cfg)
    else:
        empty_state(
            "لا توجد إشارة اليوم · Nothing met all seven conditions",
            f"The rule fires about ninety times a year across this universe, so "
            f"most sessions produce nothing. That is the design, not a fault — "
            f"the funnel below shows where {result.considered} symbols stopped.",
            icon="○",
        )

    _show_funnel(result)
    if result.unreadable:
        with st.expander(f"{len(result.unreadable)} symbols could not be read"):
            st.dataframe(
                pd.DataFrame([{"Ticker": s, "Reason": r}
                              for s, r in sorted(result.unreadable.items())]),
                hide_index=True, width="stretch")

    _show_forward_record()
    _show_plan(cfg)
    _show_basis(cfg)


def _show_forward_record() -> None:
    """The only evidence about this strategy that is not in-sample.

    Everything in "What this rests on" was measured on history the thresholds
    were chosen against. This section is the record of signals written down
    before anyone knew the answer, and it will be thin for months. Showing it
    thin is the point: a strategy page that displays only its backtest invites
    the reader to treat the backtest as the result.
    """
    try:
        from strategy_momentum_breakout.forward import ForwardTest

        summary = ForwardTest().report()
    except Exception as error:                          # noqa: BLE001 - shown
        st.caption(f"Forward record unavailable: {type(error).__name__}")
        return

    section_header(
        "السجل الحقيقي · The forward record",
        "Signals recorded before the outcome was known, and resolved by the "
        "same code that backtested them. This is the only evidence here that "
        "is not in-sample.",
    )
    columns = st.columns(4)
    columns[0].metric("جلسات مسجلة · Sessions", summary["sessions"])
    columns[1].metric("إشارات · Signals", summary["signals"])
    columns[2].metric("مغلقة · Closed", summary["closed"])
    columns[3].metric(
        "الرفع مقابل السوق · Lift",
        f"{summary['mean_lift']:+.2f}%" if summary["mean_lift"] is not None else "—",
    )

    if summary["closed"]:
        st.caption(
            f"Median {summary['median_net']:+.2f}%, win rate "
            f"{summary['win_rate']:.0f}%, over {summary['closed']} closed "
            f"trades since {summary['first_session']}. Lift is against an "
            f"average tradeable name over each trade's own window."
        )
    elif summary["sessions"]:
        st.info(
            f"{summary['sessions']} session(s) recorded since "
            f"{summary['first_session']}, {summary['signals']} signal(s), none "
            f"closed yet. The rule fires roughly ninety times a year and holds "
            f"twenty sessions, so a first closed trade is weeks away and a "
            f"number worth reading is many months away. **The backtest above is "
            f"not this.**",
            icon="⏳",
        )
    else:
        st.info(
            "Nothing recorded yet. Run "
            "`scripts/record_confirmed_breakout_forward.py` daily after the "
            "close — the sessions that produce nothing are part of the "
            "evidence, so they have to be recorded too.",
            icon="⏳",
        )

    if len(summary["configs"]) > 1:
        st.warning(
            f"This record spans {len(summary['configs'])} different "
            f"configurations. Signals from different calibrations must not be "
            f"pooled — split them by `config_hash` before reading any average.",
            icon="⚠️",
        )


def _show_signals(result, cfg) -> None:
    section_header(
        "إشارات الجلسة · This session's signals",
        "Ordered by how strongly volume confirmed the breakout. That is a "
        "reading order, not a ranking: nothing here predicts which one does best.",
    )
    rows = []
    for signal in result.signals:
        rows.append({
            "Ticker": signal.symbol,
            "Company": company_name(signal.symbol),
            "Close": signal.close,
            "Level broken": signal.prior_high,
            "Volume ×": signal.volume_ratio,
            "Close in bar": signal.close_position,
            "Stop": signal.stop_loss,
            "Risk %": signal.risk_percent,
            "Turnover EGP/day": signal.turnover_egp,
            "ATR %": signal.atr_percent,
        })
    st.dataframe(
        pd.DataFrame(rows), hide_index=True, width="stretch",
        column_config={
            "Ticker": st.column_config.TextColumn(width="small", pinned=True),
            "Company": st.column_config.TextColumn(width="medium"),
            "Close": st.column_config.NumberColumn(format="%.3f", width="small"),
            "Level broken": st.column_config.NumberColumn(
                format="%.3f", width="small",
                help=f"The highest high of the previous "
                     f"{cfg.breakout_window} sessions, excluding today — "
                     f"otherwise every close breaks out of itself.",
            ),
            "Volume ×": st.column_config.NumberColumn(
                format="%.1f×", width="small",
                help=f"Against its own {cfg.turnover_window}-day average, which "
                     f"includes today. Swept 1.5 to 4.0: the validation lift is "
                     f"+1.01%, +1.25%, +1.92%, +1.41%, +2.45%.",
            ),
            "Close in bar": st.column_config.ProgressColumn(
                min_value=0.0, max_value=1.0, format="%.2f",
                help=f"Where the close sat in the day's range. The gate is "
                     f"{cfg.minimum_close_position:.2f}: a breakout that faded "
                     f"into the close is not a breakout that held.",
            ),
            "Stop": st.column_config.NumberColumn(
                format="%.3f", width="small",
                help=f"The {cfg.stop_window}-bar low less "
                     f"{cfg.stop_atr_buffer} ATR. Deliberately wide — a stop at "
                     f"1.5 ATR turned the measured lift negative.",
            ),
            "Risk %": st.column_config.NumberColumn(
                format="%.1f%%", width="small",
                help="Distance from this close to the stop. It is large by "
                     "design; the position size is what keeps the risk small.",
            ),
            "Turnover EGP/day": st.column_config.NumberColumn(
                format="localized", width="small"),
            "ATR %": st.column_config.NumberColumn(
                format="%.2f%%", width="small",
                help=f"Below the name's own {cfg.calm_window}-bar median, which "
                     f"is the condition. Calm relative to itself, not to peers.",
            ),
        },
    )

    st.info(
        f"**الخطة · The plan for every row above.** Buy at the **next "
        f"session's close** — the signal is a close, so it is not knowable "
        f"until after this one. Hold **{cfg.holding_bars} sessions** or until "
        f"the stop, whichever comes first. There is no target: seven exit rules "
        f"were measured and every one that capped the upside earned less.",
        icon="🧭",
    )
    st.warning(
        f"**ليست توصية · Not a recommendation.** The measured median trade is "
        f"{MEASURED['median_trade']:+.2f}% and "
        f"{MEASURED['win_rate']:.0f}% of trades are profitable, so close to half "
        f"of the rows above will lose. The stop is wide by design; **position "
        f"size** is what makes that survivable, and the sizing assumes "
        f"{cfg.max_open_positions} positions of "
        f"{cfg.risk_percent:g}% risk each — take fewer, larger ones and the "
        f"arithmetic behind these figures no longer holds.",
        icon="⚠️",
    )


def _show_funnel(result) -> None:
    section_header(
        "أين توقف كل سهم · Where each symbol stopped",
        "The first condition each name failed, in the order the rule checks "
        "them — so a row says the most fundamental thing that was wrong, not "
        "the last thing tested.",
    )
    rows = [
        {"الشرط · Condition": GATE_LABELS.get(reason, reason), "Symbols": count}
        for reason, count in result.funnel.items() if count
    ]
    rows.append({"الشرط · Condition": "✅ اجتاز الكل · Passed everything",
                 "Symbols": result.count})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _show_plan(cfg) -> None:
    with st.expander("كيف تُنفَّذ · How a signal is executed", expanded=False):
        st.markdown(f"""
| | |
| --- | --- |
| **الدخول · Entry** | The **next session's close**. The signal is a close, so it cannot be bought on its own bar. The open is never used: in this project's data it is carried forward from the previous close on 96–98% of bars and is not a price anybody traded at. |
| **الوقف · Stop** | The {cfg.stop_window}-bar low less {cfg.stop_atr_buffer} ATR, fixed for the life of the trade. It does not trail. |
| **الخروج · Exit** | The stop, or {cfg.holding_bars} sessions, whichever comes first. |
| **الهدف · Target** | None. Deliberately. |
| **الحجم · Size** | Risk-based: the stop is wide, so the position is small. That is the mechanism, and it is why widening the stop did not raise the risk taken. |

**لماذا وقف واسع · Why the stop is wide.** Stop distance was swept with
everything else held fixed, and the response is monotone:

| stop | risk per share | lift, 2016–2022 | lift, 2023–2026 | years with negative lift |
| --- | --: | --: | --: | --: |
| 1.5 ATR | 5.5% | +1.48% | **−1.36%** | 4 of 10 |
| 2.5 ATR | 9.2% | +3.09% | **−0.47%** | 4 of 10 |
| 4 ATR | 14.7% | +3.27% | +1.41% | 3 of 10 |
| **under the base** | **20.4%** | **+3.85%** | **+1.92%** | **2 of 10** |
| none at all | — | +4.01% | +2.57% | 0 of 10 |

A stop inside this market's daily noise is a fee, not a protection. The same
finding appears in the strategy on the Daily Dashboard from the other side:
its EMA20 trailing stop ends **390 of its 484 trades** at an average of
**−0.47%**.

A stop is still kept rather than removed. "None at all" measures better and is
not a risk policy: it is an undefined loss on one position, and a panel of 190
symbols that all still exist cannot price the one that goes to zero.
""")


def _show_basis(cfg) -> None:
    with st.expander("على أي أساس · What this rests on", expanded=False):
        st.markdown(f"""
**الشروط السبعة · The seven conditions.** A close above the highest high of the
previous **{cfg.breakout_window}** sessions, on at least
**{cfg.minimum_volume_ratio:g}×** its own {cfg.turnover_window}-day average
volume, closing in the top **{(1 - cfg.minimum_close_position) * 100:.0f}%** of
that day's range, in a name above its own **EMA200**, whose ATR% is below its
own **{cfg.calm_window}-bar median**, trading at least
**{cfg.minimum_turnover_egp:,.0f} EGP** a day, with no session in the recent
window outside EGX's daily price limit.

**كل عتبة مقيسة · Every threshold was swept**, one notch either side, with
everything else fixed. The lift stays positive across every sweep in both eras.
The holding cap is the one threshold with a real interior peak, and it sits on
20 sessions: 10/15/**20**/25/30/40 bars give a validation lift of
+1.10/+1.22/**+1.92**/+0.92/+0.41/−0.25%.

Walked forward — each year judged only by whether the rule had been working
through the end of the prior year — the out-of-sample lift is **+2.41% per trade
over 929 signals**. Two of the eight years were slightly negative (2022 at
−0.21%, 2026 so far at −0.07%). It is not a machine.

**مقابل الاستراتيجية المجاورة · Against the Daily Dashboard strategy**, same
universe, same costs, same simulator, same capital — and each at the portfolio
policy measured for it. Held at the Daily Dashboard's own 2%/10%/10 instead,
this rule returns +113.1% at a 14.2% drawdown and Sharpe 1.28: still ahead on
every line, by less.

| | Daily Dashboard | This rule |
| --- | --: | --: |
| Trades | 484 | {MEASURED['trades']} |
| Win rate | 39.9% | {MEASURED['win_rate']:.1f}% |
| Profit factor | 1.29 | {MEASURED['profit_factor']:.2f} |
| Total return | +60.1% | +{MEASURED['total_return']:.1f}% |
| Max drawdown | 18.3% | {MEASURED['max_drawdown']:.1f}% |
| Sharpe | 0.55 | {MEASURED['sharpe']:.2f} |
| Median trade | −0.64% | {MEASURED['median_trade']:+.2f}% |
| Best 5 trades as a share of net profit | 108% | {MEASURED['top5_share']:.0f}% |

**سعة المحفظة · Portfolio capacity, changed 2026-08-29.** The limits above are
**1% risk per position, 15% total heat, 15 positions**. They were 2% / 10% / 10,
which produced a cap of *five* — `min(max_open_positions, heat ÷ risk)`, not the
10 the file appeared to say — and refused 628 of {MEASURED['signals']} signals.
Widening it was measured, not preferred: the refused trades were no worse than
the taken ones (t = −0.89), so the cap was rationing at random, and the worst
single session **fell** from −10.1% to −5.0% because concentration, not capacity,
produces the tail day. It now takes
{100 * MEASURED['trades'] // MEASURED['signals']}% of the signal stream instead
of 32%.

**Drawdown, honestly.** Both figures above are **marked to market**: the account
is valued every session, so a position that is open and losing shows up. Until
2026-08-29 `backtesting/equity.py` booked profit only when a trade closed and
never valued an open position, which understated every drawdown this project
published by a median of 1.55 points — this rule's read
{MEASURED['max_drawdown_closed']:.1f}% on that basis and the Daily Dashboard
strategy's read 16.2%. Fixed, with all 25 archived runs re-priced and no shipped
decision reversed.

**وما لا يُدّعى · What is not being claimed.** Over the same window the median
EGX name returned **+{MEASURED['benchmark_median']:.0f}%** simply held, against
this rule's **+{MEASURED['total_return']:.0f}%** at 32% exposure. Both are
nominal EGP across a decade in which the currency lost most of its value. The
claim is that this rule **selects** better than the one beside it — measured as
lift over owning the same liquid names on the same days,
**+{MEASURED['lift_train']:.2f}%** in 2016–2022 and
**+{MEASURED['lift_valid']:.2f}%** in 2023–2026 — not that it beats owning the
market.

**علاقتها بصفحة Swing Breakout · Its relation to the Swing Breakout page.**
They are close relatives and that is the point: two independent passes over this
market landed on the same trigger. Swing Breakout adds a cross-sectional
momentum rank; this one adds the close-position gate, the calm gate, a stop, and
the price-integrity guard, and it is scored through the portfolio simulator
rather than listed as candidates. Measured side by side on identical terms in the research
harness, Swing Breakout's rule lifts +0.29% / +1.92% across the two eras on 575
trades and this one +4.37% / +2.45% on 884. Measured instead by importing the
shipped module directly, this one gives +3.85% / +1.92% on 953 trades with two
negative-lift years of ten — the same conclusion, slightly less flattering, and
the figures quoted everywhere else on this page.

**حد السعر اليومي · The price-limit guard matters more than it looks.** This
project backtests on **unadjusted** prices, so a split arrives as a collapse and
a reverse split as a spike. Ninety-nine sessions of 382,646 move more than ±30%
— impossible under EGX's daily limit — and untreated they were the entire tail
of the result in both directions, including a −67.8% "loss" and a +78,117 EGP
"win" that were both corporate actions. The guard is symmetric and it costs the
headline return more than it saves.

**القيود · Limits.** One dataset, one universe, ~190 symbols that still exist —
delisted names are absent from the strategy and the benchmark alike, which
flatters both. Capacity still binds: 293 of {MEASURED['signals']} signals are
turned away because fifteen positions are already open. Nothing here is a
recommendation to trade.

Re-derive any of it with `scripts/research/breakout_candidate.py`,
`breakout_robustness.py`, `against_swing_breakout.py` and
`compare_strategies.py`. Full write-up in
`docs/audits/strategies/CONFIRMED_VOLUME_BREAKOUT.md`.
""")
