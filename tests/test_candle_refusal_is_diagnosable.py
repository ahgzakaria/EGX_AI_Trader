"""When candle confirmation blocks everything, the output must say why.

Two correct changes combined into a defect that neither caused alone.

`candle_score` refuses to score a fabricated Open and explains itself — "Candle
patterns unavailable (Open OUT_OF_RANGE)". Separately, `candles["reasons"]` was
removed from the decision's reasons, because the pattern strings it used to emit
described things that had not happened.

Together: turn `require_candle_confirmation` on with a fabricated Open and every
symbol fails the gate, reporting `CandleConfirmation: FAIL` and `Candles: 0`
with **no explanation anywhere** — while the explanation exists and is discarded
one layer before display. The toggle lives in the settings UI and reads like a
safety feature, so someone enables it and then debugs "no signals today" against
a string that was thrown away.

Found by the session that audited the open field. These tests pin the fix: the
refusal reason is restored exactly when the gate it explains has failed, and the
pattern strings stay gone.
"""

import pandas as pd
import pytest

import strategy.decision_engine as decision_engine
from core.daily_open_integrity import classify_open


def _fabricated_frame(rows=320):
    """Rising series whose Open is always the previous Close, as the feed is."""
    opens, highs, lows, closes, close = [], [], [], [], 100.0
    for step in range(rows):
        open_ = close
        close = open_ * (1.004 if step % 3 else 0.997)
        opens.append(open_)
        closes.append(close)
        highs.append(max(open_, close) * 1.004)
        lows.append(min(open_, close) * 0.996)
    frame = pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows, "Close": closes,
        "Volume": [500_000.0] * rows,
    }, index=pd.date_range("2024-01-01", periods=rows, freq="B"))
    frame.attrs["open_integrity"] = classify_open(frame)
    return frame


@pytest.fixture
def frame():
    built = _fabricated_frame()
    assert built.attrs["open_integrity"].verdict.value == "CARRIED_FORWARD"
    return built


def _evaluate(frame, monkeypatch, require_candles):
    from indicators.technical import calculate_indicators

    prepared = calculate_indicators(frame.copy())
    prepared.attrs["open_integrity"] = frame.attrs["open_integrity"]
    monkeypatch.setattr(
        decision_engine, "analyze_market",
        lambda *_a, **_k: {"Passed": True, "Regime": "BULL", "Reasons": []})
    cfg = decision_engine.config.load()
    monkeypatch.setattr(cfg, "REQUIRE_CANDLE_CONFIRMATION", require_candles,
                        raising=False)
    monkeypatch.setattr(decision_engine.config, "load", lambda: cfg)
    return decision_engine.evaluate(prepared, len(prepared) - 1)


def test_the_gate_fails_on_a_fabricated_open(frame, monkeypatch):
    result = _evaluate(frame, monkeypatch, require_candles=True)
    assert result["Candles"] == 0
    assert result["DecisionTrace"]["CandleConfirmation"] == "FAIL"


def test_a_failing_gate_explains_itself(frame, monkeypatch):
    # The defect: this was FAIL with nothing in Reasons to diagnose it.
    result = _evaluate(frame, monkeypatch, require_candles=True)
    joined = " ".join(result["Reasons"])
    assert "unavailable" in joined
    assert "Open" in joined


def test_the_explanation_is_absent_when_the_gate_is_not_enforced(
        frame, monkeypatch):
    # Otherwise every signal in normal operation carries a note about a gate
    # nobody switched on.
    result = _evaluate(frame, monkeypatch, require_candles=False)
    assert "unavailable" not in " ".join(result["Reasons"])


def test_pattern_names_never_return(frame, monkeypatch):
    # The reason strings come back; the fictions do not.
    for require in (True, False):
        joined = " ".join(_evaluate(frame, monkeypatch, require)["Reasons"])
        for pattern in ("Morning Star", "Doji", "Hammer", "Engulfing", "Harami"):
            assert pattern not in joined
