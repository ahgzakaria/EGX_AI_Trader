"""Freshness enforcement across Stock Details, Watchlist and AI Analysis.

The audit found each direct view could reach a user without the shared
contracts: Stock Details and Watchlist cached decisions keyed on the symbol
alone (so a 2026-07-30 decision kept rendering as current once the expectation
advanced), and AI Analysis had no gate at all - a stale symbol consumed an
external request and returned a current-looking advisory.

Real symbols from RUN_20260804_005327 are used as fixtures: QNBE/CIRA were
genuinely current on 2026-08-03; SPIN/ORHD/ACAP/ABUK were two trading sessions
behind. The archive itself is never read or modified by these tests.

No test contacts an AI engine, a provider, Rubix, a database or the network,
and every evaluation instant is injected.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.rubix_quote_freshness import RubixQuoteStatus
from services.analysis_freshness_service import (
    AI_BLOCKED_HEADLINE,
    AI_HISTORICAL_LABELS,
    HISTORICAL_LABELS,
    STALE_HEADLINE,
    WATCHLIST_WITHHELD,
    build_analysis_context,
    cached_result_is_current,
)


CAIRO = ZoneInfo("Africa/Cairo")
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

EXPECTED = "2026-08-03"
STALE_SESSION = "2026-07-30"
CURRENT_SYMBOLS = ("QNBE.CA", "CIRA.CA", "FTNS.CA", "GBCO.CA", "MEPA.CA", "ETRS.CA")
STALE_SYMBOLS = ("SPIN.CA", "ORHD.CA", "ACAP.CA", "ABUK.CA")


def cairo(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=CAIRO).astimezone(
        timezone.utc)


#: Mid-session on the expected day, so a same-session quote can be live.
NOW = cairo(2026, 8, 3, 12, 0)


def context(symbol="QNBE.CA", *, actual=EXPECTED, expected=EXPECTED,
            evaluated_at=NOW, quote_at=None, **extra):
    quote_at = quote_at if quote_at is not None else cairo(2026, 8, 3, 11, 59)
    params = dict(
        evaluated_at=evaluated_at,
        expected_session=expected,
        actual_session=actual,
        mapping_verified=True,
        quote_price=15.81,
        quote_market_timestamp=quote_at,
        quote_receive_timestamp=quote_at,
    )
    params.update(extra)
    return build_analysis_context(symbol, **params)


# =========================================================================== #
# SHARED ADAPTER
# =========================================================================== #


def test_current_daily_with_a_live_quote_allows_everything():
    ctx = context()
    assert ctx.current_analysis_allowed is True
    assert ctx.may_show_current_decision is True
    assert ctx.may_invoke_ai is True
    assert ctx.rubix_quote_status == RubixQuoteStatus.RUBIX_LIVE_CURRENT.value
    assert ctx.may_label_rubix_live is True
    assert ctx.rubix_overlay_applied is True
    assert ctx.decision_price_source == "rubix_live"


def test_current_daily_with_a_previous_session_quote_keeps_the_eodhd_close():
    ctx = context(quote_at=cairo(2026, 7, 30, 14, 0))
    assert ctx.current_analysis_allowed is True          # the daily is fine
    assert ctx.rubix_quote_status == RubixQuoteStatus.RUBIX_PREVIOUS_SESSION.value
    assert ctx.may_label_rubix_live is False
    assert ctx.rubix_overlay_applied is False
    assert ctx.decision_price_source == "eodhd_daily_close"
    assert "Daily EODHD close retained." in ctx.rubix_message()


def test_stale_daily_blocks_analysis_even_with_a_live_quote():
    """A tick cannot substitute for the daily candle the strategy reads."""

    ctx = context("SPIN.CA", actual=STALE_SESSION)
    assert ctx.current_analysis_allowed is False
    assert ctx.may_show_current_decision is False
    assert ctx.may_invoke_ai is False
    assert ctx.rubix_quote_status == RubixQuoteStatus.RUBIX_LIVE_CURRENT.value
    assert ctx.rubix_overlay_applied is False
    assert ctx.decision_price_source == "eodhd_daily_close"


@pytest.mark.parametrize("actual", ["", None, "not-a-date", "2026-08-05"])
def test_missing_invalid_or_future_daily_dates_block_analysis(actual):
    ctx = context("X.CA", actual=actual)
    assert ctx.current_analysis_allowed is False
    assert ctx.blocking_reason


def test_an_invalid_rubix_timestamp_denies_the_overlay_only():
    ctx = context(quote_at="not-a-timestamp")
    assert ctx.current_analysis_allowed is True
    assert ctx.rubix_quote_status == RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID.value
    assert ctx.rubix_overlay_applied is False
    assert ctx.decision_price_source == "eodhd_daily_close"


def test_display_and_decision_price_sources_are_reported_separately():
    denied = context(quote_at=cairo(2026, 7, 30, 14, 0))
    assert denied.display_price_source == "eodhd_daily_close"
    assert denied.decision_price_source == "eodhd_daily_close"
    allowed = context()
    assert allowed.display_price_source == "rubix_quote"
    assert allowed.decision_price_source == "rubix_live"


def test_the_provenance_panel_never_presents_a_refresh_time_as_a_candle_date():
    labels = [label for label, _ in context().provenance_rows()]
    assert "Expected completed session" in labels
    assert "Actual candle session" in labels
    for forbidden in ("Cache updated", "Refreshed at", "Fetched at", "File modified"):
        assert forbidden not in labels


def test_the_adapter_reads_no_clock_state_or_engine():
    import ast

    source = (REPO_ROOT / "services" / "analysis_freshness_service.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    target = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "build_analysis_context")
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            for forbidden in ("now", "today", "utcnow", "session_state",
                              "analyze_symbol", "evaluate", "connect"):
                assert not name.endswith(forbidden), name


# =========================================================================== #
# CACHE IDENTITY
# =========================================================================== #


def test_the_identity_changes_when_the_expected_session_advances():
    """The exact leak: same symbol, same candle, new expectation."""

    before = context("SPIN.CA", actual=STALE_SESSION, expected=STALE_SESSION)
    after = context("SPIN.CA", actual=STALE_SESSION, expected=EXPECTED)
    assert before.cache_identity != after.cache_identity
    assert before.current_analysis_allowed is True
    assert after.current_analysis_allowed is False


def test_a_decision_from_an_older_candle_cannot_be_restored_as_current():
    old = context("SPIN.CA", actual=STALE_SESSION, expected=STALE_SESSION)
    now = context("SPIN.CA", actual=STALE_SESSION, expected=EXPECTED)
    assert cached_result_is_current(now, old.cache_identity) is False


def test_a_matching_identity_on_a_current_symbol_is_restorable():
    ctx = context()
    assert cached_result_is_current(ctx, ctx.cache_identity) is True


def test_a_blocked_symbol_never_restores_a_cached_result():
    ctx = context("SPIN.CA", actual=STALE_SESSION)
    assert cached_result_is_current(ctx, ctx.cache_identity) is False


@pytest.mark.parametrize("field,value", [
    ("actual", "2026-08-02"),
    ("expected", "2026-08-04"),
    ("source_identity", "other-source"),
    ("config_identity", "other-config"),
    ("ai_mode_identity", "other-model"),
])
def test_every_identity_component_changes_the_fingerprint(field, value):
    base = context()
    other = context(**{field: value}) if field in ("actual", "expected") \
        else context(**{field: value})
    assert base.cache_identity != other.cache_identity


def test_the_identity_covers_the_candle_session_even_at_the_same_status():
    """Two different stale sessions are both STALE - the date must still count.

    Without this, dropping the candle session from the identity would go
    unnoticed, because a changed session usually changes the status too.
    """

    older = context("SPIN.CA", actual="2026-07-27")
    newer = context("SPIN.CA", actual=STALE_SESSION)
    assert older.daily_freshness_status == newer.daily_freshness_status == "STALE"
    assert older.actual_daily_session != newer.actual_daily_session
    assert older.cache_identity != newer.cache_identity


def test_the_identity_covers_the_rubix_verdict():
    live = context()
    previous = context(quote_at=cairo(2026, 7, 30, 14, 0))
    assert live.cache_identity != previous.cache_identity


# =========================================================================== #
# STOCK DETAILS
# =========================================================================== #


def stock_row(symbol, session):
    return {
        "Ticker": symbol, "Signal": "BUY", "Rating": "A", "Score": 88,
        "Confidence": 91, "Price": 15.64,
        "LastCompletedSession": f"{session}T14:30:00+03:00",
        "LivePrice": 15.81,
        "LivePriceTimestamp": cairo(2026, 8, 3, 11, 59).isoformat(),
        "LivePriceReceivedTimestamp": cairo(2026, 8, 3, 11, 59).isoformat(),
        "DataSource": "eodhd",
    }


def test_stock_details_allows_a_current_symbol():
    from dashboard.stock_details import stock_freshness_context

    ctx = stock_freshness_context(stock_row("QNBE.CA", EXPECTED),
                                  evaluated_at=NOW, expected_session=EXPECTED)
    assert ctx.current_analysis_allowed is True


def test_stock_details_blocks_a_stale_symbol():
    from dashboard.stock_details import stock_freshness_context

    ctx = stock_freshness_context(stock_row("SPIN.CA", STALE_SESSION),
                                  evaluated_at=NOW, expected_session=EXPECTED)
    assert ctx.current_analysis_allowed is False
    message = ctx.stale_message()
    assert STALE_HEADLINE in message
    assert f"Expected completed session: {EXPECTED}" in message
    assert f"Actual latest candle: {STALE_SESSION}" in message
    assert "Trading sessions behind: 2" in message
    assert "not analyzed as a current opportunity" in message


class _FakeWidget:
    """Any Streamlit container/column. Absorbs every call, renders nothing."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        # Widget methods return False so button branches never fire; the page
        # is being observed, not driven.
        def _call(*args, **kwargs):
            return False
        return _call


