"""One read of the whole portfolio: prices, plans, and what to do about each.

This is the only module here that touches a provider, a database or a clock.
``book``, ``plan`` and ``rules`` stay pure so their arithmetic can be checked;
everything that can fail at runtime -- a locked collector database, an absent
sector file, a symbol with no history -- fails here, per position, and never
takes the rest of the page down with it.

Price selection is deliberate and narrow:

* A **live** price is used only when the Rubix overlay reports itself FRESH.
  That classification is the provider's own, made against the exchange clock,
  and is not re-derived here.
* Otherwise the **last completed session's close** is used, labelled as such,
  with the reason the live quote was rejected.
* When neither exists there is **no price**, and the rules withhold. An empty
  cell is a fact; a stale price rendered as if it were current is not.

The two liquidity readings are different in kind and are kept apart. The
stock's relative volume is measured on *completed* daily bars -- the same
definition ``decision_support`` uses -- because today's partial volume is not
comparable to a full session's average until the session ends. The sector's
intraday flow is a real intraday measurement, from the machinery built for
exactly that question, and is used as such.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.level_status import BASIS_COMPLETED_CLOSE, BASIS_LIVE
from core.sector_context import sector_context
from decision_support.quality import relative_volume
from holdings.book import Book, FeeModel, load_fee_model, value_position
from holdings.plan import ExitPlan, ExitPolicy, build_plan, load_policy, plan_differs
from holdings.rules import Evidence, Recommendation, evaluate

#: The lookback every relative-volume figure in this project uses.
RVOL_LOOKBACK = 20

#: Where the sector names that match ``sector_flow`` history come from.
SECTOR_FILE = "data/sectors.csv"

NO_PRICE = "NO_PRICE"


# --------------------------------------------------------------------------- #
# Default providers -- all best-effort, all replaceable in tests
# --------------------------------------------------------------------------- #

def engine_symbol(symbol) -> str:
    """The ``.CA`` form the loaders and the sector file are keyed by."""

    return f"{str(symbol).strip().upper()}.CA"


def default_history_loader(symbol):
    """Completed daily bars with indicators, or ``None``.

    Uses the dashboard provider purpose, so the portfolio sees exactly the
    candles the rest of the dashboard sees.
    """

    from core.data_provider import load_history
    from indicators.technical import calculate_indicators

    # A provider refusal is not swallowed: "GOUR has 131 bars against the 250
    # the indicators require" is the difference between a position the user can
    # explain and one that silently has no plan beside it.
    frame = load_history(engine_symbol(symbol), purpose="dashboard")
    if frame is None or frame.empty:
        return None
    try:
        return calculate_indicators(frame)
    except Exception:                                            # noqa: BLE001
        return None


def default_quote_loader(symbol):
    """``None``: no live quote exists since the Rubix feed was retired on 2026-09-10."""

    return None


def default_quote_overlays(symbols):
    """``{}``: no held symbol has a live quote.

    This read every held symbol's latest Rubix quote. The feed was retired on
    2026-09-10, so each of those quotes is from that day. ``select_price`` never
    took one as live -- it requires FRESH -- and the database is not opened.
    """

    return {}


def default_sector_strengths():
    """``{sector: strength}`` from the completed daily sector history."""

    try:
        from sector_flow.strength import load_latest_strength

        return load_latest_strength()
    except Exception:                                            # noqa: BLE001
        return {}


def default_sector_map():
    try:
        from decision_support.sector_analysis import load_sector_map

        return load_sector_map(SECTOR_FILE)
    except Exception:                                            # noqa: BLE001
        return {}


def default_sector_intraday():
    """``{}``: no sector forecast for the rest of today.

    The forecast blended today's opening window, read from the Rubix minute
    store, with the previous session. That store stopped at 14:18 on 2026-09-10
    when the feed was retired, so there is no opening window to blend.
    """

    return {}


# --------------------------------------------------------------------------- #
# Price selection
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SelectedPrice:
    """One price, with where it came from and why anything fresher was refused."""

    value: float | None = None
    basis: str = NO_PRICE
    reason: str = ""
    provider: str = ""
    timestamp: str = ""
    spread_percent: float | None = None
    freshness: str = ""
    session_phase: str = ""

    @property
    def live(self) -> bool:
        return self.basis == BASIS_LIVE


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def completed_close(frame):
    """``(close, session date, provider)`` from the last completed daily bar."""

    if frame is None or not len(frame):
        return None, "", ""
    close = _finite(frame["Close"].iloc[-1])
    try:
        session_date = str(frame.index[-1].date())
    except AttributeError:
        session_date = str(frame.index[-1])
    provider = ""
    if hasattr(frame, "attrs"):
        provider = str(frame.attrs.get("market_data", {}).get("provider", ""))
    return close, session_date, provider


def select_price(quote, frame=None, *, close=None, session_date="",
                 provider="") -> SelectedPrice:
    """Live if the overlay says FRESH, else the last completed close.

    The fallback close may be passed directly rather than as a frame, because
    a page visit that reuses a stored plan never loads one. Which price is
    chosen, and why the other was refused, is decided identically either way.
    """

    quote = quote if isinstance(quote, dict) else {}
    last = _finite(quote.get("last"))
    freshness = str(quote.get("freshness") or "").upper()
    phase = str(quote.get("session_phase") or "")
    spread = _finite(quote.get("spread_percent"))
    warning = str(quote.get("freshness_warning") or "")

    if last is not None and last > 0 and freshness == "FRESH":
        return SelectedPrice(
            value=last, basis=BASIS_LIVE, provider="rubix",
            timestamp=str(quote.get("quote_timestamp") or ""),
            spread_percent=spread, freshness=freshness, session_phase=phase,
            reason="Rubix quote is fresh",
        )

    if frame is not None:
        close, session_date, provider = completed_close(frame)
    close = _finite(close)

    if close is not None and close > 0:
        return SelectedPrice(
            value=close, basis=BASIS_COMPLETED_CLOSE, provider=str(provider or ""),
            timestamp=str(session_date or ""), spread_percent=spread,
            freshness=freshness, session_phase=phase,
            reason=warning or (
                "no live quote" if not last else f"live quote is {freshness or 'unusable'}"
            ),
        )

    return SelectedPrice(reason=warning or "no live quote and no completed close",
                         freshness=freshness, session_phase=phase)


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PositionView:
    """Everything the page shows for one holding."""

    position: object
    price: SelectedPrice
    value: object = None
    plan: ExitPlan | None = None
    recommendation: Recommendation | None = None
    sector: str = ""
    sector_strength: float | None = None
    sector_intraday_change: float | None = None
    relative_volume: float | None = None
    holding_sessions: int | None = None
    partial_taken: bool = False
    plan_version: int | None = None
    #: True when the plan came from the store without loading daily data.
    plan_reused: bool = False
    error: str = ""

    @property
    def symbol(self) -> str:
        return self.position.symbol

    @property
    def market_value_egp(self):
        return self.value.market_value_egp if self.value else None


@dataclass(frozen=True)
class PortfolioView:
    """The whole account at one moment."""

    generated_at: str
    book: Book
    fee_model: FeeModel
    policy: ExitPolicy
    positions: tuple = ()
    warnings: tuple = ()
    sector_exposure: dict = field(default_factory=dict)

    @property
    def valued(self) -> list:
        return [item for item in self.positions if item.value is not None]

    @property
    def market_value_egp(self) -> float:
        return sum(item.value.market_value_egp for item in self.valued)

    @property
    def unrealized_net_egp(self) -> float:
        return sum(item.value.unrealized_net_egp for item in self.valued)

    @property
    def total_equity_egp(self) -> float:
        """Positions at market plus cash. Unpriced holdings count at cost.

        Counting an unpriced holding at zero would understate the account, and
        counting it at market is impossible; cost is the only number that is
        actually known about it. The count of unpriced positions is surfaced
        beside the total so the figure is never read as fully marked.
        """

        unpriced = sum(
            item.position.cost_basis_egp
            for item in self.positions if item.value is None
        )
        return self.market_value_egp + unpriced + self.book.cash_egp

    @property
    def unpriced_count(self) -> int:
        return sum(1 for item in self.positions if item.value is None)

    @property
    def exposure_percent(self):
        equity = self.total_equity_egp
        if equity <= 0:
            return None
        return (self.market_value_egp + sum(
            item.position.cost_basis_egp
            for item in self.positions if item.value is None
        )) / equity * 100.0

    def actionable(self) -> list:
        """Positions whose recommendation asks for something, most urgent first."""

        order = {"NOW": 0, "TODAY": 1, "SOON": 2, "NONE": 3}
        items = [
            item for item in self.positions
            if item.recommendation is not None and item.recommendation.actionable
        ]
        return sorted(items, key=lambda item: order.get(item.recommendation.urgency, 9))


def _holding_sessions(frame, opened_on):
    """Trading sessions since the position was opened, counted from real bars.

    Counted from the candles themselves rather than from a calendar difference,
    so holidays and closures are handled by the data instead of by arithmetic
    about them.
    """

    if frame is None or not opened_on or not len(frame):
        return None
    try:
        return int((frame.index.date >= datetime.strptime(
            opened_on, "%Y-%m-%d").date()).sum())
    except (ValueError, AttributeError, TypeError):
        return None


def expected_completed_session() -> str:
    """The EGX session the plans should be built from, or ``""`` when unknown.

    An unavailable calendar is never permission to treat a stored plan as
    current: an empty answer forces a rebuild, which is the safe direction --
    it costs a data load, while the opposite would act on levels from a session
    that has since been superseded.
    """

    try:
        from core.egx_calendar import effective_holidays
        from core.egx_session import authoritative_completed_session

        value = authoritative_completed_session(holidays=effective_holidays())
    except Exception:                                            # noqa: BLE001
        return ""
    if value is None:
        return ""
    return str(getattr(value, "isoformat", lambda: value)())[:10]


def position_fingerprint(position) -> str:
    """What must not have changed for a stored plan to still describe a holding.

    Quantity and basis move the breakeven; a partial sale moves the stop to it;
    the opening date moves the time stop. Any of them changing invalidates the
    stored plan for that symbol alone, and nothing else has to be reloaded.
    """

    return (
        f"{position.quantity:.4f}|{position.cost_basis_egp:.2f}"
        f"|{position.partial_sales}|{position.opened_on}"
    )


def plan_from_record(record, policy: ExitPolicy) -> ExitPlan:
    """Rebuild an ``ExitPlan`` from a stored row, loading no market data.

    The levels are read back exactly as they were written. They are not
    recomputed from anything, because recomputing would need the candles this
    path exists to avoid loading -- and because a plan whose numbers changed
    without a new session behind them is not the plan that was shown.
    """

    try:
        evidence = json.loads(record.get("evidence_json") or "{}")
    except (TypeError, ValueError):
        evidence = {}
    source = str(record.get("source") or "")
    stop_source, _, target_source = source.partition("|")
    return ExitPlan(
        symbol=str(record.get("symbol") or "").upper(),
        available=True,
        session_date=str(record.get("session_date") or ""),
        reference_price=evidence.get("completed_close"),
        atr=evidence.get("atr"),
        support=evidence.get("support"),
        resistance=evidence.get("resistance"),
        stop=record.get("stop"),
        stop_source=stop_source,
        structural_stop=evidence.get("structural_stop"),
        trailing_stop=evidence.get("trailing_stop"),
        breakeven=evidence.get("breakeven"),
        target_partial=record.get("target_partial"),
        target_final=record.get("target_final"),
        target_source=target_source,
        partial_fraction=record.get("partial_fraction") or policy.partial_fraction,
        holding_sessions=evidence.get("holding_sessions"),
        max_holding_sessions=policy.max_holding_sessions,
        notes=tuple(evidence.get("notes") or ()),
    )


def _plan_evidence(plan: ExitPlan, *, fingerprint, relative, new_high, close,
                   built_for="") -> dict:
    """Everything a later visit needs so it can skip the daily data entirely."""

    return {
        "built_for_session": str(built_for or ""),
        "support": plan.support,
        "resistance": plan.resistance,
        "atr": plan.atr,
        "breakeven": plan.breakeven,
        "structural_stop": plan.structural_stop,
        "trailing_stop": plan.trailing_stop,
        "completed_close": close,
        "holding_sessions": plan.holding_sessions,
        "relative_volume": relative,
        "new_twenty_day_high": new_high,
        "position_fingerprint": fingerprint,
        "notes": list(plan.notes),
    }


def _stored_evidence(record) -> dict:
    try:
        return json.loads((record or {}).get("evidence_json") or "{}")
    except (TypeError, ValueError):
        return {}


def _reusable(record, session, fingerprint) -> bool:
    """May a stored plan stand in for a freshly computed one?

    Only when all three hold: the calendar told us which session is complete,
    the plan was built while that same session was the expectation, and the
    holding has not changed since. Any doubt reloads the data -- the cost of
    being wrong is showing levels from a superseded session as today's.

    The comparison is against ``built_for_session`` -- what the calendar
    expected when the plan was built -- and NOT against the plan's own
    ``session_date``, which is the session its candles actually end on. The two
    differ all the time: a provider that has not yet published today's candle
    leaves the data a session behind the calendar, and keying on that would
    make every plan permanently unreusable and reload twenty-one histories on
    every visit -- exactly the cost this exists to avoid.

    The trade: if the provider publishes a missing candle later in the same
    session, the stored plan keeps the older levels until the next session or
    until "إعادة حساب الخطط" is pressed. That is a visible, deliberate button;
    a page that silently reloads everything is not.
    """

    if not record or not session:
        return False
    evidence = _stored_evidence(record)
    if evidence.get("built_for_session") != str(session):
        return False
    if not evidence.get("position_fingerprint"):
        return False
    return evidence["position_fingerprint"] == fingerprint


def _new_twenty_day_high(frame, window=20):
    if frame is None or len(frame) < window + 1 or "High" not in frame:
        return None
    highs = frame["High"]
    try:
        return bool(float(highs.iloc[-1]) >= float(highs.iloc[-(window + 1):-1].max()))
    except (TypeError, ValueError):
        return None


def build_position_view(
    position,
    *,
    fee_model,
    policy,
    store=None,
    history_loader=None,
    quote_loader=None,
    sector_map=None,
    sector_strengths=None,
    sector_intraday=None,
    persist=True,
    session="",
    rebuild=False,
) -> PositionView:
    """Price, plan and advise one holding. Never raises for one bad symbol.

    Daily data is loaded only when it is needed, which is once per session per
    symbol. A plan already stored for the current completed session, against an
    unchanged holding, is reused as written -- so navigating back to this page
    costs one live-quote read rather than twenty-one history loads.
    """

    history_loader = history_loader or default_history_loader
    quote_loader = quote_loader or default_quote_loader
    sector_map = sector_map if sector_map is not None else {}
    sector_strengths = sector_strengths if sector_strengths is not None else {}
    sector_intraday = sector_intraday if sector_intraday is not None else {}
    error = ""

    stored_plan = store.active_plan(position.symbol) if store else None
    fingerprint = position_fingerprint(position)
    reuse = _reusable(stored_plan, session, fingerprint) and not rebuild

    frame = None
    if not reuse:
        try:
            frame = history_loader(position.symbol)
        except Exception as failure:                             # noqa: BLE001
            frame, error = None, str(failure)[:200] or type(failure).__name__
    try:
        quote = quote_loader(position.symbol)
    except Exception:                                            # noqa: BLE001
        quote = None

    partial_taken = position.partial_sales > 0
    version = (stored_plan or {}).get("version")

    if reuse:
        stored_evidence = _stored_evidence(stored_plan)
        plan = plan_from_record(stored_plan, policy)
        sessions = plan.holding_sessions
        relative = stored_evidence.get("relative_volume")
        new_high = stored_evidence.get("new_twenty_day_high")
        price = select_price(
            quote,
            close=stored_evidence.get("completed_close"),
            session_date=plan.session_date,
            provider="stored completed session",
        )
    else:
        sessions = _holding_sessions(frame, position.opened_on)
        relative = relative_volume(frame, RVOL_LOOKBACK) if frame is not None else None
        new_high = _new_twenty_day_high(frame)
        close, close_session, close_provider = completed_close(frame)
        price = select_price(quote, close=close, session_date=close_session,
                             provider=close_provider)
        plan = build_plan(
            position.symbol, frame,
            position=position, fee_model=fee_model, policy=policy,
            previous_stop=(stored_plan or {}).get("stop"),
            partial_taken=partial_taken, holding_sessions=sessions,
        )
        if store is not None and persist and plan.available:
            evidence_payload = _plan_evidence(
                plan, fingerprint=fingerprint, relative=relative,
                new_high=new_high, close=close, built_for=session,
            )
            if plan_differs(stored_plan, plan):
                version = store.save_plan(
                    position.symbol,
                    stop=plan.stop, target_partial=plan.target_partial,
                    target_final=plan.target_final,
                    partial_fraction=plan.partial_fraction,
                    source=f"{plan.stop_source}|{plan.target_source}",
                    reason="levels recomputed from the latest completed session",
                    session_date=plan.session_date, evidence=evidence_payload,
                )
            else:
                # Same levels, new session: refresh the row's session stamp and
                # evidence in place rather than writing a version that changed
                # nothing, which would bury the revisions that meant something.
                version = store.refresh_plan(
                    position.symbol, session_date=plan.session_date,
                    evidence=evidence_payload,
                ) or version

    value = value_position(position, price.value, fee_model)

    sector = sector_map.get(engine_symbol(position.symbol), "")
    if not sector:
        # The sector map keyed to the flow history is the authority; this is a
        # display fallback only, and its names may not match a flow sector.
        sector = sector_context(engine_symbol(position.symbol)).sector_name or ""
    strength = sector_strengths.get(sector)
    intraday_change = sector_intraday.get(sector)

    evidence = Evidence(
        relative_volume=relative,
        sector=sector,
        sector_strength=strength,
        sector_intraday_change=intraday_change,
        spread_percent=price.spread_percent,
        quote_freshness=price.freshness,
        session_phase=price.session_phase,
        new_twenty_day_high=new_high,
    )

    recommendation = evaluate(
        position, plan,
        price=price.value, price_basis=price.basis, evidence=evidence,
        fee_model=fee_model, partial_taken=partial_taken,
        holding_sessions=sessions,
    )

    return PositionView(
        position=position, price=price, value=value, plan=plan,
        recommendation=recommendation, sector=sector, sector_strength=strength,
        sector_intraday_change=intraday_change,
        relative_volume=relative, holding_sessions=sessions,
        partial_taken=partial_taken, plan_version=version, error=error,
        plan_reused=reuse,
    )


def build_portfolio_view(
    store,
    *,
    fee_model: FeeModel | None = None,
    policy: ExitPolicy | None = None,
    history_loader=None,
    quote_loader=None,
    sector_map=None,
    sector_strengths=None,
    sector_intraday=None,
    persist=True,
    now=None,
    session=None,
    rebuild=False,
) -> PortfolioView:
    """Read every open position and produce the page's whole state."""

    fee_model = fee_model or load_fee_model()
    policy = policy or load_policy()
    session = expected_completed_session() if session is None else str(session or "")
    book = store.book(fee_model)
    warnings = []
    if not fee_model.rates_loaded:
        warnings.append(
            "Trading costs are not loaded: "
            f"{fee_model.load_error or 'unknown reason'}. "
            "Net figures and breakeven prices are understated."
        )

    sector_map = default_sector_map() if sector_map is None else sector_map
    sector_strengths = (
        default_sector_strengths() if sector_strengths is None else sector_strengths
    )
    sector_intraday = (
        default_sector_intraday() if sector_intraday is None else sector_intraday
    )

    views = tuple(
        build_position_view(
            position, fee_model=fee_model, policy=policy, store=store,
            history_loader=history_loader, quote_loader=quote_loader,
            sector_map=sector_map, sector_strengths=sector_strengths,
            sector_intraday=sector_intraday, persist=persist,
            session=session, rebuild=rebuild,
        )
        for position in sorted(book.open_positions, key=lambda item: item.symbol)
    )

    exposure: dict = {}
    for view in views:
        amount = (
            view.value.market_value_egp if view.value
            else view.position.cost_basis_egp
        )
        exposure[view.sector or "غير معروف"] = (
            exposure.get(view.sector or "غير معروف", 0.0) + amount
        )

    generated = (now or datetime.now(timezone.utc).astimezone()).isoformat(
        timespec="seconds")
    return PortfolioView(
        generated_at=generated, book=book, fee_model=fee_model, policy=policy,
        positions=views, warnings=tuple(warnings), sector_exposure=exposure,
    )


def log_recommendations(store, view: PortfolioView) -> int:
    """Record every actionable recommendation once per session.

    Only recommendations that ask for something are logged, and only when the
    same rule did not already produce the same action for that symbol on the
    same session. Otherwise a page left open on a refresh timer would write
    thousands of identical rows and make the outcome measurement meaningless.
    """

    written = 0
    for item in view.positions:
        recommendation = item.recommendation
        if recommendation is None or not recommendation.actionable:
            continue
        session = item.plan.session_date if item.plan else ""
        previous = store.last_recommendation(item.symbol)
        if (
            previous
            and previous.get("rule") == recommendation.rule
            and previous.get("action") == recommendation.action
            and previous.get("session_date") == session
        ):
            continue
        store.log_recommendation(
            item.symbol,
            action=recommendation.action, urgency=recommendation.urgency,
            rule=recommendation.rule, reason=recommendation.reason_en,
            price=recommendation.price, price_basis=recommendation.price_basis,
            plan_version=item.plan_version, net_percent=recommendation.net_percent,
            evidence=recommendation.evidence, session_date=session,
        )
        written += 1
    return written
