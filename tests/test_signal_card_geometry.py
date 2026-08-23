"""The signal rail must be drawn to scale, or it is not evidence.

``signal_card`` exists to answer one question before any number is read: does
the target clear the cost? It answers it geometrically — stop, entry and target
at their true relative distances, with the round trip drawn from the entry
outward at its real width. The moment any of that stops being proportional the
picture becomes decoration that looks like measurement, so the arithmetic is
pinned here rather than left to the eye.
"""

from __future__ import annotations

import re

import pytest

from dashboard.ui import _percent, _price, _rail_position, _net_tone

RAIL_LOW, RAIL_HIGH = 4.0, 96.0


def test_the_extremes_land_on_the_rail_ends():
    assert _rail_position(10.0, 10.0, 20.0) == pytest.approx(RAIL_LOW)
    assert _rail_position(20.0, 10.0, 20.0) == pytest.approx(RAIL_HIGH)


def test_position_is_linear_in_price():
    """Twice the distance draws twice as far. Nothing is eased or ranked."""
    low, high = 100.0, 110.0
    near = _rail_position(101.0, low, high) - RAIL_LOW
    far = _rail_position(102.0, low, high) - RAIL_LOW
    assert far == pytest.approx(2 * near)


def test_a_wider_stop_draws_further_from_the_entry():
    """The asymmetry a trader must see is the one the rail must show."""
    entry, target = 100.0, 101.0

    tight_low = min(99.5, entry, target)
    tight = abs(_rail_position(entry, tight_low, target)
                - _rail_position(99.5, tight_low, target))

    wide_low = min(98.0, entry, target)
    wide = abs(_rail_position(entry, wide_low, target)
               - _rail_position(98.0, wide_low, target))

    assert wide > tight


def test_a_price_outside_the_range_is_clamped_not_overflowed():
    """The cost edge can exceed the target; it must stop at the rail's end."""
    assert _rail_position(500.0, 10.0, 20.0) == pytest.approx(RAIL_HIGH)
    assert _rail_position(1.0, 10.0, 20.0) == pytest.approx(RAIL_LOW)


def test_a_zero_span_does_not_divide_by_zero():
    assert RAIL_LOW <= _rail_position(10.0, 10.0, 10.0) <= RAIL_HIGH


class _Recorder:
    """Captures the markup ``signal_card`` writes, without a Streamlit runtime."""

    def __init__(self):
        self.html = ""

    def markdown(self, body, **_kwargs):
        self.html += body


@pytest.fixture
def rendered(monkeypatch):
    def render(**kwargs):
        from dashboard import ui

        recorder = _Recorder()
        monkeypatch.setattr(ui, "st", recorder)
        ui.signal_card(**kwargs)
        return recorder.html

    return render


def _cost_band(markup):
    """``(left, width)`` of the cost band, or ``None`` when none was drawn."""
    match = re.search(r'class="cost" style="left:([\d.]+)%;width:([\d.]+)%"', markup)
    return (float(match.group(1)), float(match.group(2))) if match else None


def _dot_left(markup):
    return float(re.search(r'class="dot" style="left:([\d.]+)%"', markup).group(1))


def test_the_cost_band_starts_at_the_entry(rendered):
    markup = rendered(ticker="MPCI", entry=8.44, stop=8.352, target=8.57,
                      move_percent=1.54, cost_percent=0.72, net_percent=0.82)
    left, width = _cost_band(markup)
    assert left == pytest.approx(_dot_left(markup), abs=0.15)
    assert width > 0


def test_a_wider_cost_draws_a_wider_band(rendered):
    common = dict(ticker="X", entry=100.0, stop=97.0, target=103.0,
                  move_percent=3.0, net_percent=0.0)
    _, narrow = _cost_band(rendered(cost_percent=0.5, **common))
    _, wide = _cost_band(rendered(cost_percent=1.5, **common))
    assert wide > narrow * 2.5


def test_an_unknown_cost_draws_no_band_and_says_so(rendered):
    """An unobserved spread is not a smaller one, and must never read 0.00%."""
    markup = rendered(ticker="MEPA", entry=10.0, stop=9.7, target=10.4,
                      move_percent=4.0, cost_percent=None, net_percent=None)
    assert _cost_band(markup) is None
    assert "unknown" in markup
    assert "0.00%" not in markup


def test_a_losing_signal_is_marked_as_one(rendered):
    losing = rendered(ticker="GPIM", entry=4.18, stop=4.132, target=4.22,
                      move_percent=0.96, cost_percent=1.22, net_percent=-0.26)
    assert "egx-sig neg" in losing
    assert "-0.26%" in losing

    winning = rendered(ticker="OCDI", entry=24.9, stop=24.63, target=25.16,
                       move_percent=1.06, cost_percent=0.70, net_percent=0.36)
    assert "egx-sig pos" in winning
    assert "+0.36%" in winning


def test_an_unknown_net_is_neither_win_nor_loss(rendered):
    markup = rendered(ticker="X", entry=10.0, stop=9.5, target=10.5,
                      move_percent=5.0, cost_percent=None, net_percent=None)
    assert "egx-sig unk" in markup
    assert _net_tone(None) == "unk"


def test_a_signal_without_levels_still_renders(rendered):
    """Sessions recorded before schema v9 kept no levels. They are still shown."""
    markup = rendered(ticker="ABUK", entry=None, stop=None, target=None,
                      move_percent=None, cost_percent=None, net_percent=None)
    assert "egx-rail" not in markup
    assert "levels were not recorded" in markup
    assert "ABUK" in markup


def test_the_ticker_is_escaped(rendered):
    markup = rendered(ticker="<script>x</script>", entry=10.0, stop=9.0,
                      target=11.0, move_percent=1.0, cost_percent=0.5,
                      net_percent=0.5)
    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup


def test_prices_and_percentages_read_as_written():
    assert _price(8.44) == "8.44"
    assert _price(None) == "—"
    assert _percent(None) == "unknown"
    assert _percent(1.5) == "1.50%"
    assert _percent(-0.26, signed=True) == "-0.26%"
    assert _percent(0.82, signed=True) == "+0.82%"
