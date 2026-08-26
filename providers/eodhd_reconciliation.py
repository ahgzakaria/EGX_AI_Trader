"""EODHD ↔ internal reconciliation classifiers + audit categories (pure logic).

Evidence-based classification only — no symbol is mapped by suffix replacement alone,
no unmapped symbol is silently mapped, and no extra symbol is auto-added. Fuzzy name
similarity is never a *final* confirmation; it only proposes a candidate for review.
"""

from __future__ import annotations

# unmapped classifications
ACTIVE_EQUITY_DIFFERENT_TICKER = "ACTIVE_EQUITY_DIFFERENT_TICKER"
RENAMED_SYMBOL = "RENAMED_SYMBOL"
DELISTED = "DELISTED"
SUSPENDED = "SUSPENDED"
RIGHTS_ISSUE = "RIGHTS_ISSUE"
FUND_OR_ETF = "FUND_OR_ETF"
USD_DENOMINATED_SECURITY = "USD_DENOMINATED_SECURITY"
OTC_OR_SPECIAL_MARKET = "OTC_OR_SPECIAL_MARKET"
DUPLICATE_INTERNAL_ENTRY = "DUPLICATE_INTERNAL_ENTRY"
NOT_SUPPORTED_BY_EODHD = "NOT_SUPPORTED_BY_EODHD"
MANUAL_REVIEW = "MANUAL_REVIEW"

# audit agreement categories
CLEAN_MATCH = "CLEAN_MATCH"
MINOR_ROUNDING_DIFFERENCE = "MINOR_ROUNDING_DIFFERENCE"
#: The two series overlap too thinly in the compared window to judge agreement.
#: Distinct from a real disagreement: it says the measurement could not be made,
#: not that the providers differ.
INSUFFICIENT_OVERLAP = "INSUFFICIENT_OVERLAP"
#: Fewer recent sessions than this and no agreement verdict is defensible. Ten
#: is roughly two trading weeks; below it a single odd print dominates the max.
MIN_RECENT_SESSIONS = 10
ADJUSTMENT_CONVENTION_DIFFERENCE = "ADJUSTMENT_CONVENTION_DIFFERENCE"
HISTORY_COVERAGE_DIFFERENCE = "HISTORY_COVERAGE_DIFFERENCE"
VOLUME_DIFFERENCE = "VOLUME_DIFFERENCE"
PRICE_SCALE_ANOMALY = "PRICE_SCALE_ANOMALY"
STALE_PROVIDER_DATA = "STALE_PROVIDER_DATA"
MAPPING_ERROR = "MAPPING_ERROR"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"

_ETF_HINTS = ("ETF", "INDEX", "FUND", "SUKUK")


def classify_unmapped(ticker, *, in_rubix, egx_search_candidates, name_hint=""):
    """Classify one unmapped internal ticker from concrete evidence.

    in_rubix: is the ticker actively quoted by the live Rubix feed?
    egx_search_candidates: EODHD search hits whose Exchange == 'EGX' (list of dicts).
    """
    t = str(ticker).upper()
    hint = f"{t} {name_hint}".upper()
    if any(h in hint for h in _ETF_HINTS) or t.endswith("ETF"):
        return {
            "classification": FUND_OR_ETF, "confidence": 0.95,
            "candidate_eodhd_symbol": "", "candidate_company_name": "",
            "evidence": "ticker/name indicates an index ETF/fund; EODHD EGX list is "
                        "common-stock only",
            "action_required": "exclude from EODHD equity coverage; keep on current provider",
        }
    if egx_search_candidates:
        best = egx_search_candidates[0]
        return {
            "classification": ACTIVE_EQUITY_DIFFERENT_TICKER, "confidence": 0.5,
            "candidate_eodhd_symbol": f"{best.get('Code')}.EGX",
            "candidate_company_name": best.get("Name", ""),
            "evidence": f"EODHD EGX search returned {best.get('Code')} "
                        f"(ISIN {best.get('Isin')}); verify identity before mapping",
            "action_required": "MANUAL_REVIEW: confirm this is the same security (ISIN/name)",
        }
    if in_rubix:
        return {
            "classification": NOT_SUPPORTED_BY_EODHD, "confidence": 0.9,
            "candidate_eodhd_symbol": "", "candidate_company_name": "",
            "evidence": "actively quoted by Rubix (currently trading on EGX) but absent "
                        "from the EODHD EGX symbol list AND EODHD search — a coverage gap",
            "action_required": "keep on current provider (Yahoo/Rubix); not available via EODHD",
        }
    return {
        "classification": MANUAL_REVIEW, "confidence": 0.3,
        "candidate_eodhd_symbol": "", "candidate_company_name": "",
        "evidence": "not in EODHD EGX list/search and not currently in Rubix — possibly "
                    "delisted/suspended; needs manual confirmation",
        "action_required": "MANUAL_REVIEW: confirm delisted vs renamed vs internal error",
    }


