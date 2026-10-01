"""رادار T+0 · how far each name is likely to move tomorrow, and why it is on the list.

Built after the close from the record the live path reads. For every name that
clears the gates it forecasts **the size of the next session's range** -- high to
low, as a percent of today's close -- and ranks by it. **It does not say which way
any of them will move**: replayed over 500 sessions, nothing on the radar told
which names would close up (docs/audits/strategies/T0_RADAR_ACCURACY.md).

Why this ranking, measured on that replay (2024-09-08 .. 2026-09-29), with each
half judged on its own:

* the mean range of the last five sessions ranked the next session's range
  better than every alternative tried -- rank correlation 0.586 and 0.531 in
  the two halves, against 0.455 and 0.430 for the first version's 0-100 score,
  which mixed in turnover and expansion and was worse for it;
* dividing by the cost, or blending the old components back in, lowered it.
  The cost is still applied -- as a gate, where it decides who is listed.

The forecast is that mean times a measured ratio, because a name that ranks
first has had an unusually wide week and narrows the next day: for the first ten
the next range came in at 0.735x the five-session mean at the median (0.545x at
the lower quartile, 1.015x at the upper), steady across both halves; for the
rest of the list at 0.880x (0.650x .. 1.215x). The band shown is that quartile
range -- half the time the next session lands inside it.

The two inputs that decide whether a day trade can pay at all are measured, not
assumed: the round trip is each symbol's own -- the published commission plus
the crossing cost banked by ``scripts/bank_effective_cost.py`` -- and a symbol
nobody measured is refused rather than filled with a universe median.
Same-session eligibility comes from ``core.sector_context``; while that list is
empty nothing is filtered on it, and the reader checks it.

Every gate and constant is below, and every reason on the page is written here
from the same numbers, so a sentence and a column cannot disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

RULE_VERSION = "t0-radar-v2"

#: History a symbol needs before its typical range means anything.
MIN_SESSIONS = 60

#: "Typical" is the median range of the twenty traded sessions before the last.
BASE_WINDOW = 20

#: The ranking: the mean range of the last this-many sessions, today included.
RECENT_WINDOW = 5

#: Average daily turnover over the last twenty sessions, a session without a
#: trade counted as zero. Twenty million is the floor that took the gap study's
#: selection from +0.55% to +0.74% net (GAP_PREDICTABILITY.md), the one sweep of
#: liquidity on a same-session question here.
MIN_TURNOVER_EGP = 20_000_000

#: The typical range must cover the round trip this many times to be listed.
MIN_ROOM_MULTIPLE = 3.0

#: A session "had room" when its range was at least twice the round trip.
ROOM_DAY_MULTIPLE = 2.0

#: EGX stops a stock at +/-20% of its reference, and a stock that does not trade
#: keeps its reference, so a larger move between two traded bars did not trade:
#: it is a split or bonus issue the record has not adjusted (POUL 2026-09-27,
#: EFID 2026-09-29). Every input here would measure the adjustment.
DAILY_LIMIT_PERCENT = 20.5
CORPORATE_ACTION_WINDOW = 60

#: A market session is a date at least this many symbols traded on.
SESSION_QUORUM = 50

DEFAULT_TOP = 10

#: Next range / five-session mean, measured over 500 sessions: (lower quartile,
#: median, upper quartile). The first ten narrow more than the rest because
#: ranking first selects an unusually wide week.
FORECAST_TOP = (0.545, 0.735, 1.015)
FORECAST_REST = (0.650, 0.880, 1.215)

#: What the replay found, for the page. Re-run the accuracy script to refresh.
ACCURACY = {
    "window": "500 جلسة من سبتمبر 2024 لسبتمبر 2026",
    "top_next_range_median": 5.1,
    "rest_next_range_median": 2.8,
    "top_over_5x_cost": 0.75,
    "top_over_3x_cost": 0.96,
    "top_closed_up": 0.42,
    "all_closed_up": 0.45,
}

#: In the order they are applied. Each refused symbol is counted once, by the
#: first gate that turned it away, so the counts partition the universe.
GATES = (
    ("Unreadable", "مش متقري · unreadable"),
    ("InsufficientHistory", f"تاريخ أقل من {MIN_SESSIONS} جلسة · insufficient history"),
    ("NotT0", "مش على قائمة T+0 · not same-session eligible"),
    ("NotTradedLastSession", "مااتداولش آخر جلسة · no trade last session"),
    ("CorporateAction", "حركة أكبر من حد الـ 20% · unadjusted corporate action"),
    ("OnePriceSession", "جلسة بسعر واحد · one-price session"),
    ("Liquidity", f"سيولة أقل من {MIN_TURNOVER_EGP / 1e6:g} مليون · liquidity"),
    ("CostUnmeasured", "تكلفة الصفقة مش مقاسة · cost unmeasured"),
    ("NoRoom", f"مداه أقل من {MIN_ROOM_MULTIPLE:g} أضعاف التكلفة · no room for the cost"),
)

HEADER_NOTE = (
    "مساعد بعد الإغلاق، مش توصية. لكل سهم بيتوقع حجم حركة الجلسة الجاية (المدى من "
    "أعلى لأقل) ومعاه النطاق اللي الحقيقة بتقع فيه نص الأيام، ومابيتوقعش الاتجاه. "
    f"على {ACCURACY['window']}، أول 10 اتحركوا {ACCURACY['top_next_range_median']}% "
    f"في الجلسة اللي بعدها مقابل {ACCURACY['rest_next_range_median']}% للباقي، و"
    f"{ACCURACY['top_over_5x_cost']:.0%} منهم تحركوا أكتر من 5 أضعاف تكلفة الصفقة. "
    f"لكن قفلوا طالعين {ACCURACY['top_closed_up']:.0%} من الأيام بس، مقابل "
    f"{ACCURACY['all_closed_up']:.0%} لكل الأسهم."
)


# --- one symbol --------------------------------------------------------------


def _atr(frame, window=14):
    from ta.volatility import AverageTrueRange

    if len(frame) <= window:
        return np.nan
    value = AverageTrueRange(high=frame["High"], low=frame["Low"], close=frame["Close"],
                             window=window).average_true_range().iloc[-1]
    return float(value) if value and value > 0 else np.nan


def assess(symbol, frame, turnover, sessions, round_trip, t0_status, t0_enforced):
    """One symbol's row: the first gate that refused it, or its measurements.

    ``turnover`` is the symbol's reported turnover by session (EGP), ``sessions``
    the market's calendar, ``round_trip`` its measured cost in percent or None.
    Pure: everything it reads is passed in. The forecast and the reasons depend
    on the name's rank, so ``finish`` adds them once the list is ordered.
    """

    row = {"symbol": symbol, "gate": None, "t0_status": t0_status}
    if frame is None or not len(frame):
        row["gate"] = "Unreadable"
        return row
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    close, high, low = frame["Close"], frame["High"], frame["Low"]
    if len(frame) < MIN_SESSIONS:
        row["gate"] = "InsufficientHistory"
        return row
    if t0_enforced and t0_status != "ELIGIBLE":
        row["gate"] = "NotT0"
        return row
    if frame.index[-1] != sessions[-1]:
        row["gate"] = "NotTradedLastSession"
        return row

    moves = (close.pct_change() * 100).iloc[-CORPORATE_ACTION_WINDOW:]
    if (moves.abs() > DAILY_LIMIT_PERCENT).any():
        row["gate"] = "CorporateAction"
        row["corporate_action"] = "; ".join(
            f"{v:+.1f}% {d.date().isoformat()}"
            for d, v in moves[moves.abs() > DAILY_LIMIT_PERCENT].items())
        return row

    ranges = (high - low) / close.shift(1) * 100
    range_today = float(ranges.iloc[-1])
    if not range_today > 0:
        row["gate"] = "OnePriceSession"
        return row

    turnover = turnover[~turnover.index.duplicated(keep="last")]
    on_calendar = turnover.reindex(sessions[-(BASE_WINDOW + 1):]).fillna(0.0)
    turnover_today = float(on_calendar.iloc[-1])
    average_prior = float(on_calendar.iloc[:-1].mean())
    average_recent = float(on_calendar.iloc[1:].mean())
    row.update(turnover_today_egp=turnover_today, average_turnover_egp=average_recent)
    if average_recent < MIN_TURNOVER_EGP:
        row["gate"] = "Liquidity"
        return row

    if round_trip is None or not np.isfinite(round_trip) or round_trip <= 0:
        row["gate"] = "CostUnmeasured"
        return row

    base = ranges.iloc[-(BASE_WINDOW + 1):-1].dropna()
    median_range = float(base.median())
    row.update(median_range_pct=median_range, round_trip_pct=float(round_trip),
               room_multiple=median_range / round_trip)
    if median_range / round_trip < MIN_ROOM_MULTIPLE:
        row["gate"] = "NoRoom"
        return row

    c = float(close.iloc[-1])
    h, l = float(high.iloc[-1]), float(low.iloc[-1])
    recent = float(ranges.iloc[-RECENT_WINDOW:].mean())
    row.update(
        score=recent,
        recent_range_pct=recent,
        close=c,
        change_pct=float((c / close.iloc[-2] - 1) * 100),
        close_position=(c - l) / (h - l) if h > l else np.nan,
        dist_sma20_pct=(c / float(close.iloc[-20:].mean()) - 1) * 100,
        range_today_pct=range_today,
        relative_turnover=turnover_today / average_prior if average_prior > 0 else np.nan,
        room_days=int((base > ROOM_DAY_MULTIPLE * round_trip).sum()),
        room_days_window=int(len(base)),
        high_today=h, low_today=l,
        high_20=float(high.iloc[-20:].max()), low_20=float(low.iloc[-20:].min()),
        atr14=_atr(frame),
    )
    return row


def forecast(recent_range, rank, top=DEFAULT_TOP):
    """(low, middle, high) of the next session's range, in percent."""

    ratios = FORECAST_TOP if rank <= top else FORECAST_REST
    return tuple(recent_range * r for r in ratios)


