"""الطرفية · Terminal — one screen, and the rules that keep it honest.

The screen exists because the answer to "what happened today" was spread over
four pages that are each read by scrolling. Putting it on one screen is only an
improvement if the compression does not start lying, so these pin the four ways
it could:

* an absence must never be drawn as a zero, in any pane;
* a pane that cannot be read must say so where it would have drawn, and must
  not take the other three down with it;
* the trade rail is drawn from the levels or not at all;
* the scan's vocabulary and the portfolio's vocabulary stay separate -- HOLD is
  not AVOID and EXIT is not a refusal to buy.

Presentation only: nothing here reads a provider, and nothing in the module
computes an indicator, a level or a decision.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from dashboard import terminal


# --- numbers -------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, float("nan"), "", "abc", [], {}])
def test_a_value_that_was_never_measured_is_never_a_zero(value):
    assert terminal.num(value) == terminal.UNKNOWN
    assert terminal.pct(value) == terminal.UNKNOWN


def test_zero_is_a_number_and_prints_as_one():
    """A measured zero and an unmeasured value are different facts."""
    assert terminal.num(0) == "0.00"
    assert terminal.num(0.0, 0) == "0"
    assert terminal.pct(0) == "0.00%"


def test_a_gain_carries_its_sign_and_a_loss_needs_no_help():
    assert terminal.num(31238.0, 0, plus=True) == "+31,238"
    assert terminal.num(-70460.0, 0, plus=True) == "-70,460"
    assert terminal.num(0, 2, plus=True) == "0.00"        # not "+0.00"


def test_thousands_are_grouped_so_two_rows_can_be_compared():
    assert terminal.num(3840858, 0) == "3,840,858"


@pytest.mark.parametrize("value, expected", [
    (5, "up"), (-5, "dn"), (0, ""), (None, ""), (float("nan"), ""),
])
def test_a_tone_follows_the_sign_and_nothing_else(value, expected):
    assert terminal._tone_of(value) == expected


# --- the two vocabularies -------------------------------------------------------

@pytest.mark.parametrize("action, klass", [
    ("BUY", "buy"), ("WATCH", "watch"), ("AVOID", "avoid"),
    ("HOLD", "hold"), ("EXIT", "exit"), ("TRIM", "watch"),
    ("RAISE_STOP", "watch"),
])
def test_each_action_keeps_its_own_chip(action, klass):
    assert f'class="sig {klass}"' in terminal.signal_chip(action)


def test_holding_a_position_is_not_the_same_as_refusing_to_buy_one():
    """HOLD and AVOID are different decisions about different things."""
    assert terminal.signal_chip("HOLD") != terminal.signal_chip("AVOID")


def test_exit_is_not_drawn_in_the_colour_of_a_refusal():
    assert 'class="sig exit"' in terminal.signal_chip("EXIT")


def test_withheld_is_the_unknown_state_rather_than_a_sixth_action():
    """No recommendation could be made. That is not an action."""
    rendered = terminal.signal_chip("WITHHELD")
    assert 'class="q"' in rendered
    assert "sig" not in rendered


def test_a_missing_recommendation_is_the_unknown_mark():
    assert terminal.signal_chip(None) == terminal.unknown_cell()
    assert terminal.UNKNOWN in terminal.signal_chip("")


# --- the positions pane ---------------------------------------------------------

def position(**overrides):
    row = dict(symbol="COMI", quantity=1000, average=75.968, last=60.31,
               pnl=-15658.0, percent=-20.61, rule="EXIT")
    row.update(overrides)
    return row


def test_a_position_the_market_never_priced_is_hatched_not_zeroed():
    markup = terminal.positions_body([position(last=None, pnl=None,
                                               percent=None, rule=None)])
    assert 'class="unk"' in markup
    # The quantity and the average are facts about the book and stay.
    assert "1,000" in markup and "75.968" in markup
    # Everything a price would have produced is the unknown mark.
    assert markup.count(terminal.UNKNOWN) == 4        # last, P&L, %, rule


def test_a_priced_position_carries_no_unknown_marks():
    assert terminal.UNKNOWN not in terminal.positions_body([position()])


def test_a_loss_and_a_gain_are_toned_apart():
    losing = terminal.positions_body([position()])
    winning = terminal.positions_body([position(pnl=1000.0, percent=5.0)])
    assert 'class="r dn"' in losing
    assert 'class="r up"' in winning


def test_a_position_cannot_inject_markup():
    markup = terminal.positions_body([position(symbol="<script>a</script>",
                                               rule="<script>b</script>")])
    assert "<script>" not in markup


# --- the candidates pane --------------------------------------------------------

def test_a_candidate_with_every_level_is_drawn_to_scale():
    """The design's own COMI numbers: stop 89.50, band 91.80-92.50, close
    92.40, target 101.00."""
    rendered = terminal.trade_rail(89.50, 91.80, 92.50, 92.40, 101.00)
    left = float(re.search(r"left:([\d.]+)%", rendered).group(1))
    assert left == pytest.approx(20.0, abs=0.1)
    assert 'class="nw"' in rendered


@pytest.mark.parametrize("levels", [
    (0, 0, 0, 1120.0, 0),                 # a refused name: no level at all
    (None, 91.8, 92.5, 92.4, 101.0),
    (89.5, 91.8, 92.5, 92.4, None),
    (100.0, 95.0, 96.0, 97.0, 90.0),      # target below the stop
])
def test_a_rail_is_never_drawn_from_a_level_that_does_not_exist(levels):
    """A picture of a trade nobody computed is worse than no picture."""
    rendered = terminal.trade_rail(*levels)
    assert rendered == terminal.unknown_cell()
    assert 'class="bd"' not in rendered


def test_a_refused_candidate_still_appears_with_its_close():
    rows = terminal.candidate_rows([{
        "Ticker": "ESRS.CA", "Signal": "AVOID", "Regime": "BEAR",
        "Price": 1120.0, "StopLoss": 0.0, "Target2": 0.0, "RR": 0.0,
        "BuyLow": 0.0, "BuyHigh": 0.0}])
    markup = terminal.candidates_body(rows)
    assert "ESRS" in markup and "1,120.00" in markup
    assert 'class="sig avoid"' in markup


def test_the_ticker_is_shown_without_its_exchange_suffix():
    rows = terminal.candidate_rows([{"Ticker": "COMI.CA", "Signal": "WATCH"}])
    assert rows[0]["symbol"] == "COMI"


def test_the_second_target_is_preferred_and_the_first_is_the_fallback():
    rows = terminal.candidate_rows([
        {"Ticker": "A", "Target1": 96.5, "Target2": 101.0},
        {"Ticker": "B", "Target1": 96.5, "Target2": 0.0},
    ])
    assert rows[0]["target"] == 101.0
    assert rows[1]["target"] == 96.5


def test_the_drawing_limit_is_not_a_filter():
    """The pane scrolls and its header counts the whole population."""
    results = [{"Ticker": f"S{i}", "Signal": "WATCH"} for i in range(200)]
    assert len(terminal.candidate_rows(results, limit=60)) == 60
    assert terminal.decision_counts(results)["WATCH"] == 200


def test_decision_counts_ignore_a_state_nobody_defined():
    counts = terminal.decision_counts(
        [{"Signal": "BUY"}, {"Signal": "sideways"}, {"Signal": None}])
    assert counts == {"BUY": 1, "WATCH": 0, "AVOID": 0}


# --- the bars -------------------------------------------------------------------

def test_the_bars_are_a_share_of_the_population_they_were_measured_in():
    markup = terminal.decision_bars(
        [("buy", 0, ""), ("watch", 109, "w"), ("avoid", 100, "")], 209)
    widths = [float(w) for w in re.findall(r"width:([\d.]+)%", markup)]
    assert widths[0] == 0.0
    assert widths[1] == pytest.approx(52.2, abs=0.1)
    assert sum(widths) == pytest.approx(100.0, abs=0.2)


def test_a_decision_nobody_reached_is_listed_at_zero_rather_than_dropped():
    """A day with no buys is a fact about the day."""
    markup = terminal.decision_bars([("buy", 0, "")], 209)
    assert ">buy<" in markup and ">0<" in markup


def test_an_empty_population_does_not_divide():
    markup = terminal.decision_bars([("buy", 0, "")], 0)
    assert "width:0.0%" in markup


# --- panes that could not be read -----------------------------------------------

def test_an_unavailable_pane_says_what_would_make_it_computable():
    markup = terminal.unavailable_pane("Market", "السوق",
                                       "Run a fresh scan on Breakout Watch.")
    assert terminal.UNKNOWN in markup
    assert "Breakout Watch" in markup
    assert "<table" not in markup, "an unreadable pane drew an empty table"


def test_an_unavailable_pane_is_still_a_pane():
    """It must occupy its place in the grid, not vanish from it."""
    markup = terminal.unavailable_pane("Market", "السوق", "needs a scan")
    assert 'class="pane"' in markup and 'class="ph"' in markup


def test_nothing_in_a_pane_header_injects_markup():
    markup = terminal.pane_html("<script>a</script>", "<script>b</script>",
                                "<script>c</script>", "body")
    assert "<script>" not in markup


# --- the rail and the status line -----------------------------------------------

def test_the_rail_offers_links_rather_than_controls():
    """This page owns no job. Two owners of the scan lifecycle is how two
    concurrent scans of the same universe happened."""
    markup = terminal.rail_html([], [("run scan", "/", True)])
    assert "<button" not in markup
    assert '<a class="go" href="/"' in markup


def test_a_rail_reading_carries_its_state_as_a_colour_not_only_a_word():
    markup = terminal.rail_html([("daily run", "not recorded", "amber")], [])
    assert "var(--amber)" in markup


def test_the_status_line_states_the_unknown_convention():
    markup = terminal.status_html([("history", "mubasher", "green")])
    assert terminal.UNKNOWN in markup
    assert "never 0" in markup


def test_the_status_line_cannot_inject_markup():
    assert "<script>" not in terminal.status_html(
        [("<script>a</script>", "<script>b</script>", "green")])


# --- the frame ------------------------------------------------------------------

def test_the_frame_holds_five_panes_and_never_scrolls_the_page():
    markup = terminal.terminal_html("RAIL", "L", "M", "RT", "RB", "STATUS")
    assert markup.count('class="col"') == 2 and 'class="col split"' in markup
    for part in ("RAIL", "L", "M", "RT", "RB", "STATUS"):
        assert part in markup


def test_the_frame_is_sized_to_the_screen_rather_than_to_a_constant():
    """A literal 900px frame ended 56px below a 900px viewport once Streamlit's
    header and padding were counted, which put the status line -- the line that
    says where every number came from -- off the bottom of the screen."""
    style = terminal.style_html()
    assert f"height:calc(100vh - {terminal.TERMINAL_CHROME}px)" in style
    assert f"height:{terminal.TERMINAL_HEIGHT}px" not in style


def test_the_panes_scroll_and_the_page_does_not():
    style = terminal.style_html()
    assert "overflow:hidden" in style               # the frame
    assert ".term .scroll { overflow:auto" in style  # the panes


def test_every_size_in_the_frame_comes_off_the_one_token():
    """Twenty hard-coded sizes between 8.5px and 20px is how a screen ends up
    needing a magnifying glass, and how fixing that becomes twenty edits."""
    style = re.sub(r"/\*.*?\*/", "", terminal.style_html(), flags=re.S)
    literals = [size for size in re.findall(r"font-size:([^;}]+)", style)
                if "px" in size and "--term-fs" not in size]
    assert not literals, f"a size that does not move with the token: {literals}"


def test_nothing_in_the_frame_is_drawn_below_the_legibility_floor():
    """`em` compounds: a .82em label inside a .82em header rendered at 9.1px,
    the smallest thing on the screen, which is the opposite of what a label
    nested inside a heading should be."""
    style = re.sub(r"/\*.*?\*/", "", terminal.style_html(), flags=re.S)
    base = terminal.TERMINAL_FONT_PX
    for size in re.findall(r"font-size:\s*\.(\d+)em", style):
        assert base * float(f"0.{size}") >= 11.0, (
            f".{size}em of {base}px is below the floor")


def test_the_type_size_is_one_number():
    assert "--term-fs:12px" in terminal.style_html(font_px=12)
    assert terminal.TERMINAL_FONT_PX >= 13


def test_the_terminal_takes_its_colours_from_the_one_palette():
    """A second copy of the palette is how the greens stopped agreeing."""
    style = re.sub(r"/\*.*?\*/", "", terminal.style_html(), flags=re.S)
    literals = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", style))
    assert not literals, f"hard-coded colours in the terminal: {sorted(literals)}"


# --- reading the book -----------------------------------------------------------

def holding(symbol, percent, *, priced=True, action="HOLD"):
    value = (SimpleNamespace(unrealized_net_egp=percent * 100,
                             unrealized_net_percent=percent)
             if priced else None)
    return SimpleNamespace(
        symbol=symbol,
        position=SimpleNamespace(quantity=100, average_price=10.0),
        price=SimpleNamespace(value=9.0 if priced else None),
        value=value,
        recommendation=SimpleNamespace(action=action))


def test_the_worst_position_is_the_first_one_read():
    """The money already at risk outranks the money that is not."""
    view = SimpleNamespace(positions=[holding("A", 5.0), holding("B", -20.0),
                                      holding("C", -1.0)])
    assert [row["symbol"] for row in terminal.position_rows(view)] == ["B", "C", "A"]


def test_an_unpriced_position_sorts_last_rather_than_as_a_breakeven():
    view = SimpleNamespace(positions=[holding("A", 5.0),
                                      holding("MTIE", 0.0, priced=False),
                                      holding("B", -20.0)])
    rows = terminal.position_rows(view)
    assert rows[-1]["symbol"] == "MTIE"
    assert rows[-1]["percent"] is None and rows[-1]["pnl"] is None


def test_a_position_with_no_recommendation_reports_none_rather_than_hold():
    item = holding("A", 1.0)
    item.recommendation = None
    rows = terminal.position_rows(SimpleNamespace(positions=[item]))
    assert rows[0]["rule"] is None


# --- the funnel -----------------------------------------------------------------

def test_the_funnel_is_read_and_never_reconstructed(monkeypatch):
    """A saved watchlist carries its survivors, not its counts."""
    monkeypatch.setattr(terminal.st, "session_state", {}, raising=False)
    assert terminal.funnel_reading() is None
    markup = terminal._funnel_pane()
    assert terminal.UNKNOWN in markup
    assert "Breakout Watch" in markup


def test_a_gate_that_refused_nobody_is_listed_at_zero(monkeypatch):
    """A week a gate was quiet is a fact about the week."""
    monkeypatch.setattr(
        terminal.st, "session_state",
        {"breakout_watch_funnel": {"counts": {"Liquidity": 74},
                                   "candidates": 12, "session": "2026-09-10"}},
        raising=False)
    markup = terminal._funnel_pane()
    assert "price integrity" in markup
    assert ">0<" in markup
    assert ">12<" in markup            # the survivors