class _FakeSt:
    """Records only what this test asks about: were decision tabs built?"""

    def __init__(self):
        self.tabs_called = 0
        self.errors = []
        self.warnings = []

    def tabs(self, labels):
        self.tabs_called += 1
        return [_FakeWidget() for _ in labels]

    def columns(self, spec):
        count = spec if isinstance(spec, int) else len(spec)
        return [_FakeWidget() for _ in range(count)]

    def error(self, message):
        self.errors.append(str(message))

    def warning(self, message):
        self.warnings.append(str(message))

    def __getattr__(self, name):
        def _call(*args, **kwargs):
            return _FakeWidget()
        return _call


def test_stock_details_renders_no_decision_surface_for_a_stale_symbol(monkeypatch):
    """Behavioural, not structural: the decision tabs must never be built.

    A source-order assertion alone would still pass if the guard condition were
    disabled, because both statements remain present.
    """

    from dashboard import freshness_panel, stock_details

    fake = _FakeSt()
    monkeypatch.setattr(stock_details, "st", fake)
    monkeypatch.setattr(freshness_panel, "st", fake)
    monkeypatch.setattr(stock_details, "Watchlist",
                        lambda: type("W", (), {"load": lambda self: []})())

    ctx = stock_details.stock_freshness_context(
        stock_row("SPIN.CA", STALE_SESSION), evaluated_at=NOW,
        expected_session=EXPECTED)
    assert ctx.current_analysis_allowed is False

    stock_details.show_stock_details(stock_row("SPIN.CA", STALE_SESSION),
                                     freshness=ctx)
    assert fake.tabs_called == 0, "decision tabs were built for a stale symbol"
    assert any(STALE_HEADLINE in message for message in fake.errors)


