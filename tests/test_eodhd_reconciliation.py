"""Tests for EODHD reconciliation classifiers + audit categories (pure logic)."""

from __future__ import annotations

from providers.eodhd_reconciliation import (
    classify_extra,
    classify_unmapped,
    categorize_agreement,
    is_scale_anomaly,
    scale_bucket,
)


# --- unmapped classification ------------------------------------------------

def test_active_in_rubix_absent_from_eodhd_is_coverage_gap():
    c = classify_unmapped("MISR", in_rubix=True, egx_search_candidates=[])
    assert c["classification"] == "NOT_SUPPORTED_BY_EODHD" and c["confidence"] >= 0.8


def test_etf_ticker_classified_fund():
    c = classify_unmapped("EGX30ETF", in_rubix=True, egx_search_candidates=[])
    assert c["classification"] == "FUND_OR_ETF"


def test_egx_search_hit_is_only_a_review_candidate_not_final():
    # a search hit proposes a candidate but must require manual review (never auto-map)
    c = classify_unmapped("NBKE", in_rubix=True,
                          egx_search_candidates=[{"Code": "NBK", "Name": "Nat Bank", "Isin": "EG..."}])
    assert c["classification"] == "ACTIVE_EQUITY_DIFFERENT_TICKER"
    assert c["candidate_eodhd_symbol"] == "NBK.EGX"
    assert "MANUAL_REVIEW" in c["action_required"] and c["confidence"] <= 0.6


def test_not_in_rubix_and_not_in_eodhd_is_manual_review():
    c = classify_unmapped("GONE", in_rubix=False, egx_search_candidates=[])
    assert c["classification"] == "MANUAL_REVIEW"


def test_unmapped_never_silently_mapped():
    # no path returns VERIFIED_* — an unmapped symbol is never auto-mapped
    for rub in (True, False):
        c = classify_unmapped("XXXX", in_rubix=rub, egx_search_candidates=[])
        assert "VERIFIED" not in c["classification"]


# --- extra classification ---------------------------------------------------

def _row(name="Some Co", isin="EGS123", itype="Common Stock"):
    return {"Name": name, "Isin": isin, "Type": itype, "Currency": "EGP"}


def test_extra_active_equity_flagged_for_review_not_auto_added():
    c = classify_extra("AUTO", _row("GB Corp", "EGS673T1C012"),
                       in_rubix=False, internal_codes={"COMI", "SWDY"})
    assert c["classification"] == "ACTIVE_EQUITY_DIFFERENT_TICKER"
    assert "do not auto-add" in c["action_required"]


def test_extra_null_code_is_mapping_error():
    c = classify_extra("NULL", {"Name": "", "Isin": ""}, in_rubix=False, internal_codes=set())
    assert c["classification"] == "MAPPING_ERROR"


def test_extra_alternate_share_class_detected():
    c = classify_extra("SEIGA", _row("Seig Alt", "EGS999"), in_rubix=False,
                       internal_codes={"SEIG"})
    assert c["classification"] == "DUPLICATE_INTERNAL_ENTRY"


# --- scale anomaly ----------------------------------------------------------

def test_scale_buckets():
    assert scale_bucket(10.12) == "~10x"        # ORAS-style
    assert scale_bucket(100.0) == "~100x"
    assert scale_bucket(0.1) == "~0.1x"
    assert scale_bucket(0.0102) == "~0.01x"
    assert scale_bucket(1.001) == "NORMAL"
    assert scale_bucket(2.5) == "IRREGULAR"


def test_oras_10x_is_anomaly_normal_is_not():
    assert is_scale_anomaly(10.12) is True
    assert is_scale_anomaly(1.0) is False
    assert is_scale_anomaly(1.003) is False
    # irregular gross offsets (ORAS ~6.6x, MEGM ~3.5x) are anomalies too
    assert is_scale_anomaly(6.63) is True
    assert is_scale_anomaly(3.50) is True
    assert is_scale_anomaly(0.5) is True        # inverse gross offset


# --- agreement categories ---------------------------------------------------

def test_categorize_clean_and_minor():
    assert categorize_agreement({"eodhd_rows": 5, "yahoo_rows": 5, "common_sessions": 5,
                                 "mean_close_pct_diff": 0.02, "max_close_pct_diff": 0.1,
                                 "close_scale_ratio": 1.0}) == "CLEAN_MATCH"
    assert categorize_agreement({"eodhd_rows": 5, "yahoo_rows": 5, "common_sessions": 5,
                                 "mean_close_pct_diff": 0.3, "max_close_pct_diff": 0.8,
                                 "close_scale_ratio": 1.0}) == "MINOR_ROUNDING_DIFFERENCE"


def test_categorize_scale_anomaly_wins():
    assert categorize_agreement({"eodhd_rows": 5, "yahoo_rows": 5, "common_sessions": 5,
                                 "mean_close_pct_diff": 0.0, "max_close_pct_diff": 0.0,
                                 "close_scale_ratio": 10.1}) == "PRICE_SCALE_ANOMALY"


def test_categorize_data_unavailable():
    assert categorize_agreement({"eodhd_rows": 0, "yahoo_rows": 5,
                                 "common_sessions": 0}) == "DATA_UNAVAILABLE"


def test_categorize_adjustment_convention():
    m = {"eodhd_rows": 5, "yahoo_rows": 5, "common_sessions": 5, "mean_close_pct_diff": 1.2,
         "max_close_pct_diff": 3.0, "close_scale_ratio": 1.0, "max_adj_ratio_gap": 0.5}
    assert categorize_agreement(m) == "ADJUSTMENT_CONVENTION_DIFFERENCE"
