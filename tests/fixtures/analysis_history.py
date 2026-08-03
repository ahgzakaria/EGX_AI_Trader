"""Deterministic daily-history fixtures for presentation and export tests.

Rendering and export tests validate a PRESENTATION contract. They must not need
an EODHD credential, a network call, a Rubix feed, or the machine-local provider
cache under ``data/eodhd_cache/`` — none of which exist in a fresh worktree, a
clean clone, or CI. Injecting one of these frames through the existing
``analyze_symbol(history_loader=...)`` seam lets the real Core produce a real
``AnalysisResult``, so no assertion has to be weakened to gain portability.

The frame matches ``tests/fixtures/history_frame_contract.json`` exactly:
``Open, High, Low, Close, Adj Close, Volume`` as float64 over a named ``Date``
DatetimeIndex, no NaN, with a ``market_data`` attrs block.

Values come from integer triangle waves rounded to four decimals, so every
close is bit-identical on every platform. Nothing here derives from a captured
provider response, and no credential or cache metadata is present.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


#: Enough sessions for the 200-period indicators the analysis Core computes.
DEFAULT_SESSION_COUNT = 260

#: The last completed session every fixture frame ends on. Fixed so exported
#: filenames and identity hashes stay deterministic.
DEFAULT_LAST_SESSION = "2026-07-30"


#: Phase offset of the slow wave at the final session. Every offset from 3 to 17
#: yields the same pullback verdict, so the midpoint keeps the fixture well
#: inside a stable plateau rather than balanced on a threshold.
_PHASE = 10


def _close(index: int) -> float:
    """A range-bound close with no sustained directional trend.

    Two integer triangle waves of different periods. Integer arithmetic and a
    four-decimal round keep this exactly reproducible on any platform, unlike
    ``math.sin``. The shape is deliberately choppy: it gives the Core genuine
    swing highs, ATR and support/resistance to work with while never forming
    the clean prior uptrend a pullback setup requires — so the Core reports a
    real, fully-populated *invalidated* pullback diagnosis.
    """

    position = index + _PHASE
    slow = abs((position % 28) - 14) - 7       # -7 .. +7
    fast = abs((position % 10) - 5) - 2.5      # -2.5 .. +2.5
    return round(18.0 + slow * 0.13 + fast * 0.14, 4)


def _volume(index: int) -> float:
    """Mixed but bounded participation, so volume never reads as one-sided."""

    return float(900_000 + (index % 7) * 55_000 + (index % 3) * 30_000)


def build_history_frame(
    *,
    sessions: int = DEFAULT_SESSION_COUNT,
    last_session: str = DEFAULT_LAST_SESSION,
    provider: str = "eodhd",
) -> pd.DataFrame:
    """Return a contract-shaped daily OHLCV frame ending on ``last_session``."""

    if sessions < 1:
        raise ValueError("sessions must be positive")
    index = pd.bdate_range(
        end=pd.Timestamp(last_session), periods=sessions, name="Date"
    )
    close = pd.Series(
        [_close(position) for position in range(sessions)], dtype=float, index=index
    )
    volume = pd.Series(
        [_volume(position) for position in range(sessions)], dtype=float, index=index
    )
    frame = pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]).astype(float),
            "High": (close * 1.012).round(4).astype(float),
            "Low": (close * 0.988).round(4).astype(float),
            "Close": close,
            "Adj Close": close,
            "Volume": volume,
        },
        index=index,
    )
    assert not frame.isna().to_numpy().any(), "fixture frame must contain no NaN"
    assert frame.dtypes.eq(np.dtype("float64")).all(), "fixture frame must be float64"
    frame.attrs["market_data"] = {
        "data_domain": "CURRENT_RESEARCH_V2",
        "provider": provider,
        "effective_provider": provider,
        "requested_provider": provider,
        "purpose": "analysis",
        "fallback_active": False,
        "delayed": False,
        "provider_status": "OK",
        "price_series": "SPLIT_ADJUSTED",
        "price_adjustment_policy": "SPLIT_ADJUSTED_ALL_EVENTS",
        "volume_series": "RAW_EODHD",
        "volume_adjustment_policy": "NONE",
        "volume_safe_for_lookback": True,
        "latest_action_in_lookback": None,
        "freshness_status": "HISTORY_CURRENT",
        "latest_completed_session": last_session,
        "expected_completed_session": last_session,
        "history_sufficient": sessions >= 200,
        "yahoo_network_used": False,
        "yahoo_seed_present": False,
        "routing_tier": "TIER_A_FORWARD_SAFE",
        "live_provider": "rubix",
    }
    return frame


def analysis_response(
    symbol: str = "FWRY",
    *,
    as_of: str = DEFAULT_LAST_SESSION,
    sessions: int = DEFAULT_SESSION_COUNT,
):
    """Run the real analysis Core over a fixture frame, with no I/O of any kind.

    Every external seam is stubbed: history comes from the fixture, the live
    quote and intraday providers return nothing, and the narrative is the
    deterministic fallback. The evidence, indicators, levels, scenarios and
    pullback diagnostics are all genuinely computed by the Core.
    """

    from core.ai_analysis_narrative import build_fallback_narrative
    from core.ai_stock_analysis_service import analyze_symbol

    frame = build_history_frame(sessions=sessions, last_session=as_of)
    return analyze_symbol(
        symbol,
        as_of=as_of,
        history_loader=lambda _symbol: frame,
        live_quote_provider=lambda _symbol: None,
        intraday_provider=lambda *_args, **_kwargs: None,
        narrative_generator=build_fallback_narrative,
    )


def analysis_presentation(symbol: str = "FWRY", *, as_of: str = DEFAULT_LAST_SESSION):
    """The fixture ``AnalysisPresentation`` used by the rendering/export tests."""

    from core.analysis_presentation import build_presentation

    response = analysis_response(symbol, as_of=as_of)
    return build_presentation(response.result, response.narrative)


__all__ = [
    "DEFAULT_LAST_SESSION",
    "DEFAULT_SESSION_COUNT",
    "analysis_presentation",
    "analysis_response",
    "build_history_frame",
]