def test_stock_details_renders_the_decision_surface_for_a_current_symbol(monkeypatch):
    from dashboard import freshness_panel, stock_details

    fake = _FakeSt()
    monkeypatch.setattr(stock_details, "st", fake)
    monkeypatch.setattr(freshness_panel, "st", fake)
    monkeypatch.setattr(stock_details, "Watchlist",
                        lambda: type("W", (), {"load": lambda self: []})())
    monkeypatch.setattr(stock_details, "stock_level_context", lambda stock: {})

    ctx = stock_details.stock_freshness_context(
        stock_row("QNBE.CA", EXPECTED), evaluated_at=NOW,
        expected_session=EXPECTED)
    try:
        stock_details.show_stock_details(stock_row("QNBE.CA", EXPECTED),
                                         freshness=ctx)
    except Exception:
        pass                      # rendering internals are not under test here
    assert fake.tabs_called == 1, "the current symbol lost its decision tabs"


def test_stock_details_returns_before_rendering_any_decision_surface():
    """The blocking panel replaces the decision tabs; it does not accompany them."""

    import inspect

    from dashboard import stock_details

    source = inspect.getsource(stock_details.show_stock_details)
    block = source.index("render_stale_block(freshness)")
    tabs = source.index("st.tabs([")
    assert block < tabs, "the decision tabs are built before the stale check"
    guard = source[block:tabs]
    assert "return" in guard, "the stale path falls through to the decision tabs"


