"""A cost model must never fail quietly to zero.

The first version of the loader called a settings function that did not exist,
caught the ImportError, and returned zero rates. Every cost on the dashboard
read 0.00% and all thirteen of that session's signals looked profitable. The
real numbers, once the loader worked, said ten of the thirteen lost money at
their own first target.

So the tests below care about two things in roughly equal measure: that the
arithmetic is right, and that a failure to read the rates is reported as a
failure rather than as a cost of nothing.
"""

from __future__ import annotations

import pytest

from services.trading_costs import TradingCosts, load_trading_costs

#: The rates in config/settings.json at the time of writing.
COMMISSION = 0.003
SLIPPAGE = 0.0005


@pytest.fixture
def costs():
    return TradingCosts(
        commission_per_side=COMMISSION,
        slippage_per_side=SLIPPAGE,
        rates_loaded=True,
    )


def test_round_trip_charges_both_sides(costs):
    # 0.3% + 0.05%, entry and exit.
    assert costs.round_trip_percent == pytest.approx(0.70)


def test_spread_is_added_once_not_twice(costs):
    # You buy at the ask and sell at the bid: one full spread over the trip.
    assert costs.total_percent(0.40) == pytest.approx(1.10)


def test_an_unmeasured_spread_makes_the_cost_unknown_not_smaller(costs):
    # Substituting zero here would understate cost on exactly the signals
    # whose book was never observed, and the row would look complete.
    assert costs.total_percent(None) is None
    assert costs.net_target_percent(1.5, None) is None
    assert costs.net_reward_risk(10.0, 9.5, 10.5, None) is None


def test_a_target_smaller_than_its_costs_is_reported_negative(costs):
    # RUBX on 2026-08-18: a 0.42% move against a 0.30% spread.
    net = costs.net_target_percent(0.423, 0.299)
    assert net == pytest.approx(0.423 - 0.999, abs=1e-9)
    assert net < 0


def test_no_reward_risk_exists_when_costs_eat_the_move(costs):
    # None here means "cannot win", and the caller must not render it the same
    # way it renders a missing input.
    assert costs.net_reward_risk(13.45, 13.39, 13.507, 0.299) is None


def test_reward_risk_falls_far_below_the_engine_ratio(costs):
    # AMES: the engine reports 2.0 because both targets are R multiples. The
    # same levels after costs are worth well under half that.
    net = costs.net_reward_risk(128.90, 127.84, 133.148, 0.382)
    assert net is not None
    assert 0.5 < net < 1.2


def test_tax_applies_to_the_gain_only_never_to_the_loss():
    taxed = TradingCosts(commission_per_side=0.001, capital_gains_tax_percent=10.0,
                         rates_loaded=True)
    untaxed = TradingCosts(commission_per_side=0.001, rates_loaded=True)

    # A winner is reduced by the tax.
    assert taxed.net_target_percent(3.0, 0.0) < untaxed.net_target_percent(3.0, 0.0)

    # A move that already loses is not made worse by a tax on a gain there is
    # none of. Charging it as a flat percentage would overstate every loss.
    losing = taxed.net_target_percent(0.1, 0.0)
    assert losing == pytest.approx(untaxed.net_target_percent(0.1, 0.0))
    assert losing < 0


def test_missing_inputs_give_none_not_a_guess(costs):
    assert costs.net_target_percent(None, 0.3) is None
    assert costs.net_reward_risk(None, 1.0, 2.0, 0.3) is None
    assert costs.net_reward_risk(10.0, 11.0, 12.0, 0.3) is None    # stop above entry
    assert costs.net_reward_risk(10.0, 9.0, 9.5, 0.3) is None      # target below entry


def test_rates_load_from_the_settings_section():
    loaded = load_trading_costs({"commission": 0.003, "slippage": 0.0005})

    assert loaded.rates_loaded
    assert loaded.load_error is None
    assert loaded.round_trip_percent == pytest.approx(0.70)


def test_tax_is_none_when_unset_and_is_not_treated_as_zero():
    loaded = load_trading_costs({"commission": 0.003})

    assert loaded.capital_gains_tax_percent is None
    assert not loaded.tax_configured


@pytest.mark.parametrize("section", [None, {}, {"slippage": 0.0005}, "not a dict", 42])
def test_unreadable_settings_report_failure_rather_than_zero_cost(section):
    loaded = load_trading_costs(section if section is not None else {})

    assert not loaded.rates_loaded
    assert loaded.load_error, "a failed load must carry a reason a human can act on"


def test_a_default_instance_is_marked_unloaded():
    # The dataclass default is what a SignalRow built without settings gets.
    # It must not pass itself off as a real, zero-cost account.
    assert not TradingCosts().rates_loaded


def test_garbage_rates_do_not_raise_and_do_not_become_negative():
    loaded = load_trading_costs({"commission": "abc", "slippage": -5})

    assert not loaded.rates_loaded
    assert loaded.slippage_per_side >= 0.0


def test_zero_tax_and_unset_tax_are_different_claims():
    # Zero is an answer: no capital gains tax applies on this market, and the
    # stamp duty that does is already a fee line. Unset is the absence of an
    # answer. Collapsing them would put a number nobody chose behind every net
    # figure on the page.
    zero = load_trading_costs({"commission": 0.001819, "capital_gains_tax_percent": 0})
    unset = load_trading_costs({"commission": 0.001819})

    assert zero.tax_configured and zero.capital_gains_tax_percent == 0
    assert not unset.tax_configured and unset.capital_gains_tax_percent is None

    # And with no tax, the two compute identically -- the distinction is in
    # what the page says, not in the arithmetic.
    assert zero.net_target_percent(2.0, 0.3) == pytest.approx(
        unset.net_target_percent(2.0, 0.3)
    )


def test_fee_schedule_is_summed_and_kept_line_by_line():
    loaded = load_trading_costs({
        "commission": 0.003,          # must lose to the schedule
        "fee_schedule": {
            "_source": "contract note",
            "brokerage_and_custody_percent": 0.1000,
            "trading_stamp_duty_percent": 0.0500,
            "egx_services_percent": 0.0100,
            "mcdr_services_percent": 0.0100,
            "fra_services_percent": 0.0069,
            "risk_insurance_percent": 0.0050,
            "order_fee_egp": 4.00,
        },
    })

    assert loaded.commission_per_side == pytest.approx(0.001819)
    assert len(loaded.fee_lines) == 6, "the _source and flat fee are not rate lines"
    assert loaded.order_fee_egp == 4.00


def test_the_flat_order_fee_is_not_folded_into_a_percentage():
    loaded = load_trading_costs({
        "commission": 0.001819,
        "fee_schedule": {"order_fee_egp": 4.00, "brokerage_percent": 0.1},
    })

    # Its weight is entirely a function of position size, so it cannot be a
    # rate; a smaller position pays proportionally more.
    assert loaded.order_fee_percent(20_000) > loaded.order_fee_percent(100_000)
    assert loaded.order_fee_percent(None) is None
    assert loaded.round_trip_percent == pytest.approx(0.2), "flat fee is excluded"
