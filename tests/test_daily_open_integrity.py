"""The fabricated daily `Open`, pinned.

Measured 2026-08-25: in `data/market_data_cache.sqlite` 97.4% of Yahoo daily
opens equal the previous close, and 26.2% of bars carry an open outside their
own `[low, high]` -- 31.87% among bars that actually traded. The raw provider
CSVs are already 98.4% carried forward before any local ingestion, so the field
arrives fabricated. Rebuilt from traded minutes the same quantity is 12.9% and
0.0%, which is what an honest EGX open looks like.

These tests pin the detector, not the data, so they stay green while the defect
exists and stay meaningful after it is fixed.
"""

import pandas as pd
import pytest

from core.daily_open_integrity import (
    CARRY_FORWARD_LIMIT,
    MINIMUM_OBSERVATIONS,
    OpenIntegrity,
    classify_open,
)
from providers.base_provider import normalize_history
from strategy.candles import candle_score


def _frame(rows):
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"])


def _observed(n=140):
    """A market whose opens gap around the prior close, as a real one does."""
    rows, close = [], 100.0
    for step in range(n):
        drift = 0.4 if step % 2 else -0.3
        open_ = close + drift
        high, low = open_ + 0.6, open_ - 0.6
        close = open_ + (0.2 if step % 3 else -0.25)
        rows.append([open_, max(high, close), min(low, close), close])
    return _frame(rows)


def _carried_forward(n=140):
    """The defect: every open is the previous close, but clipped into range."""
    rows, close = [], 100.0
    for step in range(n):
        open_ = close
        close = open_ + (0.5 if step % 2 else -0.4)
        rows.append([open_, max(open_, close) + 0.3, min(open_, close) - 0.3, close])
    return _frame(rows)


def test_a_real_market_reads_as_observed():
    report = classify_open(_observed())
    assert report.verdict is OpenIntegrity.OBSERVED
    assert report.usable
    assert report.carry_forward_rate == 0.0


def test_carried_forward_opens_are_caught_even_when_clipped_into_range():
    # EODHD's variant: fabricated but tidy, so the range test alone cannot see
    # it. The carry-forward rate is what catches this one.
    report = classify_open(_carried_forward())
    assert report.verdict is OpenIntegrity.CARRIED_FORWARD
    assert not report.usable
    assert report.out_of_range_rate == 0.0
    assert report.carry_forward_rate == 1.0


def test_an_open_outside_its_own_bar_is_proof_not_an_estimate():
    # A single impossible bar is enough. It cannot have traded.
    frame = _observed()
    frame.loc[10, "Open"] = float(frame.loc[10, "High"]) + 5.0
    report = classify_open(frame)
    assert report.verdict is OpenIntegrity.OUT_OF_RANGE
    assert not report.usable


def test_out_of_range_is_reported_ahead_of_carry_forward():
    # The Yahoo variant is both at once; the stronger evidence should be named.
    frame = _carried_forward()
    frame.loc[5, "Open"] = float(frame.loc[5, "Low"]) - 5.0
    assert classify_open(frame).verdict is OpenIntegrity.OUT_OF_RANGE


def test_a_thin_market_below_the_threshold_still_reads_as_observed():
    # 12.9% carry-forward was measured from traded minutes. A real EGX symbol
    # sits there, and must not be condemned for it.
    rows, close = [], 100.0
    for step in range(200):
        open_ = close if step % 8 == 0 else close + 0.35
        close = open_ + (0.3 if step % 2 else -0.2)
        rows.append([open_, max(open_, close) + 0.2, min(open_, close) - 0.2, close])
    report = classify_open(_frame(rows))
    assert report.carry_forward_rate <= CARRY_FORWARD_LIMIT
    assert report.verdict is OpenIntegrity.OBSERVED


def test_too_few_rows_is_unknown_and_unknown_is_not_a_pass():
    report = classify_open(_observed(n=MINIMUM_OBSERVATIONS - 5))
    assert report.verdict is OpenIntegrity.UNKNOWN
    assert not report.usable


def test_a_frame_without_ohlc_is_unknown_rather_than_an_exception():
    report = classify_open(pd.DataFrame({"Close": [1.0, 2.0]}))
    assert report.verdict is OpenIntegrity.UNKNOWN


@pytest.mark.parametrize("builder,expected", [
    (_observed, OpenIntegrity.OBSERVED),
    (_carried_forward, OpenIntegrity.CARRIED_FORWARD),
])
def test_normalize_history_attaches_the_verdict_once_per_frame(builder, expected):
    frame = builder()
    frame["Volume"] = 1000.0
    frame.index = pd.date_range("2024-01-01", periods=len(frame), freq="D")
    normalized = normalize_history(frame, "TEST.CA", "test")
    assert normalized.attrs["open_integrity"].verdict is expected
    # Representation only: the boundary must not have altered a candle value.
    assert list(normalized["Open"]) == list(frame["Open"])


def test_candle_score_refuses_to_score_a_fabricated_open():
    # The contract changed deliberately on 2026-08-26. It used to report the
    # verdict and score anyway; it now refuses, because every pattern it tests
    # reads Open and none of them mean what they are named for without one. A
    # signal citing "Morning Star" on a carried-forward Open was not telling
    # the reader what happened.
    frame = _carried_forward()
    frame.attrs["open_integrity"] = classify_open(frame)
    scored = candle_score(frame, 30)
    assert scored["open_integrity"] == OpenIntegrity.CARRIED_FORWARD.value
    assert scored["score"] == 0
    assert scored["confidence"] == 0
    # It says why, rather than going quiet and looking like a real zero.
    assert scored["reasons"] == ["Candle patterns unavailable (Open CARRIED_FORWARD)"]


def test_an_unknown_verdict_does_not_silence_the_detector():
    # pandas `attrs` does not survive every operation, so a frame can arrive
    # without provenance. Condemning on UNKNOWN would disable candles silently
    # wherever that happened. Absence of evidence is not evidence of
    # fabrication: only a positive finding silences it.
    frame = _carried_forward()
    assert "open_integrity" not in frame.attrs
    scored = candle_score(frame, 30)
    assert scored["open_integrity"] == "UNKNOWN"
    assert "unavailable" not in " ".join(scored["reasons"])


def test_the_refusal_lifts_when_the_open_is_observed():
    # It has to start working again on its own the day a real Open arrives,
    # rather than needing someone to remember to switch it back on.
    frame = _observed()
    frame.attrs["open_integrity"] = classify_open(frame)
    scored = candle_score(frame, 30)
    assert scored["open_integrity"] == OpenIntegrity.OBSERVED.value
    assert "unavailable" not in " ".join(scored["reasons"])


def test_engulfing_and_harami_are_unreachable_on_a_carried_forward_open():
    # Both require Open to differ from the previous Close in opposite
    # directions, so both reduce to x < x. Neither can ever fire on this data.
    frame = _carried_forward(n=160)
    cited = [reason for i in range(2, len(frame))
             for reason in candle_score(frame, i)["reasons"]]
    assert "Bullish Engulfing" not in cited
    assert "Bullish Harami" not in cited