def classify_extra(code, eodhd_row, *, in_rubix, internal_codes):
    """Classify an EODHD EGX code that is not in the internal universe."""
    code = str(code).upper()
    name = str((eodhd_row or {}).get("Name", ""))
    itype = str((eodhd_row or {}).get("Type", ""))
    isin = str((eodhd_row or {}).get("Isin", ""))
    if code in ("NULL", "") or not isin:
        return {"classification": MAPPING_ERROR, "confidence": 0.9,
                "evidence": f"placeholder/invalid code '{code}' or missing ISIN",
                "action_required": "ignore; do not add"}
    # alternate share class: stem matches an internal code (e.g. SEIGA→SEIG)
    stem = code[:-1]
    if len(code) >= 4 and stem in internal_codes:
        return {"classification": DUPLICATE_INTERNAL_ENTRY, "confidence": 0.5,
                "evidence": f"possible alternate share class of internal {stem} "
                            f"(EODHD {code}, {name})",
                "action_required": "MANUAL_REVIEW: confirm share class before any use"}
    if any(h in name.upper() for h in _ETF_HINTS):
        return {"classification": FUND_OR_ETF, "confidence": 0.8,
                "evidence": f"name indicates a fund/ETF ({name})",
                "action_required": "not appropriate for the equity scanner"}
    return {"classification": ACTIVE_EQUITY_DIFFERENT_TICKER, "confidence": 0.5,
            "evidence": f"EODHD active EGX {itype} '{name}' (ISIN {isin}) absent from "
                        f"internal universe/Rubix",
            "action_required": "MANUAL_REVIEW: candidate to add to universe later; do not auto-add"}


# --- price-scale + agreement audit ------------------------------------------

def scale_bucket(ratio):
    """Nearest suspicious scale bucket for a price ratio, or 'NORMAL'."""
    if ratio is None or ratio <= 0:
        return "UNKNOWN"
    for target, label in ((100, "~100x"), (10, "~10x"), (1, "~1x"), (0.1, "~0.1x"),
                          (0.01, "~0.01x")):
        if abs(ratio - target) / target <= 0.15:
            return label if target != 1 else "NORMAL"
    return "IRREGULAR"


def is_scale_anomaly(ratio):
    """True for a near-exact 10/100/0.1/0.01 scale OR any gross systematic offset.

    A recent-window (≈1y, split-free) median close ratio that is far from 1 signals a
    scale/currency/listing/instrument mismatch (e.g. ORAS ≈6.6×, MEGM ≈3.5×), not a
    rounding difference. Splits are rare within a single year, so this does not
    routinely mis-flag adjustment-convention cases (those are checked separately).
    """
    if ratio is None or ratio <= 0:
        return False
    if scale_bucket(ratio) in ("~100x", "~10x", "~0.1x", "~0.01x"):
        return True
    return ratio >= 1.5 or ratio <= (1 / 1.5)


def categorize_agreement(m):
    """Category from a symbol's recent-window metrics dict.

    Keys used: common_sessions, mean_close_pct_diff, max_close_pct_diff,
    close_scale_ratio, mean_volume_pct_diff, max_adj_ratio_gap, eodhd_error,
    eodhd_rows, yahoo_rows.
    """
    if m.get("eodhd_error") or m.get("eodhd_rows", 0) == 0 or m.get("yahoo_rows", 0) == 0:
        return DATA_UNAVAILABLE
    if m.get("common_sessions", 0) == 0:
        return DATA_UNAVAILABLE
    # A scale anomaly is measured over the whole overlap, so it stays judgeable
    # even when the recent window is thin.
    if is_scale_anomaly(m.get("close_scale_ratio")):
        return PRICE_SCALE_ANOMALY
    # The percentage metrics below are computed over a recent window only. If
    # that window contains almost nothing -- which happens when one series has
    # stopped advancing and the other has not -- the numbers describe the edge
    # of the stale series, not a disagreement between providers.
    recent = m.get("recent_sessions_compared")
    if recent is not None and int(recent) < MIN_RECENT_SESSIONS:
        return INSUFFICIENT_OVERLAP
    mean_pct = float(m.get("mean_close_pct_diff") or 0.0)
    max_pct = float(m.get("max_close_pct_diff") or 0.0)
    if mean_pct <= 0.10 and max_pct <= 0.50:
        return CLEAN_MATCH
    if mean_pct <= 0.50:
        return MINOR_ROUNDING_DIFFERENCE
    if float(m.get("max_adj_ratio_gap") or 0.0) > 0.02:
        return ADJUSTMENT_CONVENTION_DIFFERENCE
    return MANUAL_REVIEW
