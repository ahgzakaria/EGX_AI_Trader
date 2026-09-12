"""The four things the portfolio page can say about a holding.

They were a card, a joined sentence inside one info banner, and a warning each.
Three shapes for four facts, and the reader has to weigh them against each
other:

* act          -- something to do now, priced after costs
* unvalidated  -- a recommendation whose rule has never been measured
* withheld     -- no price this page trusts, so it issues no advice at all
* noplan       -- nothing was ever measured for this holding

Presentation only: these assert what is drawn, never what is computed.
"""

from __future__ import annotations

import re
from types import SimpleNamespace as NS

import pytest

from dashboard import portfolio, ui


@pytest.fixture
def drawn(monkeypatch):
    """Whatever the page writes with st.markdown, in order."""
    written = []
    monkeypatch.setattr(portfolio.st, "markdown", lambda body, **kw: written.append(body))
    return written


def kind_of(markup):
    return re.search(r'class="egx-act k-(\w+)', markup).group(1)


def tone_of(markup):
    return re.search(r'class="egx-act k-\w+ t-(\w+)"', markup).group(1)


def holding(symbol="COMI", *, action="EXIT", measured=True, basis="LIVE",
            plan_available=True, plan_reason=None, error=None):
    plan = NS(available=plan_available, reason=plan_reason, stop=11.0,
              target_partial=13.0, target_final=14.0)
    recommendation = NS(action=action, urgency=portfolio.NOW, price_basis=basis,
                        measured=measured, reason_ar="كسر وقف الخسارة",
                        price=12.5, quantity=1000, net_egp=-2500.0, net_percent=-4.2)
    return NS(symbol=symbol, recommendation=recommendation,
              price=NS(value=12.5, basis=basis), plan=plan,
              position=NS(quantity=1000, average_price=13.05), error=error,
              value=None, sector="Banks", holding_sessions=4)


# --- each kind is itself --------------------------------------------------------

def test_an_action_card_carries_the_actions_colour_on_its_rail(drawn):
    """The rail was `solid var(--text)` on every card: the tone was computed
    and then never used, so EXIT and HOLD had identical rails."""
    portfolio._action_card(holding(action="EXIT"))
    assert kind_of(drawn[-1]) == "act"
    assert tone_of(drawn[-1]) == "red"

    drawn.clear()
    portfolio._action_card(holding(action="HOLD"))
    assert tone_of(drawn[-1]) == "green"


def test_an_unmeasured_rule_changes_the_whole_card_not_one_badge(drawn):
    """It was the fourth badge in a row of four, carrying the entire caveat."""
    portfolio._action_card(holding(measured=False))
    markup = drawn[-1]
    assert kind_of(markup) == "unvalidated"
    assert "قاعدة غير مُقاسة بعد" in markup


def test_a_measured_rule_is_a_plain_action_card(drawn):
    portfolio._action_card(holding(measured=True))
    assert kind_of(drawn[-1]) == "act"
    assert "غير مُقاسة" not in drawn[-1]


def test_a_withheld_holding_says_why_on_its_own_card(drawn):
    """They were joined into one sentence naming every withheld ticker, which
    said what was blocked but nothing per name."""
    portfolio._withheld_card(holding("MTIE", basis="NO_PRICE"))
    markup = drawn[-1]
    assert kind_of(markup) == "withheld"
    assert "MTIE" in markup
    assert "سعر قديم" in markup


def test_a_holding_with_no_plan_is_unknown_not_a_warning(drawn):
    portfolio._no_plan_card(
        holding("CCAP", plan_available=False, plan_reason="NO_HISTORY"))
    markup = drawn[-1]
    assert kind_of(markup) == "noplan"
    assert "لا توجد شموع يومية كافية" in markup


def test_an_unknown_plan_reason_is_reported_rather_than_swallowed(drawn):
    portfolio._no_plan_card(holding(plan_available=False, plan_reason="SOMETHING_NEW"))
    assert "SOMETHING_NEW" in drawn[-1]


def test_an_explicit_error_beats_the_reason_code(drawn):
    portfolio._no_plan_card(
        holding(plan_available=False, plan_reason="NO_ATR", error="القياس فشل"))
    assert "القياس فشل" in drawn[-1]


def test_the_four_kinds_are_four_distinct_classes(drawn):
    portfolio._action_card(holding())
    portfolio._action_card(holding(measured=False))
    portfolio._withheld_card(holding(basis="NO_PRICE"))
    portfolio._no_plan_card(holding(plan_available=False, plan_reason="NO_ATR"))
    assert [kind_of(m) for m in drawn] == ["act", "unvalidated", "withheld", "noplan"]


# --- and each is drawn differently ----------------------------------------------

@pytest.fixture(scope="module")
def css():
    captured = []
    original = ui.st.markdown
    ui.st.markdown = lambda body, **kwargs: captured.append(body)
    try:
        ui.apply_global_style()
    finally:
        ui.st.markdown = original
    return captured[0]


def test_the_three_kinds_that_are_not_an_action_look_different_from_each_other(css):
    seen = {}
    for kind in ("unvalidated", "withheld", "noplan"):
        rule = re.search(rf"\.egx-act\.k-{kind}\s*\{{(.*?)\}}", css, re.S)
        assert rule, f"no rule for the {kind} card"
        body = rule.group(1)
        assert body not in seen, f"{kind} renders the same as {seen[body]}"
        seen[body] = kind


def test_no_plan_wears_the_unknown_violet_and_withheld_does_not(css):
    """Stale data is a freshness problem the evening import fixes. Nothing ever
    measured is the unknown state, and they must not share a colour."""
    no_plan = re.search(r"\.egx-act\.k-noplan\s*\{(.*?)\}", css, re.S).group(1)
    withheld = re.search(r"\.egx-act\.k-withheld\s*\{(.*?)\}", css, re.S).group(1)
    assert "--unknown" in no_plan
    assert "--amber" in withheld
    assert "--unknown" not in withheld


# --- what the card shows --------------------------------------------------------

def test_the_figures_a_decision_rests_on_are_on_the_card(drawn):
    portfolio._action_card(holding())
    markup = drawn[-1]
    for label in ("السعر", "الكمية", "الصافي بعد التكاليف"):
        assert label in markup
    assert "12.500" in markup and "1,000" in markup


def test_a_loss_and_a_gain_are_coloured_apart(drawn):
    item = holding()
    portfolio._action_card(item)
    assert 'class="v neg"' in drawn[-1]

    drawn.clear()
    item.recommendation.net_egp = 4000.0
    item.recommendation.net_percent = 3.1
    portfolio._action_card(item)
    assert 'class="v pos"' in drawn[-1]


def test_a_missing_company_name_does_not_lose_the_card(drawn, monkeypatch):
    monkeypatch.setattr(portfolio, "company_name",
                        lambda symbol: (_ for _ in ()).throw(KeyError("no name")))
    portfolio._action_card(holding())
    assert "COMI" in drawn[-1]


def test_a_reason_cannot_inject_markup(drawn):
    item = holding()
    item.recommendation.reason_ar = "<script>alert(1)</script>"
    portfolio._action_card(item)
    assert "<script>" not in drawn[-1]


def test_the_card_places_no_order(drawn):
    """The Stitch design drew an execute button routed to a broker OMS. This
    program never places an order and the page must never suggest it does."""
    portfolio._action_card(holding())
    markup = drawn[-1].lower()
    for word in ("<button", "<form", "broker", "oms", "routed", "تنفيذ"):
        assert word not in markup
