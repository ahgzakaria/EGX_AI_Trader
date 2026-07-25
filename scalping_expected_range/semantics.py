"""Range semantics for EXPECTED_RANGE_SCALPER.

The expected-range bands are STATISTICAL REFERENCES, not guaranteed support/
resistance. Historical full-session containment is low (approx: conservative 2%,
base/p50 15%, high-volatility/p75 48%), so a move above a band High is NOT
automatically a breakout — scenario confirmation must use live price, Bid/Ask,
spread, activity and observable behaviour.

Public semantic names map onto the existing percentile bands WITHOUT changing any
percentile:

    CORE / MEDIAN EXCURSION RANGE  <- base (p50)  central expected excursion
    EXPANSION RANGE                <- high_volatility (p75)  wider movement band
    EXTREME RANGE                  <- extreme (max)  high-volatility extension
"""

from __future__ import annotations

SEMANTIC_TO_BAND = {
    "core": "base",
    "expansion": "high_volatility",
    "extreme": "extreme",
}

SEMANTIC_LABELS = {
    "core": "CORE / MEDIAN EXCURSION RANGE",
    "expansion": "EXPANSION RANGE",
    "extreme": "EXTREME RANGE",
}

DISCLAIMER = (
    "Range boundaries are statistical references, not guaranteed support/resistance; "
    "a move above the Core/Expansion High is not automatically a breakout. Confirm "
    "scenarios with live price, Bid/Ask, spread and observable behaviour."
)


def semantic_bounds(expected):
    """Return {'core': (low, high), 'expansion': (...), 'extreme': (...)}.

    Missing bands yield (None, None). Never mutates the ExpectedRange.
    """
    out = {}
    for semantic, band_name in SEMANTIC_TO_BAND.items():
        band = getattr(expected, band_name, None) if expected is not None else None
        out[semantic] = (band.expected_low, band.expected_high) if band else (None, None)
    return out


def semantic_band(expected, semantic):
    band_name = SEMANTIC_TO_BAND.get(semantic)
    return getattr(expected, band_name, None) if (expected and band_name) else None
