"""The 200-day average test on the T+0 Radar page.

These pin the definition the evidence was measured under: which side of the
average a test closed on, what counts as approaching or already through it, that
a session without a trade counts as zero turnover, and that a move past the daily
limit -- an unadjusted split -- takes a name out rather than distorting its
average.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from t0_radar import sma200 as S


def row(**values):
    base = {"traded": True, "turnover": 10e6, "slope": 0.02, "peak": 0.15, "action": 0.0,
            "dist": 0.0, "low_dist": -0.01}
    base.update(values)
    return base


@pytest.mark.parametrize("values,expected", [
    (row(dist=0.004, low_dist=-0.01), S.ON_OR_ABOVE),
    (row(dist=-0.02, low_dist=-0.04), S.BELOW),
    (row(dist=0.04, low_dist=0.03), S.APPROACHING),
    (row(dist=-0.06, low_dist=-0.08), S.BROKE),
    (row(dist=0.004, slope=-0.01), ""),                 # the average is falling
    (row(dist=0.004, peak=0.05), ""),                   # it never came from 10% above
    (row(dist=0.004, action=1.0), ""),                  # an unadjusted split in the window
    (row(dist=0.004, turnover=1e6), ""),                # not liquid
    (row(dist=0.004, traded=False), ""),                # no trade on the session
    (row(dist=-0.04, low_dist=-0.05), S.BROKE),         # closed past the 3% allowance
    (row(dist=0.03, low_dist=0.025), S.APPROACHING),    # the low never reached the zone
])
def test_each_state_is_what_the_evidence_was_measured_under(values, expected):
    assert S.state(pd.DataFrame([values])).iloc[0] == expected


DAYS = pd.bdate_range("2025-01-01", periods=320)


def frame(closes, turnover=10e6, lows=None):
    closes = pd.Series(closes, index=DAYS[:len(closes)], dtype=float)
    lows = closes * 0.995 if lows is None else pd.Series(lows, index=closes.index)
    return pd.DataFrame({"Close": closes, "High": closes * 1.005, "Low": lows,
                         "Turnover": turnover}, index=closes.index)


def test_a_session_without_a_trade_counts_as_zero_turnover():
    f = frame(np.full(320, 100.0), turnover=10e6)
    sparse = f.iloc[::2]                                 # trades every other session
    out = S.features(sparse, DAYS)
    assert out["turnover"].iloc[-1] == pytest.approx(5e6, rel=0.11)


def test_a_move_past_the_daily_limit_flags_the_next_two_hundred_sessions():
    closes = np.full(320, 100.0)
    closes[200:] = 70.0                                  # -30% in one session
    out = S.features(frame(closes), DAYS)
    assert out["action"].iloc[-1] == 1.0
    assert out["action"].iloc[150] == 0.0


def pulled_back():
    """260 sessions up, 59 down, and a last close half a percent over the average."""

    rise = np.linspace(100, 160, 260)
    fall = np.linspace(158, 120, 59)
    closes = np.concatenate([rise, fall, [0.0]])
    sma = pd.Series(closes[:-1]).iloc[-199:].sum()
    closes[-1] = (sma / 199) * 1.0055                   # about +0.5% over the new average
    lows = closes * 0.995
    lows[-1] = closes[-1] * 0.985
    return frame(closes, lows=lows)


def test_scan_finds_the_test_and_reads_the_market():
    frames = {f"F{i:02d}": frame(np.full(320, 100.0)) for i in range(30)}
    frames["TEST"] = pulled_back()
    result = S.scan(frames)
    assert result.session_date == DAYS[-1].date().isoformat()
    assert list(result.names["symbol"]) == ["TEST"]
    found = result.names.iloc[0]
    assert found["state"] == S.ON_OR_ABOVE
    assert 0 <= found["dist"] < S.ZONE_ABOVE
    # Flat names are not below their 20-day average, so this is not a sell-off.
    assert result.below_sma20_share == pytest.approx(0.0)
    assert result.selloff is False


def test_scan_calls_a_broad_sell_off_when_most_names_are_under_their_sma20():
    falling = np.concatenate([np.full(300, 100.0), np.linspace(99, 90, 20)])
    frames = {f"F{i:02d}": frame(falling) for i in range(30)}
    result = S.scan(frames)
    assert result.below_sma20_share == pytest.approx(1.0)
    assert result.selloff is True