def finish(candidates):
    """Order the list, then add each name's forecast and its reasons."""

    if not len(candidates):
        return candidates
    ordered = candidates.sort_values(["score", "turnover_today_egp"],
                                     ascending=[False, False]).reset_index(drop=True)
    ordered.insert(0, "rank", range(1, len(ordered) + 1))
    rows = []
    for _, row in ordered.iterrows():
        row = row.to_dict()
        low, mid, high = forecast(row["recent_range_pct"], row["rank"])
        row.update(forecast_low_pct=low, forecast_pct=mid, forecast_high_pct=high,
                   forecast_cost_multiple=mid / row["round_trip_pct"],
                   forecast_move_egp=row["close"] * mid / 100)
        row["reasons"], row["cautions"], row["context"] = explain(row)
        rows.append(row)
    return pd.DataFrame(rows)


def explain(row):
    """Why a name is on the list, what to check, and where it closed.

    The reasons are the forecast and what it rests on; the cautions are what a
    reader should look at before acting; the context is direction, shown and
    never used.
    """

    reasons = [
        f"المدى المتوقع الجلسة الجاية حوالي {row['forecast_pct']:.1f}% "
        f"(نص الأيام بيقع بين {row['forecast_low_pct']:.1f}% و{row['forecast_high_pct']:.1f}%)",
        f"يعني حوالي {row['forecast_cost_multiple']:.1f} ضعف تكلفة الصفقة "
        f"({row['round_trip_pct']:.2f}%)، أو {row['forecast_move_egp']:,.2f} جنيه في السهم",
        f"متوسط مداه آخر {RECENT_WINDOW} جلسات {row['recent_range_pct']:.1f}%، "
        f"والمعتاد في {BASE_WINDOW} جلسة {row['median_range_pct']:.1f}%",
    ]
    rel = row.get("relative_turnover")
    if rel is not None and np.isfinite(rel) and rel >= 1.2:
        reasons.append(f"اتداول آخر جلسة بـ {row['turnover_today_egp'] / 1e6:,.0f} مليون جنيه، "
                       f"{rel:.1f} ضعف متوسطه")
    # Counts come back as floats once a row has passed through a frame.
    reasons.append(f"في {int(row['room_days'])} من آخر {int(row['room_days_window'])} جلسة "
                   f"كان مداه أكبر من ضعف التكلفة")
    if row.get("t0_status") == "ELIGIBLE":
        reasons.append("على قايمة التداول في نفس الجلسة (T+0)")

    # Same-session eligibility is the reader's to check -- the owner keeps it,
    # and said so on 2026-10-01 -- so an unknown status stays in its column.
    cautions = []
    if rel is not None and np.isfinite(rel) and rel < 0.8:
        cautions.append(f"سيولة آخر جلسة أقل من المعتاد ({rel:.1f}×)")
    if abs(row.get("change_pct") or 0) >= 9.5:
        cautions.append(f"حركة كبيرة آخر جلسة ({row['change_pct']:+.1f}%)")

    position = row.get("close_position")
    where = (f"قفل عند {position:.0%} من مداها، "
             if position is not None and np.isfinite(position) else "")
    context = (f"آخر جلسة {row['change_pct']:+.1f}%، {where}"
               f"{row['dist_sma20_pct']:+.1f}% عن متوسط 20 جلسة")
    return reasons, cautions, context