def test_stock_details_labels_its_historical_section():
    import inspect

    from dashboard import stock_details

    source = inspect.getsource(stock_details.show_stock_details)
    assert "HISTORICAL SNAPSHOT" in source
    assert "render_historical_labels()" in source


def test_a_stale_symbol_does_not_block_a_different_current_symbol():
    from dashboard.stock_details import stock_freshness_context

    stale = stock_freshness_context(stock_row("SPIN.CA", STALE_SESSION),
                                    evaluated_at=NOW, expected_session=EXPECTED)
    current = stock_freshness_context(stock_row("QNBE.CA", EXPECTED),
                                      evaluated_at=NOW, expected_session=EXPECTED)
    assert stale.current_analysis_allowed is False
    assert current.current_analysis_allowed is True


def test_an_unavailable_calendar_never_unblocks_stock_details():
    from dashboard.stock_details import stock_freshness_context

    ctx = stock_freshness_context(stock_row("QNBE.CA", EXPECTED),
                                  evaluated_at=NOW, expected_session="")
    assert ctx.current_analysis_allowed is False


# =========================================================================== #
# WATCHLIST
# =========================================================================== #


class _Results(list):
    def __init__(self, rows, freshness=()):
        super().__init__(rows)
        self.freshness = list(freshness)


def test_a_stale_watchlist_symbol_is_retained_not_dropped():
    import inspect

    from dashboard import watchlist

    source = inspect.getsource(watchlist.render_data_update_required)
    assert "symbol not in analysed" in source
    assert "remain on your watchlist" in inspect.getsource(watchlist)


def test_the_withheld_badge_replaces_a_current_decision():
    from dashboard.freshness_panel import withheld_badge

    assert withheld_badge() == WATCHLIST_WITHHELD
    assert "DECISION WITHHELD" in withheld_badge()
    for forbidden in ("BUY", "WATCH", "AVOID"):
        assert forbidden not in withheld_badge()


def test_the_watchlist_separates_current_from_update_required():
    import inspect

    from dashboard import watchlist

    source = inspect.getsource(watchlist)
    assert "Current Opportunities" in source
    assert "Data Update Required" in source
    assert source.index("render_data_update_required(symbols, results)") < \
        source.index('section_header("Current Opportunities"')


def test_watchlist_badge_counts_come_from_current_rows_only():
    import inspect

    from dashboard import watchlist

    source = inspect.getsource(watchlist.show_watchlist)
    counts = source.index('metrics[0].metric("BUY"')
    assert "frame[" in source[counts:counts + 120]


def test_a_previous_decision_is_labelled_not_current():
    import inspect

    from dashboard import watchlist

    source = inspect.getsource(watchlist.render_data_update_required)
    assert "NOT CURRENT" in source


# =========================================================================== #
# AI ANALYSIS
# =========================================================================== #


def test_a_current_symbol_invokes_the_ai_engine_once():
    from dashboard.ai_stock_analysis import run_analysis

    calls = []
    result = run_analysis("QNBE.CA", runner=lambda s: calls.append(s) or {"ok": True},
                          context=context())
    assert calls == ["QNBE.CA"]
    assert result == {"ok": True}


@pytest.mark.parametrize("actual", [STALE_SESSION, "", "not-a-date", "2026-08-05"])
def test_a_non_current_symbol_invokes_the_engine_zero_times(actual):
    """A blocked symbol must not spend an external request to be refused."""

    from dashboard.ai_stock_analysis import run_analysis

    calls = []
    result = run_analysis("SPIN.CA", runner=lambda s: calls.append(s),
                          context=context("SPIN.CA", actual=actual))
    assert calls == [], "the AI engine was invoked for a non-current symbol"
    assert result["blocked"] is True
    assert AI_BLOCKED_HEADLINE in result["message"]


