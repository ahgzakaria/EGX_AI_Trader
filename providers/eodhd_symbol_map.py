"""Internal-universe ↔ EODHD EGX symbol mapping (verified against the real list).

Mappings are produced by verifying each internal base ticker against the EODHD
exchange-symbol list — never by blind suffix replacement. Pure, testable logic; the
audit script supplies the fetched EODHD rows.
"""

from __future__ import annotations

EQUITY_TYPES = {"common stock", "preferred stock", "stock", "etf", "fund", "unit", "reit"}

# statuses
VERIFIED_EXACT = "VERIFIED_EXACT"
VERIFIED_NAME_MATCH = "VERIFIED_NAME_MATCH"
MANUAL_REVIEW = "MANUAL_REVIEW"
NOT_FOUND = "NOT_FOUND"
DUPLICATE = "DUPLICATE"
NON_EQUITY = "NON_EQUITY"
DELISTED_OR_UNSUPPORTED = "DELISTED_OR_UNSUPPORTED"


def _base(symbol):
    return str(symbol).strip().upper().split(".")[0]


def build_mapping(internal_symbols, eodhd_rows, *, exchange="EGX"):
    """Return (rows, summary).

    internal_symbols: iterable of internal tickers (e.g. 'COMI.CA' or 'COMI').
    eodhd_rows: list of dicts from EODHD exchange-symbol-list (Code, Name, Type, ...).
    """
    eodhd_by_code = {}
    for r in eodhd_rows or []:
        if isinstance(r, dict) and r.get("Code"):
            eodhd_by_code[str(r["Code"]).strip().upper()] = r

    rows = []
    seen_targets = {}
    for internal in internal_symbols:
        base = _base(internal)
        internal_ca = f"{base}.CA"
        eodhd_match = eodhd_by_code.get(base)
        row = {
            "internal_symbol": base,
            "internal_symbol_ca": internal_ca,
            "eodhd_symbol": f"{base}.{exchange}" if eodhd_match else "",
            "company_name_internal": "",           # internal universe carries tickers only
            "company_name_eodhd": (eodhd_match or {}).get("Name", ""),
            "instrument_type": (eodhd_match or {}).get("Type", ""),
            "mapping_status": "",
            "mapping_method": "",
            "notes": "",
        }
        if eodhd_match is None:
            row["mapping_status"] = NOT_FOUND
            row["mapping_method"] = "NOT_IN_EODHD_LIST"
            row["notes"] = "internal base has no EODHD EGX code; possibly delisted/unsupported"
        else:
            itype = str(eodhd_match.get("Type", "")).strip().lower()
            if itype and itype not in EQUITY_TYPES:
                row["mapping_status"] = NON_EQUITY
                row["mapping_method"] = "EXACT_CODE"
                row["notes"] = f"EODHD type '{eodhd_match.get('Type')}' not treated as equity"
            else:
                row["mapping_status"] = VERIFIED_EXACT
                row["mapping_method"] = "EXACT_CODE"
            target = f"{base}.{exchange}"
            seen_targets.setdefault(target, []).append(base)
        rows.append(row)

    # flag duplicates (two internal bases → same EODHD symbol)
    dup_targets = {t for t, bases in seen_targets.items() if len(bases) > 1}
    for row in rows:
        if row["eodhd_symbol"] in dup_targets and row["mapping_status"] == VERIFIED_EXACT:
            row["mapping_status"] = DUPLICATE
            row["notes"] = "multiple internal symbols map to this EODHD code"

    internal_bases = {_base(s) for s in internal_symbols}
    extra_eodhd = sorted(set(eodhd_by_code) - internal_bases)

    summary = {
        "internal_count": len(rows),
        "eodhd_egx_count": len(eodhd_by_code),
        "verified_exact": sum(1 for r in rows if r["mapping_status"] == VERIFIED_EXACT),
        "name_match": sum(1 for r in rows if r["mapping_status"] == VERIFIED_NAME_MATCH),
        "non_equity": sum(1 for r in rows if r["mapping_status"] == NON_EQUITY),
        "duplicate": sum(1 for r in rows if r["mapping_status"] == DUPLICATE),
        "manual_review": sum(1 for r in rows if r["mapping_status"] == MANUAL_REVIEW),
        "not_found": sum(1 for r in rows if r["mapping_status"] == NOT_FOUND),
        "mapped_total": sum(1 for r in rows if r["eodhd_symbol"]),
        "missing_internal": [r["internal_symbol"] for r in rows if r["mapping_status"] == NOT_FOUND],
        "extra_eodhd_symbols": extra_eodhd,
    }
    return rows, summary