# --- the universe ------------------------------------------------------------


def market_sessions(histories, quorum=SESSION_QUORUM):
    """The exchange's sessions, from the histories themselves."""

    if not histories:
        return pd.DatetimeIndex([])
    counts = pd.Series(np.concatenate(
        [pd.DatetimeIndex(f.index).values for f in histories.values() if len(f)])).value_counts()
    return pd.DatetimeIndex(sorted(counts[counts >= quorum].index))


def _reported_turnover(symbol):
    from sector_flow import measured_turnover

    frame = measured_turnover.frame_for(f"{symbol}.CA")
    if frame is None or "Turnover" not in frame:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame["Turnover"], errors="coerce").dropna()


@dataclass
class RadarResult:
    session_date: str | None
    candidates: pd.DataFrame
    funnel: dict
    provenance: dict = field(default_factory=dict)
    refused: pd.DataFrame = field(default_factory=pd.DataFrame)


def build(histories=None, *, turnover_of=None, costs=None, eligibility_of=None,
          t0_provenance=None, on_error=None):
    """The radar for the last session in the record. Every input is injectable."""

    from core import effective_cost

    failures = {}

    def failed(symbol, reason):
        # Kept here as well as passed on: a symbol the router refused is still
        # part of the universe, and the funnel has to count it.
        failures.setdefault(symbol, reason)
        if on_error is not None:
            on_error(symbol, reason)

    if histories is None:
        from services.swing_breakout import load_universe_histories

        histories = load_universe_histories(on_error=failed)
    turnover_of = turnover_of or _reported_turnover
    if costs is None:
        costs = effective_cost.load_symbol_costs()
    if eligibility_of is None or t0_provenance is None:
        from core.sector_context import intraday_eligibility, intraday_eligibility_provenance

        eligibility_of = eligibility_of or intraday_eligibility
        t0_provenance = t0_provenance or intraday_eligibility_provenance()
    t0_enforced = bool(t0_provenance.get("available"))

    sessions = market_sessions(histories)
    rows = []
    for symbol in sorted(set(histories) | set(failures)):
        frame = histories.get(symbol)
        record = costs.get(str(symbol).upper())
        round_trip = None if record is None else record.round_trip_percent
        status = eligibility_of(symbol)
        try:
            rows.append(assess(symbol, frame, turnover_of(symbol) if frame is not None
                               else pd.Series(dtype=float),
                               sessions, round_trip, status, t0_enforced))
        except Exception as error:                          # noqa: BLE001 - one name, not the list
            rows.append({"symbol": symbol, "gate": "Unreadable", "t0_status": status,
                         "error": f"{type(error).__name__}: {error}"})
            continue
        if symbol in failures:
            rows[-1]["error"] = failures[symbol]

    table = pd.DataFrame(rows)
    funnel = {gate: int((table["gate"] == gate).sum()) for gate, _ in GATES}
    passed = finish(table[table["gate"].isna()].copy())
    if len(passed):
        try:
            from core.universe import company_name

            passed.insert(2, "name", [company_name(s) for s in passed["symbol"]])
        except Exception:                                   # noqa: BLE001 - display only
            passed.insert(2, "name", "")
    session = sessions[-1].date().isoformat() if len(sessions) else None
    cost_through = max((c.last_session for c in costs.values()), default=None)
    provenance = {
        "rule_version": RULE_VERSION,
        "symbols_considered": int(len(table)),
        "t0_enforced": t0_enforced,
        "t0_eligible_count": int(t0_provenance.get("eligible_count") or 0),
        "cost_measured_through": cost_through,
        "cost_symbols": len(costs),
    }
    return RadarResult(session, passed, funnel, provenance,
                       table[table["gate"].notna()].reset_index(drop=True))