def test_the_ai_block_message_shows_both_sessions():
    ctx = context("SPIN.CA", actual=STALE_SESSION)
    message = ctx.ai_blocked_message()
    assert f"Expected completed session: {EXPECTED}" in message
    assert f"Actual latest candle: {STALE_SESSION}" in message
    assert "Daily freshness: STALE" in message
    assert "AI analysis was not invoked" in message


def test_the_gate_precedes_the_runner_call():
    import inspect

    from dashboard import ai_stock_analysis

    source = inspect.getsource(ai_stock_analysis.run_analysis)
    guard = source.index("analysis_is_permitted(context)")
    assert "runner" not in source[:guard] or source.index("return {") > guard


def test_the_ai_cache_identity_includes_the_candle_session():
    import inspect

    from dashboard import ai_stock_analysis

    source = inspect.getsource(ai_stock_analysis._discard_outdated_analysis)
    assert "cached_result_is_current" in source
    assert "STATE_FRESHNESS_IDENTITY" in source


def test_an_ai_result_from_older_data_is_refused_as_current():
    old = context("SPIN.CA", actual=STALE_SESSION, expected=STALE_SESSION)
    now = context("SPIN.CA", actual=STALE_SESSION, expected=EXPECTED)
    assert cached_result_is_current(now, old.cache_identity) is False


def test_historical_ai_labels_are_explicit():
    assert "HISTORICAL AI EXPLANATION" in AI_HISTORICAL_LABELS
    assert "NOT A BUY SIGNAL" in AI_HISTORICAL_LABELS
    assert "NOT CURRENT MARKET ANALYSIS" in AI_HISTORICAL_LABELS


# =========================================================================== #
# SHARED WORDING
# =========================================================================== #


def test_all_views_use_the_typed_rubix_wording():
    from core.rubix_quote_freshness import STATUS_LABEL

    for status, label in STATUS_LABEL.items():
        if status is not RubixQuoteStatus.RUBIX_LIVE_CURRENT:
            assert "LIVE CURRENT" not in label


def test_no_view_uses_a_generic_freshness_word():
    """FRESH / LIVE / UPDATED without session evidence is what caused this."""

    import ast

    for name in ("dashboard/freshness_panel.py",
                 "services/analysis_freshness_service.py"):
        tree = ast.parse((REPO_ROOT / name).read_text(encoding="utf-8"))
        docstrings = {ast.get_docstring(node, clean=False)
                      for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in docstrings:
                    continue
                assert node.value.strip().upper() not in ("FRESH", "LIVE", "UPDATED")


def test_the_historical_labels_are_never_optional():
    assert "NOT CURRENT MARKET ANALYSIS" in HISTORICAL_LABELS
    assert "NOT A CURRENT BUY/WATCH/AVOID DECISION" in HISTORICAL_LABELS
    assert "RESEARCH ONLY" in HISTORICAL_LABELS


# =========================================================================== #
# BOUNDARIES
# =========================================================================== #


def test_no_yahoo_or_process_control_in_the_new_modules():
    for name in ("services/analysis_freshness_service.py",
                 "dashboard/freshness_panel.py"):
        source = (REPO_ROOT / name).read_text(encoding="utf-8").lower()
        for forbidden in ("yahoo", "subprocess", "popen", "taskkill", "websocket"):
            assert forbidden not in source


def test_strategy_thresholds_are_unchanged():
    from scalping_orb.strategy_config import OrbStrategyConfig

    config = OrbStrategyConfig()
    assert config.minimum_reward_risk == 1.5
    assert config.target_2_r_multiple == 2.0


def test_no_test_here_reads_the_wall_clock():
    import ast

    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    guard = "test_no_test_here_reads_the_wall_clock"
    skipped = {id(node) for parent in ast.walk(tree)
               if isinstance(parent, ast.FunctionDef) and parent.name == guard
               for node in ast.walk(parent)}
    for node in ast.walk(tree):
        if id(node) in skipped or not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        assert not name.endswith("datetime.now"), name
        assert not name.endswith("date.today"), name