# --- the saved list ----------------------------------------------------------

CSV_COLUMNS = (
    "rank", "symbol", "name", "forecast_pct", "forecast_low_pct", "forecast_high_pct",
    "forecast_cost_multiple", "forecast_move_egp", "score", "recent_range_pct",
    "median_range_pct", "round_trip_pct", "room_multiple", "room_days", "room_days_window",
    "t0_status", "close", "change_pct", "close_position", "dist_sma20_pct",
    "range_today_pct", "turnover_today_egp", "average_turnover_egp", "relative_turnover",
    "high_today", "low_today", "high_20", "low_20", "atr14", "reasons", "cautions",
    "context",
)

#: Joins a list of sentences in one CSV cell, and splits it back on the page.
SEPARATOR = " | "


def out_dir():
    return Path(__file__).resolve().parents[1] / "reports" / "t0_radar"


def as_frame(result):
    frame = result.candidates.copy()
    for column in ("reasons", "cautions"):
        if column in frame:
            frame[column] = frame[column].map(
                lambda items: SEPARATOR.join(items) if isinstance(items, list) else items)
    frame["session_date"] = result.session_date
    # Carried on every row so a saved list states its own basis, whether or not
    # its session ever reached the forward record.
    frame["cost_measured_through"] = (result.provenance or {}).get("cost_measured_through")
    frame["rule_version"] = (result.provenance or {}).get("rule_version", RULE_VERSION)
    columns = ([c for c in CSV_COLUMNS if c in frame.columns]
               + ["session_date", "cost_measured_through", "rule_version"])
    return frame[columns]


def write_csv(result, directory=None):
    """The list, named for the day it was made and the session it read."""

    directory = Path(directory) if directory else out_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    path = directory / f"t0_radar_{stamp}_{result.session_date or 'unknown'}.csv"
    as_frame(result).round(4).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def available_csvs(directory=None):
    directory = Path(directory) if directory else out_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob("t0_radar_*.csv"), reverse=True)


def dates_from_name(path):
    """(run date, session date) from ``t0_radar_YYYYMMDD_YYYY-MM-DD.csv``."""

    parts = Path(path).stem.split("_")
    if len(parts) != 4:
        return None, None
    run, session = parts[2], parts[3]
    try:
        run = datetime.strptime(run, "%Y%m%d").date().isoformat()
        session = datetime.strptime(session, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None, None
    return run, session


def split_sentences(cell):
    if not isinstance(cell, str) or not cell.strip():
        return []
    return [part.strip() for part in cell.split(SEPARATOR) if part.strip()]
