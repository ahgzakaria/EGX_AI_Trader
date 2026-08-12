"""PHASE 1 — READ-ONLY reconciliation of the EGX sector workbook against the
canonical universe, corroborated by Rubix live-feed observation evidence.

THIS SCRIPT HAS NO WRITE PATH. It is a reporter, not an importer-with-apply.

Deliberately absent, and absent by design rather than by omission:

* no ``--apply`` flag, and no code that could implement one;
* no write to ``data/universe/egx_universe.csv``;
* no write to ``data/universe/universe_changes.csv`` (that file is Phase 2's
  concern and is not created here);
* no SQLite connection opened in anything but ``mode=ro`` with
  ``PRAGMA query_only=1`` set on every handle;
* no ``open()`` in a writing mode anywhere in this module;
* no automatic resolution of any identity conflict. Every discrepancy is
  emitted verbatim and marked ``UNRESOLVED_REPORT_ONLY``.

The report goes to stdout. Redirect it if you want it on disk; this module will
not choose a destination inside the project for you.

Determinism: the output contains no wall-clock timestamp, so two consecutive
runs over unchanged inputs are byte-identical. Provenance is carried instead by
SHA-256 digests of the two small inputs and by the Rubix feed watermark, which
are facts about the data rather than about the run.

Source authority, applied exactly as Phase 1 defines it:

    workbook   -> ticker identity, company name, sector, sector rank,
                  overall rank, reported market cap, listing status
    canonical  -> current repository membership state, EODHD metadata,
                  existing Rubix mapping state
    Rubix      -> observation evidence ONLY (was a ticker seen on the feed)
    EODHD      -> ISIN/currency/type metadata already in the repository; never
                  an authority on current ticker identity
    Yahoo      -> not consulted, not imported, not referenced

Observation is evidence. It never authorizes a universe change.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.universe import UNIVERSE_SOURCE, canonical, load_universe

#: This module cannot write. The constant exists so the intent is greppable and
#: so a future reader does not have to prove the absence of an apply path.
WRITE_MODE_SUPPORTED = False

DEFAULT_WORKBOOK = r"D:\EGX_00\EGX_Active_Stocks_By_Sector_Market_Cap.xlsx"
DEFAULT_RUBIX_DB = "data/rubix_live_market.db"

#: Sheets that are not sector tabs.
NON_SECTOR_SHEETS = frozenset(
    {"Overview", "All_Stocks", "Sector_Mapping", "Exceptions", "Data_Sources"}
)

EXPECTED_SECTOR_COUNTS = {
    "Non-bank Financial Services": 35,
    "Real Estate": 34,
    "Food and Beverages": 30,
    "Health Care & Pharmaceuticals": 20,
    "Basic Resources": 18,
    "Building Materials": 14,
    "Construction": 14,
    "Travel & Leisure": 14,
    "Banks": 13,
    "Industrial Goods, Services, and Automobiles": 9,
    "Textile & Durable Goods": 9,
    "IT, Media, and Communication Services": 8,
    "Trade & Distributors": 7,
    "Education Services": 6,
    "Energy & Support Services": 6,
    "Paper & Packaging": 6,
    "Shipping & Transportation Services": 5,
    "Utilities": 2,
}
EXPECTED_WORKBOOK_ROWS = 250
EXPECTED_SECTOR_COUNT = 18

# --- Phase 1 named investigations (sections E, F, G, H of the brief) ----------
# These are the cases the brief names explicitly. They are constants so the
# report always covers them even if a future data refresh makes one of them
# stop being anomalous — a case silently disappearing is itself a finding.
INVESTIGATE_RUBIX_OBSERVED_WORKBOOK_MISSING = ("FTNS", "HBCO", "UTOP")
INVESTIGATE_STATUS_DISAGREEMENT = ("AIFI", "AIHC", "ELWA")
INVESTIGATE_ISIN_COLLISION = "EGS3E1E1C013"
INVESTIGATE_STALE_ACTIVE = (
    "DEIN", "GPPL", "ICLE", "MEGM", "NDRL", "SAIB", "SIMO", "SPHT",
)
KNOWN_CONFLICT_PAIRS = (
    ("CNFN", "SRWA"), ("ASPI", "PIOH"), ("OIH", "ORMT"), ("GBCO", "AUTO"),
    ("QNBE", "QNBA"), ("AMIA", "AGIG"), ("MEPA", "MEDP"), ("CRST", "EDBM"),
    ("AIFI", "ALRA"), ("AIHC", "AIND"), ("MMAT", "MATD"), ("EKHO", "VLMR"),
)

UNRESOLVED = "UNRESOLVED_REPORT_ONLY"


# --- read-only plumbing ------------------------------------------------------

def _connect_read_only(path):
    """The ONLY database entry point in this module.

    ``mode=ro`` refuses to create or write the file; ``query_only`` makes the
    connection reject any statement that would mutate it. Rubix is a live
    production database with a very large WAL — this never checkpoints it,
    never VACUUMs it, and never touches its WAL/SHM beyond the shared-memory
    index every reader (including the collector's own readers) must map.
    """

    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"database not found: {resolved}")
    uri = f"file:{resolved.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=20.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=1")
    return connection


def _sha256(path):
    digest = hashlib.sha256()
    with open(Path(path), "rb") as handle:            # 'rb' — read only
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _norm_name(value):
    """Collapse a company name for comparison only. Never written anywhere."""

    text = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper())
    return " ".join(text.split())


def _text(value):
    return "" if value is None else str(value).strip()


# --- workbook ----------------------------------------------------------------

@dataclass(frozen=True)
class WorkbookRow:
    overall_rank: object
    sector_rank: object
    ticker: str
    company: str
    isin: str
    market_cap: object
    market_cap_bn: object
    last_price: object
    shares: object
    price_date: str
    market_cap_date: str
    active_status: str
    sector: str
    verification: str
    notes: str


def load_workbook_rows(path):
    """Read ``All_Stocks`` plus the 18 sector tabs. Opens the file read-only."""

    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheets = list(workbook.sheetnames)

    rows = list(workbook["All_Stocks"].iter_rows(values_only=True))
    header = [_text(cell) for cell in rows[0]]
    records = []
    for raw in rows[1:]:
        if all(cell is None or _text(cell) == "" for cell in raw):
            continue
        records.append(WorkbookRow(
            overall_rank=raw[0], sector_rank=raw[1],
            ticker=_text(raw[2]), company=_text(raw[3]), isin=_text(raw[4]),
            market_cap=raw[5], market_cap_bn=raw[6], last_price=raw[7],
            shares=raw[8], price_date=_text(raw[9]), market_cap_date=_text(raw[10]),
            active_status=_text(raw[11]), sector=_text(raw[12]),
            verification=_text(raw[17]) if len(raw) > 17 else "",
            notes=_text(raw[18]) if len(raw) > 18 else "",
        ))

    sector_tabs = {}
    for name in sheets:
        if name in NON_SECTOR_SHEETS:
            continue
        tab = []
        for raw in list(workbook[name].iter_rows(values_only=True))[1:]:
            if all(cell is None or _text(cell) == "" for cell in raw):
                continue
            # Sector tabs carry the same columns minus 'Rank (Overall)', so
            # Rank IS the sector rank and every index shifts left by one.
            tab.append((raw[0], _text(raw[1]), _text(raw[11])))
        sector_tabs[name] = tab

    exceptions = []
    for raw in list(workbook["Exceptions"].iter_rows(values_only=True))[1:]:
        if raw[0] is None:
            continue
        exceptions.append((_text(raw[0]), _text(raw[1]), _text(raw[2]), _text(raw[3])))

    mapping = []
    for raw in list(workbook["Sector_Mapping"].iter_rows(values_only=True))[1:]:
        if raw[0] is None or raw[1] is None:
            continue
        mapping.append((_text(raw[0]), _text(raw[1]), _text(raw[2]), raw[3]))

    workbook.close()
    return records, header, sheets, sector_tabs, exceptions, mapping


# --- Rubix observation evidence ---------------------------------------------

@dataclass(frozen=True)
class RubixEvidence:
    quote_tickers: frozenset
    candle_tickers: frozenset
    watermark: str
    last_observation_dates: tuple
    per_ticker_last: dict

    def observed(self, ticker):
        return ticker in self.quote_tickers or ticker in self.candle_tickers

    @property
    def watermark_date(self):
        return self.watermark[:10]

    def observed_currently(self, ticker):
        """Observed on the feed's most recent day, not merely somewhere in it.

        The database retains pre-migration history, so plain "was seen" mixes
        two different subscription eras together.
        """

        return self.per_ticker_last.get(ticker, "")[:10] == self.watermark_date


def _distinct_tickers(connection, table):
    """Loose index scan.

    ``quotes`` is multi-gigabyte with a 33 GB WAL; a ``SELECT DISTINCT`` would
    scan it. This walks the ticker index instead — one index seek per distinct
    value — and returns in milliseconds.
    """

    query = f"""
        WITH RECURSIVE walk(k) AS (
            SELECT min(ticker) FROM {table}
            UNION ALL
            SELECT (SELECT min(ticker) FROM {table} WHERE ticker > k)
            FROM walk WHERE k IS NOT NULL
        )
        SELECT k FROM walk WHERE k IS NOT NULL
    """
    return frozenset(row[0] for row in connection.execute(query))


def load_rubix_evidence(path):
    connection = _connect_read_only(path)
    try:
        quote_tickers = _distinct_tickers(connection, "quotes")
        candle_tickers = _distinct_tickers(connection, "candles_1m")
        per_ticker_last = {}
        for ticker in sorted(quote_tickers):
            # Indexed on (ticker, market_timestamp): a seek, not a scan.
            row = connection.execute(
                "SELECT max(market_timestamp) FROM quotes WHERE ticker = ?",
                (ticker,),
            ).fetchone()
            per_ticker_last[ticker] = _text(row[0]) if row else ""
        watermark = max(per_ticker_last.values()) if per_ticker_last else ""
        # Dates on which SOME ticker was last seen. Deliberately not labelled
        # "session dates": establishing full session coverage would need a scan
        # of a multi-gigabyte table, which this tool declines to perform.
        last_dates = sorted({value[:10] for value in per_ticker_last.values() if value})
        return RubixEvidence(
            quote_tickers=quote_tickers,
            candle_tickers=candle_tickers,
            watermark=watermark,
            last_observation_dates=tuple(last_dates),
            per_ticker_last=per_ticker_last,
        )
    finally:
        connection.close()


# --- reconciliation ----------------------------------------------------------

@dataclass(frozen=True)
class Conflict:
    kind: str
    isin: str
    workbook_ticker: str
    canonical_ticker: str
    company: str
    workbook_status: str
    canonical_status: str
    workbook_observed: object
    canonical_observed: object
    workbook_observed_currently: object
    canonical_subscribed: object
    evidence: str
    assessment: str
    confidence: str
    independence: str


def _canonical_status(record):
    if record is None:
        return "ABSENT"
    return "ACTIVE" if record.is_active else "ARCHIVED"


def _is_subscribed(record):
    """Whether the collector would actually request this symbol.

    A verified Rubix mapping alone is NOT enough:
    ``core.universe.rubix_subscription_symbols`` emits a key only for ACTIVE
    records (plus archived ones still holding an open position, which is an
    exit-monitoring exception and not a membership claim). An archived row can
    therefore carry ``VERIFIED_FEED_OBSERVED`` from an earlier era and still
    never be requested today — which is exactly the confound that makes its
    feed silence uninformative.
    """

    return bool(record is not None
                and record.is_active
                and record.has_verified_rubix_mapping)


def _assess(workbook_observed, canonical_observed, canonical_subscribed):
    """Classify a conflict from OBSERVATION ALONE.

    This deliberately never emits a word like "rename". Two tickers sharing an
    ISIN where only one carries feed traffic is an observation; concluding a
    corporate action from it is a judgement, and Phase 1 does not make
    judgements.

    The third argument exists because of a circularity that would otherwise
    inflate every confidence rating in this report. A canonical ticker with no
    verified Rubix mapping contributes no subscription key, so the collector
    never requests it and it CANNOT appear on the feed. Its absence is then a
    restatement of the universe's own configuration, not independent evidence
    about which ticker the exchange uses. Only a subscribed-yet-silent ticker
    carries information in the negative direction.

    Returns ``(assessment, confidence, independence)``.
    """

    if workbook_observed and not canonical_observed:
        if canonical_subscribed:
            return (
                "WORKBOOK_ON_FEED__CANONICAL_SUBSCRIBED_BUT_SILENT",
                "HIGH",
                "INDEPENDENT — canonical ticker was requested and never answered",
            )
        return (
            "WORKBOOK_ON_FEED__CANONICAL_NOT_SUBSCRIBED",
            "MEDIUM",
            "NOT INDEPENDENT — canonical ticker is unsubscribed, so its absence "
            "is expected regardless of which ticker is current",
        )
    if canonical_observed and not workbook_observed:
        return (
            "CANONICAL_ON_FEED__WORKBOOK_TICKER_ABSENT",
            "HIGH",
            "INDEPENDENT — contradicts the workbook's ticker identity",
        )
    if workbook_observed and canonical_observed:
        return (
            "BOTH_TICKERS_ON_FEED",
            "LOW",
            "INCONCLUSIVE — both identifiers carry traffic",
        )
    return (
        "NEITHER_TICKER_ON_FEED",
        "LOW",
        "NO EVIDENCE — neither identifier carries traffic",
    )


def reconcile(workbook_rows, canonical_records, rubix):
    workbook_by_ticker = {row.ticker: row for row in workbook_rows}
    workbook_by_isin = {row.isin: row for row in workbook_rows if row.isin}
    workbook_by_name = defaultdict(list)
    for row in workbook_rows:
        workbook_by_name[_norm_name(row.company)].append(row)

    canonical_by_ticker = {r.canonical_symbol: r for r in canonical_records}
    active = [r for r in canonical_records if r.is_active]
    active_by_ticker = {r.canonical_symbol: r for r in active}

    result = {}

    # --- B1..B3: membership set differences -------------------------------
    result["overlap"] = sorted(set(workbook_by_ticker) & set(active_by_ticker))
    result["workbook_absent_from_canonical"] = sorted(
        t for t in workbook_by_ticker if t not in canonical_by_ticker
    )
    result["workbook_archived_in_canonical"] = sorted(
        t for t in workbook_by_ticker
        if t in canonical_by_ticker and not canonical_by_ticker[t].is_active
    )
    result["canonical_active_absent_from_workbook"] = sorted(
        t for t in active_by_ticker if t not in workbook_by_ticker
    )

    # --- B7/B8: ISIN hygiene inside the canonical file --------------------
    result["canonical_missing_isin"] = sorted(
        r.canonical_symbol for r in canonical_records if not r.isin
    )
    result["canonical_active_missing_isin"] = sorted(
        r.canonical_symbol for r in active if not r.isin
    )
    isin_to_tickers = defaultdict(list)
    for record in canonical_records:
        if record.isin:
            isin_to_tickers[record.isin].append(record.canonical_symbol)
    result["canonical_isin_multi_ticker"] = {
        isin: sorted(tickers)
        for isin, tickers in sorted(isin_to_tickers.items())
        if len(tickers) > 1
    }

    # --- B5: duplicate ACTIVE identity ------------------------------------
    dup_active_isin = defaultdict(list)
    for record in active:
        if record.isin:
            dup_active_isin[record.isin].append(record.canonical_symbol)
    result["duplicate_active_by_isin"] = {
        isin: sorted(tickers)
        for isin, tickers in sorted(dup_active_isin.items())
        if len(tickers) > 1
    }
    dup_active_name = defaultdict(list)
    for record in active:
        dup_active_name[_norm_name(record.company_name)].append(record.canonical_symbol)
    result["duplicate_active_by_name"] = {
        name: sorted(tickers)
        for name, tickers in sorted(dup_active_name.items())
        if len(tickers) > 1 and name
    }

    # --- B4/B6: identity conflicts, discovered rather than assumed --------
    conflicts = []
    seen = set()

    # B6: same ISIN, different ticker.
    for isin, workbook_row in sorted(workbook_by_isin.items()):
        for canonical_ticker in sorted(isin_to_tickers.get(isin, [])):
            if canonical_ticker == workbook_row.ticker:
                continue
            record = canonical_by_ticker[canonical_ticker]
            workbook_observed = rubix.observed(workbook_row.ticker)
            canonical_observed = rubix.observed(canonical_ticker)
            subscribed = _is_subscribed(record)
            assessment, confidence, independence = _assess(
                workbook_observed, canonical_observed, subscribed
            )
            key = (isin, workbook_row.ticker, canonical_ticker)
            if key in seen:
                continue
            seen.add(key)
            conflicts.append(Conflict(
                kind="ISIN_MATCH_TICKER_DIFFERS",
                isin=isin,
                workbook_ticker=workbook_row.ticker,
                canonical_ticker=canonical_ticker,
                company=workbook_row.company,
                workbook_status=workbook_row.active_status,
                canonical_status=_canonical_status(record),
                workbook_observed=workbook_observed,
                canonical_observed=canonical_observed,
                workbook_observed_currently=rubix.observed_currently(
                    workbook_row.ticker
                ),
                canonical_subscribed=subscribed,
                evidence=(
                    f"ISIN identical; canonical company='{record.company_name}'; "
                    f"canonical rubix_mapping={record.rubix_mapping_status}"
                ),
                assessment=assessment,
                confidence=confidence,
                independence=independence,
            ))

    # B4: canonical row whose ISIN is blank but whose NAME matches a workbook
    # row under a different ticker. Weaker evidence than an ISIN match, and
    # reported at lower confidence for exactly that reason.
    for record in canonical_records:
        if record.isin:
            continue
        name = _norm_name(record.company_name)
        if not name:
            continue
        for workbook_row in workbook_by_name.get(name, []):
            if workbook_row.ticker == record.canonical_symbol:
                continue
            key = (workbook_row.isin, workbook_row.ticker, record.canonical_symbol)
            if key in seen:
                continue
            seen.add(key)
            workbook_observed = rubix.observed(workbook_row.ticker)
            canonical_observed = rubix.observed(record.canonical_symbol)
            subscribed = _is_subscribed(record)
            assessment, confidence, independence = _assess(
                workbook_observed, canonical_observed, subscribed
            )
            conflicts.append(Conflict(
                kind="NAME_MATCH_TICKER_DIFFERS_CANONICAL_ISIN_BLANK",
                isin=workbook_row.isin,
                workbook_ticker=workbook_row.ticker,
                canonical_ticker=record.canonical_symbol,
                company=workbook_row.company,
                workbook_status=workbook_row.active_status,
                canonical_status=_canonical_status(record),
                workbook_observed=workbook_observed,
                canonical_observed=canonical_observed,
                workbook_observed_currently=rubix.observed_currently(
                    workbook_row.ticker
                ),
                canonical_subscribed=subscribed,
                evidence=(
                    "canonical ISIN blank; normalized company name identical; "
                    f"canonical rubix_mapping={record.rubix_mapping_status}"
                ),
                assessment=assessment,
                # Name equality alone cannot separate a rename from two
                # genuinely similarly-named companies, so cap the rating.
                confidence="MEDIUM" if confidence == "HIGH" else "LOW",
                independence=independence,
            ))

    result["conflicts"] = sorted(
        conflicts, key=lambda c: (c.kind, c.workbook_ticker, c.canonical_ticker)
    )

    # --- C: Rubix relationships -------------------------------------------
    observed = rubix.quote_tickers | rubix.candle_tickers
    result["rubix_observed_count"] = len(observed)
    result["rubix_observed_in_workbook"] = sorted(observed & set(workbook_by_ticker))
    result["rubix_observed_absent_from_workbook"] = sorted(
        observed - set(workbook_by_ticker)
    )
    result["rubix_observed_absent_from_canonical"] = sorted(
        t for t in observed if canonical(t) not in canonical_by_ticker
    )
    result["rubix_observed_canonical_archived"] = sorted(
        t for t in observed
        if t in canonical_by_ticker and not canonical_by_ticker[t].is_active
    )
    result["workbook_not_observed"] = sorted(set(workbook_by_ticker) - observed)
    result["canonical_active_not_observed"] = sorted(set(active_by_ticker) - observed)

    # --- H: canonical-active rows the workbook dates as stale -------------
    stale_active = []
    for ticker, record in sorted(active_by_ticker.items()):
        workbook_row = workbook_by_ticker.get(ticker)
        if workbook_row is None:
            continue
        if workbook_row.price_date and workbook_row.price_date != "2026-08-10":
            stale_active.append(ticker)
    result["canonical_active_workbook_stale_price"] = stale_active

    # --- coverage: which workbook rows the collector would actually request ---
    subscribed_tickers = {
        r.canonical_symbol for r in canonical_records if _is_subscribed(r)
    }
    result["subscription_set_size"] = len(subscribed_tickers)
    result["workbook_subscribed"] = sorted(
        set(workbook_by_ticker) & subscribed_tickers
    )
    result["workbook_not_subscribed"] = sorted(
        set(workbook_by_ticker) - subscribed_tickers
    )
    result["subscribed_absent_from_workbook"] = sorted(
        subscribed_tickers - set(workbook_by_ticker)
    )
    # A company whose workbook ticker AND whose canonical counterpart are both
    # unsubscribed has no live coverage under either identifier.
    uncovered = []
    for conflict in result["conflicts"]:
        workbook_side = conflict.workbook_ticker in subscribed_tickers
        canonical_side = conflict.canonical_ticker in subscribed_tickers
        if not workbook_side and not canonical_side:
            uncovered.append(conflict)
    result["conflicts_without_coverage"] = uncovered
    result["subscribed_tickers"] = subscribed_tickers

    result["workbook_by_ticker"] = workbook_by_ticker
    result["canonical_by_ticker"] = canonical_by_ticker
    result["active_by_ticker"] = active_by_ticker
    return result


def validate_workbook(rows, sector_tabs, header):
    """Section A. Returns an ordered list of (check, expected, observed, verdict)."""

    checks = []

    def add(name, expected, observed):
        checks.append((name, str(expected), str(observed),
                       "PASS" if str(expected) == str(observed) else "FAIL"))

    add("All_Stocks data rows", EXPECTED_WORKBOOK_ROWS, len(rows))
    add("All_Stocks columns", 19, len(header))

    tickers = [row.ticker for row in rows]
    add("Blank tickers", 0, sum(1 for t in tickers if not t))
    duplicate_tickers = sorted(
        t for t, n in Counter(tickers).items() if n > 1
    )
    add("Duplicate tickers", 0, len(duplicate_tickers))

    isins = [row.isin for row in rows]
    add("Blank ISINs", 0, sum(1 for i in isins if not i))
    duplicate_isins = sorted(i for i, n in Counter(isins).items() if n > 1 and i)
    add("Duplicate ISINs", 0, len(duplicate_isins))
    add("Distinct ISINs", EXPECTED_WORKBOOK_ROWS, len({i for i in isins if i}))

    add("Blank sectors", 0, sum(1 for row in rows if not row.sector))
    sector_counts = Counter(row.sector for row in rows)
    add("Distinct sectors", EXPECTED_SECTOR_COUNT, len(sector_counts))
    mismatched = sorted(
        sector for sector, expected in EXPECTED_SECTOR_COUNTS.items()
        if sector_counts.get(sector, 0) != expected
    )
    add("Sector constituent counts vs expected", 0, len(mismatched))
    unexpected = sorted(set(sector_counts) - set(EXPECTED_SECTOR_COUNTS))
    add("Sectors outside the 18-sector taxonomy", 0, len(unexpected))

    # Sector ranks: contiguous 1..N and ordered by descending market cap.
    rank_problems = []
    cap_order_problems = []
    by_sector = defaultdict(list)
    for row in rows:
        by_sector[row.sector].append(row)
    for sector, members in sorted(by_sector.items()):
        ranks = sorted(m.sector_rank for m in members)
        if ranks != list(range(1, len(members) + 1)):
            rank_problems.append(sector)
        ordered = sorted(members, key=lambda m: m.sector_rank)
        caps = [m.market_cap for m in ordered]
        if caps != sorted(caps, reverse=True):
            cap_order_problems.append(sector)
    add("Sector ranks contiguous 1..N", 0, len(rank_problems))
    add("Sector rank order matches market cap desc", 0, len(cap_order_problems))

    overall = [row.overall_rank for row in rows]
    add("Overall ranks distinct", len(rows), len(set(overall)))
    add("Overall ranks complete 1..250", 0,
        len(set(range(1, EXPECTED_WORKBOOK_ROWS + 1)) - set(overall)))

    bad_format = sorted(t for t in tickers if not re.fullmatch(r"[A-Z0-9]+", t))
    add("Tickers outside [A-Z0-9]", 0, len(bad_format))

    add("Sector tab count", EXPECTED_SECTOR_COUNT, len(sector_tabs))
    all_keys = {(row.ticker, row.sector, row.sector_rank) for row in rows}
    tab_mismatches = []
    for name, tab in sorted(sector_tabs.items()):
        for rank, ticker, sector in tab:
            if (ticker, sector, rank) not in all_keys:
                tab_mismatches.append(f"{name}:{ticker}")
    add("Sector tab rows reconciling to All_Stocks", 0, len(tab_mismatches))
    add("Sector tab row total", EXPECTED_WORKBOOK_ROWS,
        sum(len(tab) for tab in sector_tabs.values()))

    details = {
        "duplicate_tickers": duplicate_tickers,
        "duplicate_isins": duplicate_isins,
        "sector_count_mismatches": mismatched,
        "unexpected_sectors": unexpected,
        "sector_rank_problems": rank_problems,
        "cap_order_problems": cap_order_problems,
        "bad_ticker_format": bad_format,
        "tab_mismatches": tab_mismatches,
        "sector_counts": dict(sorted(sector_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "price_date_distribution": dict(
            sorted(Counter(row.price_date for row in rows).items())
        ),
        "status_distribution": dict(
            sorted(Counter(row.active_status for row in rows).items(),
                   key=lambda kv: (-kv[1], kv[0]))
        ),
    }
    return checks, details


# --- rendering ---------------------------------------------------------------

def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return out


def _yn(value):
    return "yes" if value else "no"


def _dossier(ticker, data, rubix, note=""):
    """One evidence block per named ticker. Facts only; no conclusion."""

    workbook_row = data["workbook_by_ticker"].get(ticker)
    record = data["canonical_by_ticker"].get(ticker)
    lines = [f"**{ticker}**" + (f" — {note}" if note else "")]
    lines.append("")
    lines.append(f"- workbook present: {_yn(workbook_row)}")
    if workbook_row:
        lines.append(f"    - company: {workbook_row.company}")
        lines.append(f"    - ISIN: {workbook_row.isin or '(blank)'}")
        lines.append(f"    - sector: {workbook_row.sector} (rank {workbook_row.sector_rank})")
        lines.append(f"    - market cap: {workbook_row.market_cap_bn} bn EGP")
        lines.append(f"    - price date: {workbook_row.price_date}")
        lines.append(f"    - listing status: {workbook_row.active_status}")
    lines.append(f"- canonical present: {_yn(record)}")
    if record:
        lines.append(f"    - state: {_canonical_status(record)}")
        lines.append(f"    - company: {record.company_name}")
        lines.append(f"    - ISIN: {record.isin or '(blank)'}")
        lines.append(f"    - rubix_symbol: {record.rubix_symbol or '(none)'}")
        lines.append(f"    - rubix_mapping_status: {record.rubix_mapping_status}")
    observed = rubix.observed(ticker)
    lines.append(f"- rubix observed (any time): {_yn(observed)}")
    if observed:
        lines.append(f"    - in quotes: {_yn(ticker in rubix.quote_tickers)}")
        lines.append(f"    - in candles_1m: {_yn(ticker in rubix.candle_tickers)}")
        lines.append(f"    - last market_timestamp: {rubix.per_ticker_last.get(ticker, '(n/a)')}")
        lines.append(f"    - observed on watermark date "
                     f"({rubix.watermark_date}): "
                     f"{_yn(rubix.observed_currently(ticker))}")
    if record is not None:
        lines.append(
            f"- verified Rubix mapping: {_yn(record.has_verified_rubix_mapping)}"
        )
        lines.append(
            f"- in the current subscription set (active AND verified): "
            f"{_yn(_is_subscribed(record))}"
        )
        if not _is_subscribed(record):
            reason = ("archived, so no subscription key is emitted even though a "
                      "verified mapping exists"
                      if record.has_verified_rubix_mapping
                      else "no verified mapping, so no subscription key is emitted")
            lines.append(f"    - {reason}")
            lines.append("    - therefore absence from today's feed is EXPECTED "
                         "and carries no evidential weight")
    lines.append(f"- resolution: {UNRESOLVED}")
    lines.append("")
    return lines


def render(data, checks, details, rubix, provenance, exceptions, mapping):
    L = []
    A = L.append

    conflicts = data["conflicts"]
    unresolved_total = (
        len(conflicts)
        + len(data["rubix_observed_absent_from_workbook"])
        + len(INVESTIGATE_STATUS_DISAGREEMENT)
        + len(data["canonical_active_workbook_stale_price"])
    )

    A("# PHASE 1 — EGX UNIVERSE RECONCILIATION")
    A("")
    A("Read-only reconciliation of the EGX sector workbook against the canonical")
    A("universe, corroborated by Rubix live-feed observation evidence.")
    A("")
    A("Generated by `scripts/import_egx_sector_universe.py`, which has no write path.")
    A("")

    # ---- 1 ----
    A("## 1. Executive Summary")
    A("")
    A("\n".join(_table(
        ["Metric", "Value"],
        [
            ["Workbook rows (`All_Stocks`)", len(data["workbook_by_ticker"])],
            ["Canonical rows (total)", len(data["canonical_by_ticker"])],
            ["Canonical rows active", len(data["active_by_ticker"])],
            ["Canonical rows archived",
             len(data["canonical_by_ticker"]) - len(data["active_by_ticker"])],
            ["Ticker overlap (workbook ∩ canonical-active)", len(data["overlap"])],
            ["Workbook-only (absent from canonical entirely)",
             len(data["workbook_absent_from_canonical"])],
            ["Workbook tickers present but ARCHIVED in canonical",
             len(data["workbook_archived_in_canonical"])],
            ["Canonical-active-only (absent from workbook)",
             len(data["canonical_active_absent_from_workbook"])],
            ["Identity conflicts discovered", len(conflicts)],
            ["  of which ISIN match / ticker differs",
             sum(1 for c in conflicts if c.kind == "ISIN_MATCH_TICKER_DIFFERS")],
            ["  of which name match / canonical ISIN blank",
             sum(1 for c in conflicts if c.kind.startswith("NAME_MATCH"))],
            ["Duplicate-ACTIVE identity groups (by ISIN)",
             len(data["duplicate_active_by_isin"])],
            ["Duplicate-ACTIVE identity groups (by company name)",
             len(data["duplicate_active_by_name"])],
            ["Canonical ISINs mapping to >1 ticker",
             len(data["canonical_isin_multi_ticker"])],
            ["Canonical rows with blank ISIN", len(data["canonical_missing_isin"])],
            ["Rubix distinct observed tickers", data["rubix_observed_count"]],
            ["Rubix-observed absent from workbook",
             len(data["rubix_observed_absent_from_workbook"])],
            ["Rubix-observed absent from canonical",
             len(data["rubix_observed_absent_from_canonical"])],
            ["Workbook tickers never observed on Rubix",
             len(data["workbook_not_observed"])],
            ["Canonical-active never observed on Rubix",
             len(data["canonical_active_not_observed"])],
            ["Canonical-active with stale workbook price date",
             len(data["canonical_active_workbook_stale_price"])],
            ["Live subscription set size", data["subscription_set_size"]],
            ["**Workbook securities visible to the engine**",
             f"**{len(data['workbook_subscribed'])} of "
             f"{len(data['workbook_by_ticker'])}**"],
            ["Workbook securities NOT in the subscription set",
             len(data["workbook_not_subscribed"])],
            ["Conflicted companies with no coverage under either ticker",
             len(data["conflicts_without_coverage"])],
            ["**Unresolved cases requiring a decision**", f"**{unresolved_total}**"],
        ],
    )))
    A("")
    A("### NO DATA WAS MODIFIED.")
    A("")
    A("No universe CSV write, no SQLite write, no schema change, no VACUUM, no WAL")
    A("checkpoint, no Rubix write, no `universe_changes.csv`, no conflict resolved")
    A("automatically. Every database handle was opened `mode=ro` with")
    A("`PRAGMA query_only=1`. Every discrepancy below remains as found.")
    A("")
    A("### Input provenance")
    A("")
    A("\n".join(_table(
        ["Input", "Identity"],
        [[key, value] for key, value in provenance],
    )))
    A("")

    # ---- 2 ----
    A("## 2. Source Integrity")
    A("")
    failures = [c for c in checks if c[3] == "FAIL"]
    A(f"**{len(checks) - len(failures)}/{len(checks)} checks PASS"
      + (f", {len(failures)} FAIL**" if failures else ", 0 FAIL**"))
    A("")
    A("\n".join(_table(["Check", "Expected", "Observed", "Verdict"], checks)))
    A("")
    if failures:
        A("Validation failed. Nothing was modified and no downstream conclusion in")
        A("this report should be relied on until these are explained:")
        A("")
        for name, expected, observed, _ in failures:
            A(f"- `{name}`: expected {expected}, observed {observed}")
        A("")
        for key in ("duplicate_tickers", "duplicate_isins", "sector_count_mismatches",
                    "unexpected_sectors", "sector_rank_problems", "cap_order_problems",
                    "bad_ticker_format", "tab_mismatches"):
            if details[key]:
                A(f"- `{key}`: {', '.join(map(str, details[key]))}")
        A("")

    A("### Sector constituent counts")
    A("")
    A("\n".join(_table(
        ["Sector", "Workbook", "Expected", "Verdict"],
        [[sector, count, EXPECTED_SECTOR_COUNTS.get(sector, "—"),
          "PASS" if EXPECTED_SECTOR_COUNTS.get(sector) == count else "FAIL"]
         for sector, count in details["sector_counts"].items()],
    )))
    A("")
    A("### Sector taxonomy mapping (EGX official → project taxonomy)")
    A("")
    A("\n".join(_table(
        ["EGX official sector", "Project taxonomy", "Worksheet tab", "Stocks"],
        [list(row) for row in mapping],
    )))
    A("")
    A("### Listing-status distribution")
    A("")
    A("The workbook's `Active Status` is free text with "
      f"{len(details['status_distribution'])} distinct values. It records *register")
    A("membership and last-trade recency*, not tradeability.")
    A("")
    A("\n".join(_table(
        ["Active Status", "Rows"],
        [[status, count] for status, count in details["status_distribution"].items()],
    )))
    A("")
    A("### Price-date distribution")
    A("")
    fresh = details["price_date_distribution"].get("2026-08-10", 0)
    A(f"- rows dated `2026-08-10` (last completed session): **{fresh}**")
    A(f"- rows dated earlier: **{len(data['workbook_by_ticker']) - fresh}**")
    A("")
    A("\n".join(_table(
        ["Price Date", "Rows"],
        [[date or "(blank)", count]
         for date, count in details["price_date_distribution"].items()],
    )))
    A("")
    A("### Workbook `Exceptions` sheet")
    A("")
    exception_kinds = Counter(kind for _, _, kind, _ in exceptions)
    A("\n".join(_table(
        ["Issue Type", "Rows"],
        [[kind, count] for kind, count in sorted(exception_kinds.items())],
    )))
    A("")
    A("Non-equity exclusions itemised by the workbook:")
    A("")
    for ticker, company, kind, _ in exceptions:
        if "Excluded" in kind:
            A(f"- `{ticker}` — {company}")
    A("")

    # ---- 3 ----
    A("## 3. Universe Reconciliation")
    A("")
    A("\n".join(_table(
        ["Relationship", "Count"],
        [
            ["Workbook ∩ canonical-active", len(data["overlap"])],
            ["Workbook, absent from canonical entirely",
             len(data["workbook_absent_from_canonical"])],
            ["Workbook, present but ARCHIVED in canonical",
             len(data["workbook_archived_in_canonical"])],
            ["Canonical-active, absent from workbook",
             len(data["canonical_active_absent_from_workbook"])],
        ],
    )))
    A("")

    A("### 3.1 Workbook tickers absent from the canonical universe entirely")
    A("")
    A(f"Count: **{len(data['workbook_absent_from_canonical'])}**")
    A("")
    A("\n".join(_table(
        ["Ticker", "Company", "Sector", "Price Date", "Listing Status", "Rubix Observed"],
        [[t,
          data["workbook_by_ticker"][t].company,
          data["workbook_by_ticker"][t].sector,
          data["workbook_by_ticker"][t].price_date,
          data["workbook_by_ticker"][t].active_status,
          _yn(rubix.observed(t))]
         for t in data["workbook_absent_from_canonical"]],
    )))
    A("")

    A("### 3.2 Workbook tickers present but ARCHIVED in the canonical universe")
    A("")
    A(f"Count: **{len(data['workbook_archived_in_canonical'])}**")
    A("")
    A("\n".join(_table(
        ["Ticker", "Company", "Sector", "Price Date", "Canonical ISIN", "Rubix Observed"],
        [[t,
          data["workbook_by_ticker"][t].company,
          data["workbook_by_ticker"][t].sector,
          data["workbook_by_ticker"][t].price_date,
          data["canonical_by_ticker"][t].isin or "(blank)",
          _yn(rubix.observed(t))]
         for t in data["workbook_archived_in_canonical"]],
    )))
    A("")

    A("### 3.3 Canonical-active tickers absent from the workbook")
    A("")
    A(f"Count: **{len(data['canonical_active_absent_from_workbook'])}**")
    A("")
    A("\n".join(_table(
        ["Ticker", "Canonical Company", "Canonical ISIN", "Rubix Mapping",
         "Rubix Observed"],
        [[t,
          data["active_by_ticker"][t].company_name,
          data["active_by_ticker"][t].isin or "(blank)",
          data["active_by_ticker"][t].rubix_mapping_status,
          _yn(rubix.observed(t))]
         for t in data["canonical_active_absent_from_workbook"]],
    )))
    A("")

    A("### 3.4 Canonical rows with a blank ISIN")
    A("")
    A(f"Total: **{len(data['canonical_missing_isin'])}** "
      f"(of which active: **{len(data['canonical_active_missing_isin'])}**)")
    A("")
    A("A blank ISIN removes the only reliable cross-source join key for that row.")
    A("")
    A("- all: " + ", ".join(f"`{t}`" for t in data["canonical_missing_isin"]))
    A("- active: " + ", ".join(f"`{t}`" for t in data["canonical_active_missing_isin"]))
    A("")

    A("### 3.5 Canonical ISINs mapping to more than one ticker")
    A("")
    A(f"Count: **{len(data['canonical_isin_multi_ticker'])}**")
    A("")
    A("\n".join(_table(
        ["ISIN", "Canonical Tickers", "Active?", "Workbook Ticker For This ISIN"],
        [[isin,
          ", ".join(f"`{t}`" for t in tickers),
          ", ".join(
              f"{t}={_canonical_status(data['canonical_by_ticker'][t])}"
              for t in tickers),
          next((row.ticker for row in data["workbook_by_ticker"].values()
                if row.isin == isin), "(not in workbook)")]
         for isin, tickers in data["canonical_isin_multi_ticker"].items()],
    )))
    A("")

    A("### 3.6 Duplicate-ACTIVE identity groups")
    A("")
    A("Two rows both flagged `is_active` that denote the same company. Any consumer")
    A("iterating `active_universe()` counts such a company twice.")
    A("")
    A(f"By shared ISIN — **{len(data['duplicate_active_by_isin'])}** groups:")
    A("")
    if data["duplicate_active_by_isin"]:
        A("\n".join(_table(
            ["ISIN", "Active Tickers", "Canonical Company"],
            [[isin, ", ".join(f"`{t}`" for t in tickers),
              data["canonical_by_ticker"][tickers[0]].company_name]
             for isin, tickers in data["duplicate_active_by_isin"].items()],
        )))
    else:
        A("_none_")
    A("")
    A(f"By identical normalized company name — "
      f"**{len(data['duplicate_active_by_name'])}** groups:")
    A("")
    if data["duplicate_active_by_name"]:
        A("\n".join(_table(
            ["Normalized Company Name", "Active Tickers", "ISINs"],
            [[name, ", ".join(f"`{t}`" for t in tickers),
              ", ".join(data["canonical_by_ticker"][t].isin or "(blank)"
                        for t in tickers)]
             for name, tickers in data["duplicate_active_by_name"].items()],
        )))
    else:
        A("_none_")
    A("")

    A("### 3.7 Workbook coverage by the live subscription set")
    A("")
    A("`core.universe.rubix_subscription_symbols()` emits a key only for a row that")
    A("is ACTIVE **and** carries a verified Rubix mapping. That set — not the")
    A("workbook, and not `is_active` alone — determines what the engine can see.")
    A("")
    A("\n".join(_table(
        ["Measure", "Count"],
        [["Subscription set size", data["subscription_set_size"]],
         ["Workbook tickers inside the subscription set",
          len(data["workbook_subscribed"])],
         ["Workbook tickers OUTSIDE the subscription set",
          len(data["workbook_not_subscribed"])],
         ["Subscribed tickers absent from the workbook",
          len(data["subscribed_absent_from_workbook"])]],
    )))
    A("")
    A(f"So of the workbook's {len(data['workbook_by_ticker'])} securities, "
      f"**{len(data['workbook_subscribed'])}** are currently visible to the engine")
    A(f"and **{len(data['workbook_not_subscribed'])}** are not. Sector enrichment can")
    A("only ever apply to the visible ones.")
    A("")
    A("Workbook tickers outside the subscription set:")
    A("")
    A("\n".join(_table(
        ["Ticker", "Company", "Sector", "Canonical State", "Rubix Mapping",
         "Price Date"],
        [[f"`{t}`",
          data["workbook_by_ticker"][t].company[:36],
          data["workbook_by_ticker"][t].sector,
          _canonical_status(data["canonical_by_ticker"].get(t)),
          (data["canonical_by_ticker"][t].rubix_mapping_status
           if t in data["canonical_by_ticker"] else "(absent from canonical)"),
          data["workbook_by_ticker"][t].price_date]
         for t in data["workbook_not_subscribed"]],
    )))
    A("")

    # ---- 4 ----
    A("## 4. Identity Conflicts")
    A("")
    A(f"**{len(conflicts)} conflicts discovered.** Discovered by rule, not by")
    A("consulting a hand-written list — see §4.3 for coverage of the named cases.")
    A("")
    A("`Assessment` reports *observation*, never a corporate-action conclusion. A")
    A("ticker carrying feed traffic while its counterpart carries none is evidence;")
    A("calling that a rename is a judgement Phase 1 does not make.")
    A("")
    A("**Read the `Canon Subscribed` column before trusting any row.** A canonical")
    A("ticker with no verified Rubix mapping contributes no subscription key, so the")
    A("collector never asks for it and it cannot possibly appear on the feed. For")
    A("those rows, \"not observed\" restates the universe's own configuration and is")
    A("**not** independent evidence. Only a subscribed-yet-silent counterpart")
    A("carries information in the negative direction — see §5.5.")
    A("")
    A("\n".join(_table(
        ["ISIN", "Workbook Ticker", "Canonical Ticker", "Company",
         "Workbook Status", "Canonical Status", "Observed (wb/canon)",
         "WB Observed Today", "Canon Subscribed", "Assessment", "Confidence",
         "Evidence Independence", "Resolution"],
        [[c.isin or "(blank)", f"`{c.workbook_ticker}`", f"`{c.canonical_ticker}`",
          c.company[:34], c.workbook_status[:32], c.canonical_status,
          f"{_yn(c.workbook_observed)} / {_yn(c.canonical_observed)}",
          _yn(c.workbook_observed_currently),
          _yn(c.canonical_subscribed),
          c.assessment, c.confidence,
          c.independence.split(" — ")[0], UNRESOLVED]
         for c in conflicts],
    )))
    A("")
    A("Confidence distribution:")
    A("")
    A("\n".join(_table(
        ["Confidence", "Conflicts", "Meaning"],
        [[level,
          sum(1 for c in conflicts if c.confidence == level),
          {"HIGH": "feed evidence is independent of the universe configuration",
           "MEDIUM": "evidence is suggestive but partly circular or name-only",
           "LOW": "no usable feed evidence in either direction"}[level]]
         for level in ("HIGH", "MEDIUM", "LOW")],
    )))
    A("")
    live_workbook_side = [c for c in conflicts if c.workbook_observed_currently]
    A("#### What this establishes, and what it does not")
    A("")
    A(f"**Established.** In **{len(live_workbook_side)}** of {len(conflicts)} conflicts")
    A(f"the workbook's ticker carried feed traffic on `{rubix.watermark_date}`. That is")
    A("positive, independent evidence that the exchange feed uses the workbook's")
    A("identifier for those companies. The workbook's ticker identity is corroborated")
    A("by Rubix wherever the feed speaks at all.")
    A("")
    A("**Not established.** That the canonical counterpart is therefore retired.")
    A(f"**{sum(1 for c in conflicts if not c.canonical_subscribed)}** of the canonical")
    A("tickers are unsubscribed, so their silence was guaranteed in advance and")
    A("proves nothing (§5.5). A retired ticker and a merely-unsubscribed ticker are")
    A("indistinguishable in this dataset.")
    A("")
    A("Every row therefore stays " + UNRESOLVED + ", including the ones whose")
    A("direction looks obvious.")
    A("")
    A("### 4.1 Conflicts where NEITHER identifier is subscribed")
    A("")
    uncovered = data["conflicts_without_coverage"]
    A(f"**{len(uncovered)}** of {len(conflicts)} conflicted companies have no live")
    A("coverage under either ticker: the workbook's identifier is not in the")
    A("subscription set, and neither is the canonical one. The workbook says these")
    A("securities trade; the engine cannot currently see them under any name.")
    A("")
    if uncovered:
        A("\n".join(_table(
            ["Company", "Workbook Ticker", "Canonical Ticker", "Sector",
             "Workbook Price Date"],
            [[c.company[:40], f"`{c.workbook_ticker}`", f"`{c.canonical_ticker}`",
              (data["workbook_by_ticker"][c.workbook_ticker].sector
               if c.workbook_ticker in data["workbook_by_ticker"] else "—"),
              (data["workbook_by_ticker"][c.workbook_ticker].price_date
               if c.workbook_ticker in data["workbook_by_ticker"] else "—")]
             for c in uncovered],
        )))
    else:
        A("_none_")
    A("")
    A("This is a coverage observation, not a defect claim: an unsubscribed symbol")
    A("may be unsubscribed for a good reason recorded elsewhere. It is listed")
    A("because it is the practical consequence of the conflicts above.")
    A("")
    A("### 4.2 Conflict kinds")
    A("")
    A("\n".join(_table(
        ["Kind", "Count", "Evidence strength"],
        [["ISIN_MATCH_TICKER_DIFFERS",
          sum(1 for c in conflicts if c.kind == "ISIN_MATCH_TICKER_DIFFERS"),
          "strong — shared ISIN is a hard identity link"],
         ["NAME_MATCH_TICKER_DIFFERS_CANONICAL_ISIN_BLANK",
          sum(1 for c in conflicts if c.kind.startswith("NAME_MATCH")),
          "weak — name equality cannot separate a rename from two similar names"]],
    )))
    A("")
    A("### 4.3 Coverage of the named conflict pairs")
    A("")
    A("The brief supplies 12 pairs and warns the list may not be exhaustive.")
    A("Rule-based discovery is reconciled against it here.")
    A("")
    found = {(c.workbook_ticker, c.canonical_ticker) for c in conflicts}
    rows = []
    not_reproduced = []
    for workbook_ticker, canonical_ticker in KNOWN_CONFLICT_PAIRS:
        hit = (workbook_ticker, canonical_ticker) in found
        if not hit:
            not_reproduced.append((workbook_ticker, canonical_ticker))
        rows.append([f"`{workbook_ticker}` / `{canonical_ticker}`",
                     "discovered" if hit else "NOT REPRODUCED BY RULE"])
    A("\n".join(_table(["Named pair", "Discovery result"], rows)))
    A("")
    if not_reproduced:
        A("#### Why a named pair was not reproduced")
        A("")
        A("A pair the rule does not emit is itself a finding: it means the pair is")
        A("not linked by shared ISIN or shared name in these two sources. The")
        A("diagnosis per pair:")
        A("")
        diag = []
        for workbook_ticker, canonical_ticker in not_reproduced:
            workbook_row = data["workbook_by_ticker"].get(workbook_ticker)
            other_row = data["workbook_by_ticker"].get(canonical_ticker)
            record = data["canonical_by_ticker"].get(canonical_ticker)
            peer = data["canonical_by_ticker"].get(workbook_ticker)
            reasons = []
            if workbook_row is None:
                reasons.append(f"`{workbook_ticker}` is not a workbook ticker")
            if other_row is not None:
                reasons.append(
                    f"`{canonical_ticker}` IS itself a workbook ticker")
            if record is not None and workbook_row is not None:
                if record.isin and record.isin != workbook_row.isin:
                    reasons.append("ISINs differ, so no ISIN link exists")
            if record is not None and not any(
                row.isin == record.isin
                for row in data["workbook_by_ticker"].values()
            ):
                reasons.append(
                    f"canonical ISIN `{record.isin or '(blank)'}` appears in no "
                    "workbook row")
            diag.append([
                f"`{workbook_ticker}` / `{canonical_ticker}`",
                f"{_canonical_status(peer)} / {_canonical_status(record)}",
                (workbook_row.isin if workbook_row else "(not in workbook)"),
                (record.isin or "(blank)") if record else "(absent)",
                "; ".join(reasons) or "no linking evidence in either source",
            ])
        A("\n".join(_table(
            ["Named pair", "Canonical state (wb/canon)", "Workbook ISIN",
             "Canonical ISIN", "Why the rule did not link them"],
            diag,
        )))
        A("")
        A(f"These **{len(not_reproduced)}** remain {UNRESOLVED}: the brief asserts a")
        A("relationship that this evidence neither confirms nor refutes.")
        A("")
    extra = sorted(found - set(KNOWN_CONFLICT_PAIRS))
    A(f"Conflicts discovered BEYOND the named list: **{len(extra)}**")
    A("")
    if extra:
        for workbook_ticker, canonical_ticker in extra:
            A(f"- `{workbook_ticker}` / `{canonical_ticker}`")
        A("")

    # ---- 5 ----
    A("## 5. Rubix Evidence")
    A("")
    A("Rubix was read through `mode=ro` + `PRAGMA query_only=1` only. Distinct")
    A("tickers were obtained by a loose index scan over `idx_quotes_ticker_time`")
    A("rather than a table scan, so no multi-gigabyte read was performed and no")
    A("WAL work was triggered.")
    A("")
    A("\n".join(_table(
        ["Property", "Value"],
        [["Distinct tickers in `quotes`", len(rubix.quote_tickers)],
         ["Distinct tickers in `candles_1m`", len(rubix.candle_tickers)],
         ["Union (observed)", data["rubix_observed_count"]],
         ["Feed watermark (max market_timestamp)", rubix.watermark],
         ["Distinct per-ticker LAST-observation dates",
          len(rubix.last_observation_dates)],
         ["Last-observation date range",
          f"{rubix.last_observation_dates[0]} → {rubix.last_observation_dates[-1]}"
          if rubix.last_observation_dates else "(none)"],
         ["Tickers last observed ON the watermark date",
          sum(1 for t in rubix.quote_tickers if rubix.observed_currently(t))],
         ["Tickers last observed BEFORE the watermark date",
          sum(1 for t in rubix.quote_tickers
              if not rubix.observed_currently(t))]],
    )))
    A("")
    A("The date figures above are per-ticker *last*-observation dates, not a")
    A("measure of session coverage. Establishing how many sessions the database")
    A("spans would require scanning a multi-gigabyte table, which this tool")
    A("deliberately does not do.")
    A("")
    A("\n".join(_table(
        ["Relationship", "Count"],
        [["Observed ∩ workbook", len(data["rubix_observed_in_workbook"])],
         ["Observed, absent from workbook",
          len(data["rubix_observed_absent_from_workbook"])],
         ["Observed, absent from canonical",
          len(data["rubix_observed_absent_from_canonical"])],
         ["Observed, ARCHIVED in canonical",
          len(data["rubix_observed_canonical_archived"])],
         ["Workbook tickers never observed", len(data["workbook_not_observed"])],
         ["Canonical-active never observed",
          len(data["canonical_active_not_observed"])]],
    )))
    A("")
    A("### 5.1 Rubix-observed tickers absent from the workbook")
    A("")
    current = [t for t in data["rubix_observed_absent_from_workbook"]
               if rubix.observed_currently(t)]
    historical = [t for t in data["rubix_observed_absent_from_workbook"]
                  if not rubix.observed_currently(t)]
    A(f"Total **{len(data['rubix_observed_absent_from_workbook'])}**, which splits")
    A("into two very different groups. Only the first is actionable.")
    A("")
    A(f"**Currently observed (last seen on `{rubix.watermark_date}`) — "
      f"{len(current)} tickers.** These carry live traffic today and would enrich")
    A("to no sector.")
    A("")
    A("\n".join(_table(
        ["Ticker", "Canonical State", "Canonical Company", "Rubix Mapping",
         "Last market_timestamp"],
        [[f"`{t}`",
          _canonical_status(data["canonical_by_ticker"].get(t)),
          (data["canonical_by_ticker"][t].company_name
           if t in data["canonical_by_ticker"] else "(absent)"),
          (data["canonical_by_ticker"][t].rubix_mapping_status
           if t in data["canonical_by_ticker"] else "(absent)"),
          rubix.per_ticker_last.get(t, "(candles only)")]
         for t in current],
    )))
    A("")
    A(f"**Historical only (last seen before the watermark) — {len(historical)} "
      "tickers.** Retained feed history from a previous subscription set; not")
    A("evidence about the current universe.")
    A("")
    A("\n".join(_table(
        ["Ticker", "Canonical State", "Canonical Company", "Last market_timestamp"],
        [[f"`{t}`",
          _canonical_status(data["canonical_by_ticker"].get(t)),
          (data["canonical_by_ticker"][t].company_name or "(blank)"
           if t in data["canonical_by_ticker"] else "(absent)"),
          rubix.per_ticker_last.get(t, "(candles only)")]
         for t in historical],
    )))
    A("")
    A("### 5.2 Rubix-observed tickers ARCHIVED in the canonical universe")
    A("")
    A("These carry live feed traffic while the application treats them as")
    A("ineligible for a new entry.")
    A("")
    A("\n".join(_table(
        ["Ticker", "Canonical Company", "In Workbook?", "Workbook Status",
         "Last market_timestamp"],
        [[f"`{t}`",
          data["canonical_by_ticker"][t].company_name,
          _yn(t in data["workbook_by_ticker"]),
          (data["workbook_by_ticker"][t].active_status
           if t in data["workbook_by_ticker"] else "(not in workbook)"),
          rubix.per_ticker_last.get(t, "(candles only)")]
         for t in data["rubix_observed_canonical_archived"]],
    )))
    A("")
    A("### 5.3 Feed evidence for the discovered conflicts")
    A("")
    A("\n".join(_table(
        ["Workbook Ticker", "Observed", "Canonical Ticker", "Observed", "Direction"],
        [[f"`{c.workbook_ticker}`", _yn(c.workbook_observed),
          f"`{c.canonical_ticker}`", _yn(c.canonical_observed), c.assessment]
         for c in conflicts],
    )))
    A("")
    A("### 5.4 Canonical-active tickers never observed on Rubix")
    A("")
    A(f"Count: **{len(data['canonical_active_not_observed'])}**. Absence of feed")
    A("traffic is not by itself a defect — an illiquid listed security can go")
    A("unquoted for a whole window.")
    A("")
    A("\n".join(_table(
        ["Ticker", "Canonical Company", "Rubix Mapping", "In Workbook?",
         "Workbook Price Date"],
        [[f"`{t}`",
          data["active_by_ticker"][t].company_name[:40],
          data["active_by_ticker"][t].rubix_mapping_status,
          _yn(t in data["workbook_by_ticker"]),
          (data["workbook_by_ticker"][t].price_date
           if t in data["workbook_by_ticker"] else "—")]
         for t in data["canonical_active_not_observed"]],
    )))
    A("")

    A("### 5.5 What Rubix absence does and does not prove")
    A("")
    A("This is the most important interpretive limit in the report.")
    A("")
    A("The collector requests only symbols carrying a verified Rubix mapping. A")
    A("canonical row without one is never requested, so it can never be observed.")
    A("Treating its silence as proof that its ticker is retired would be circular:")
    A("the universe decided not to ask, and the answer is then quoted back as")
    A("evidence about the universe.")
    A("")
    A("What each direction supports:")
    A("")
    A("\n".join(_table(
        ["Observation", "Supports", "Independent?"],
        [["Workbook ticker observed on the feed",
          "the exchange feed uses this identifier", "YES"],
         ["Canonical ticker silent AND subscribed",
          "the identifier was requested and produced nothing", "YES"],
         ["Canonical ticker silent AND unsubscribed",
          "nothing — it was never requested", "NO"],
         ["Workbook ticker never observed",
          "either it is unsubscribed, or it is genuinely inactive", "PARTIAL"],
         ["Any ticker observed",
          "the symbol is PUBLISHED on the feed — not that it traded", "YES"]],
    )))
    A("")
    A("The last row is easy to misread. A `quotes` row can be an unchanged or")
    A("indicative snapshot, so observation proves publication, not execution — see")
    A("§6.1, where securities whose last EGX trade was in 2014 still carry a quote")
    A("from the current watermark.")
    A("")
    A("A second confound: the database retains pre-migration history. "
      f"**{sum(1 for t in rubix.quote_tickers if not rubix.observed_currently(t))}** "
      "tickers were last")
    A(f"seen before the `{rubix.watermark_date}` watermark, many on `2026-07-30` — the")
    A("canonical universe's own as-of date. Those are observations of a *previous*")
    A("subscription set. The `WB Observed Today` column in §4 separates current")
    A("traffic from historical traffic for exactly this reason.")
    A("")
    A("Consequence for §7.2: `AIFI`, `AIHC`, `ELWA` and `MMAT` are archived and")
    A("therefore unsubscribed. Their last observations predate the watermark. Rubix")
    A("can neither confirm nor refute the workbook's claim that they traded on")
    A("2026-08-10, and no amount of further reading of this database will change")
    A("that. Resolving them requires a source outside these three.")
    A("")

    # ---- 6 ----
    A("## 6. Stale / Never-Traded")
    A("")
    A("Evidence only. The workbook's own methodology states that low liquidity was")
    A("NOT an exclusion criterion, so a stale price date says nothing about whether")
    A("a security should be in any universe. Nothing here is a tradeability")
    A("judgement, and no row was deactivated or labelled unsuitable.")
    A("")
    A("### 6.1 Canonical-ACTIVE rows whose workbook price date is not the last session")
    A("")
    stale = data["canonical_active_workbook_stale_price"]
    A(f"Count: **{len(stale)}**")
    A("")
    A("\n".join(_table(
        ["Ticker", "Company", "Sector", "Price Date", "Listing Status",
         "Canonical State", "Subscribed", "Observed Today", "Last market_timestamp"],
        [[f"`{t}`",
          data["workbook_by_ticker"][t].company[:34],
          data["workbook_by_ticker"][t].sector,
          data["workbook_by_ticker"][t].price_date,
          data["workbook_by_ticker"][t].active_status[:44],
          _canonical_status(data["canonical_by_ticker"][t]),
          _yn(_is_subscribed(data["canonical_by_ticker"][t])),
          _yn(rubix.observed_currently(t)),
          rubix.per_ticker_last.get(t, "—")]
         for t in stale],
    )))
    A("")
    A("`Subscribed` matters here: an ACTIVE row is not automatically inside the")
    A("operational universe, so these rows are not uniformly \"live\".")
    A("")
    A("**Do not read `Observed Today` as \"it traded today\".** Most of these rows")
    A("carry a recent Rubix timestamp while the workbook dates their last trade")
    A("months or years earlier — `SIMO` last traded 2014-03-03 per EGX, yet has a")
    A("quote row from the current watermark. A `quotes` row is a feed observation,")
    A("which can be an unchanged or indicative snapshot for a listed-but-untraded")
    A("security. Presence on the feed establishes that the symbol is being")
    A("published; it does not establish that a trade occurred, and this report makes")
    A("no claim that one did. Reconciling quote-presence against actual execution")
    A("would need trade-level evidence that is out of Phase 1 scope.")
    A("")
    A("### 6.2 The eight names named in the brief")
    A("")
    named = set(INVESTIGATE_STALE_ACTIVE)
    A("Reconciling the brief's list against rule-based discovery:")
    A("")
    A(f"- named in brief: {len(named)} — "
      + ", ".join(f"`{t}`" for t in sorted(named)))
    A(f"- discovered by rule: {len(stale)} — "
      + ", ".join(f"`{t}`" for t in stale))
    A(f"- named but NOT reproduced: "
      + (", ".join(f"`{t}`" for t in sorted(named - set(stale))) or "none"))
    A(f"- discovered BEYOND the named list: "
      + (", ".join(f"`{t}`" for t in sorted(set(stale) - named)) or "none"))
    A("")
    for ticker in sorted(named):
        A("\n".join(_dossier(ticker, data, rubix,
                             "named in brief §H — evidence only, no action")))
    A("")

    # ---- 7 ----
    A("## 7. Unresolved Decisions")
    A("")
    A("Everything in this section requires a human decision. None of it can be")
    A("settled from the data alone, and Phase 1 settles none of it.")
    A("")

    A("### 7.1 Rubix-observed, workbook-missing — `FTNS`, `HBCO`, `UTOP`")
    A("")
    A("Carrying live feed traffic and active in the canonical universe, yet absent")
    A("from the workbook's 250. They would enrich to **no sector**.")
    A("")
    for ticker in INVESTIGATE_RUBIX_OBSERVED_WORKBOOK_MISSING:
        A("\n".join(_dossier(ticker, data, rubix)))
        record = data["canonical_by_ticker"].get(ticker)
        if record is not None:
            name = _norm_name(record.company_name)
            isin_hit = [row.ticker for row in data["workbook_by_ticker"].values()
                        if record.isin and row.isin == record.isin]
            name_hit = [row.ticker for row in data["workbook_by_ticker"].values()
                        if _norm_name(row.company) == name and name]
            partial = sorted({
                row.ticker for row in data["workbook_by_ticker"].values()
                if name and name.split() and name.split()[0] in _norm_name(row.company)
            })
            A(f"- workbook ISIN match: {', '.join(isin_hit) or 'NONE'}")
            A(f"- workbook exact-name match: {', '.join(name_hit) or 'NONE'}")
            A(f"- workbook partial-name candidates: "
              f"{', '.join(partial) or 'NONE'}")
            A(f"- evidence sufficient to identify a workbook counterpart: "
              f"{'yes' if (isin_hit or name_hit) else 'NO'}")
            A("")
    A("**Decision required:** for each, is it a genuine workbook omission (add to")
    A("the sector source), a non-equity instrument the workbook correctly excluded,")
    A("or a canonical-universe row that should not be active? Not addable from this")
    A("evidence — none has a workbook counterpart.")
    A("")

    A("### 7.2 Status disagreement — `AIFI`, `AIHC`, `ELWA`")
    A("")
    A("The workbook dates these to the last completed session; the canonical")
    A("universe marks them ARCHIVED, so they are ineligible for a new entry today.")
    A("")
    for ticker in INVESTIGATE_STATUS_DISAGREEMENT:
        A("\n".join(_dossier(ticker, data, rubix)))
        record = data["canonical_by_ticker"].get(ticker)
        related = []
        if record is not None:
            # Many archived rows carry no company name at all. Matching on the
            # empty string would group every one of them together and invent a
            # relationship that does not exist.
            name = _norm_name(record.company_name)
            if name:
                related = sorted(
                    other.canonical_symbol
                    for other in data["canonical_by_ticker"].values()
                    if other.canonical_symbol != ticker
                    and _norm_name(other.company_name) == name
                )
        index = data["canonical_by_ticker"]
        rendered = ", ".join(
            f"`{t}` ({_canonical_status(index[t])})" for t in related
        )
        A(f"- other canonical rows with the same company name: "
          f"{rendered or 'NONE (this archived row carries no company name)'}")
        # The real link runs through the workbook's ISIN, since the archived
        # canonical row has neither name nor ISIN of its own.
        workbook_row = data["workbook_by_ticker"].get(ticker)
        if workbook_row is not None and workbook_row.isin:
            sharing = sorted(
                other.canonical_symbol for other in index.values()
                if other.isin == workbook_row.isin
            )
            rendered_isin = ", ".join(
                f"`{t}` ({_canonical_status(index[t])}, "
                f"subscribed={_yn(_is_subscribed(index[t]))})" for t in sharing
            )
            A(f"- canonical rows carrying this ticker's WORKBOOK ISIN "
              f"`{workbook_row.isin}`: {rendered_isin or 'NONE'}")
        A("")
    A("**Decision required:** whether the archived state is correct, or whether")
    A("these are the current tickers of pairs whose retired form is the one")
    A("currently flagged active. Resolving them changes live universe composition.")
    A("")

    A("### 7.3 ISIN collision — `EGS3E1E1C013` (`AMII` vs `ARVA`)")
    A("")
    isin = INVESTIGATE_ISIN_COLLISION
    workbook_hit = [row for row in data["workbook_by_ticker"].values()
                    if row.isin == isin]
    canonical_hit = sorted(
        r.canonical_symbol for r in data["canonical_by_ticker"].values()
        if r.isin == isin
    )
    A(f"- workbook rows carrying this ISIN: "
      + (", ".join(f"`{row.ticker}` ({row.company})" for row in workbook_hit)
         or "NONE"))
    A(f"- canonical rows carrying this ISIN: "
      + (", ".join(f"`{t}` ({data['canonical_by_ticker'][t].company_name}, "
                   f"{_canonical_status(data['canonical_by_ticker'][t])})"
                   for t in canonical_hit) or "NONE"))
    A("")
    for ticker in sorted({row.ticker for row in workbook_hit} | set(canonical_hit)):
        A("\n".join(_dossier(ticker, data, rubix)))
    names = {_norm_name(row.company) for row in workbook_hit} | {
        _norm_name(data["canonical_by_ticker"][t].company_name)
        for t in canonical_hit
    }
    A(f"- distinct normalized company names sharing this ISIN: **{len(names)}**")
    for name in sorted(names):
        A(f"    - {name}")
    A("")
    classification = (
        "DIFFERENT company names, which is inconsistent with a ticker change and "
        "consistent with an ISIN data error in the canonical file"
        if len(names) > 1 else
        "the SAME company name, consistent with a ticker change"
    )
    A(f"**Classification (evidence-based, not resolved):** the rows sharing this "
      f"ISIN carry {classification}. Status: {UNRESOLVED}.")
    A("")
    A("Note which side agrees with which source: the workbook and the canonical file")
    A("agree with each other on `AMII`'s name AND its ISIN, while `ARVA` appears")
    A("only in the canonical file, under a different company name, holding the same")
    A("ISIN — yet `ARVA` is the side that is subscribed and observed. So the traded")
    A("security and the correctly-attributed ISIN are on opposite rows.")
    A("")
    A("**Decision required:** which ticker this ISIN belongs to, and what the other")
    A("row's correct ISIN is. Not inferable — no source here can adjudicate it.")
    A("")

    A("### 7.4 Canonical-active with stale workbook price date")
    A("")
    A(f"**{len(stale)}** rows, itemised in §6. They are currently active and")
    A("therefore inside the operational universe.")
    A("")
    A("**Decision required:** whether listing status should ever gate universe")
    A("membership, and if so on what rule. Any change alters universe composition")
    A("and breaks comparability with already-validated sessions, so it is a dated")
    A("decision rather than a cleanup.")
    A("")

    # ---- 8 ----
    A("## 8. Proposed Phase 2 Inputs")
    A("")
    A("Not an implementation plan. These are the decisions and data a future apply")
    A("phase would need as *inputs*. Nothing here is implemented, scheduled, or")
    A("authorized by this report.")
    A("")
    A("Decisions that would have to exist first:")
    A("")
    A(f"1. Resolution for each of the **{len(conflicts)}** identity conflicts in §4 —")
    A("   which ticker is current, and what becomes of the other row.")
    A("2. Resolution for `FTNS`, `HBCO`, `UTOP` (§7.1).")
    A("3. Resolution for `AIFI`, `AIHC`, `ELWA` (§7.2).")
    A("4. Resolution for the `EGS3E1E1C013` collision (§7.3).")
    A(f"5. A rule — or an explicit decision to have none — for the "
      f"**{len(stale)}** stale-price active rows (§7.4).")
    A("6. A correct ISIN, or an accepted blank, for each of the "
      f"**{len(data['canonical_active_missing_isin'])}** active rows with no ISIN (§3.4).")
    A("")
    A("Data that would be required:")
    A("")
    A("- an authoritative ticker↔ISIN reconciliation for the conflicted rows;")
    A("- a decision on whether sector membership is required for a symbol to be")
    A("  operationally eligible;")
    A("- a recorded universe-composition change policy, since altering the active")
    A("  set changes what future sessions are comparable to.")
    A("")
    A("Explicitly NOT proposed and NOT done here: no sector fields added, no")
    A("`sector_context()`, no `universe_changes.csv`, no CSV edit, no dashboard")
    A("change, no test authored, no branch merged, nothing committed.")
    A("")

    # ---- 9 ----
    A("## 9. Files Touched")
    A("")
    A("\n".join(_table(
        ["Path", "Action"],
        [["`scripts/import_egx_sector_universe.py`", "created (read-only reporter)"],
         ["`docs/PHASE_1_EGX_UNIVERSE_RECONCILIATION.md`", "created (this report)"]],
    )))
    A("")
    A("No other project file was created, modified, or deleted. Specifically")
    A("unmodified: `core/universe.py`, `data/universe/egx_universe.csv`,")
    A("`data/rubix_live_market.db` and its `-wal`, every ORB research database,")
    A("everything under `F:/EGX_ORB_automation_runtime_wt/`, `scalping_orb/`,")
    A("`providers/`, `dashboard/`, `app.py`. No branch was merged, rebased,")
    A("cherry-picked, committed, or pushed. `universe_changes.csv` was not created.")
    A("")
    A("### One precise caveat about the Rubix `-shm` file")
    A("")
    A("Stated exactly rather than glossed: reading a WAL-mode SQLite database")
    A("requires mapping its `-shm` shared-memory index, so the `-shm` file's")
    A("modification time advances when this report runs. That is a consequence of")
    A("opening the database at all — the production collector's own readers do the")
    A("same — and it is **not** a change to database content: the `.db` and `-wal`")
    A("payloads are untouched, no page is written, no WAL frame is added, and no")
    A("checkpoint occurs. Verify by comparing `.db` and `-wal` digests before and")
    A("after a run; they are identical.")
    A("")
    A("The alternative, `immutable=1`, would avoid touching `-shm` entirely but")
    A("instructs SQLite to ignore the WAL — against a live database with a very")
    A("large uncheckpointed WAL that would silently return stale data. Correct")
    A("evidence was preferred over an untouched timestamp.")
    A("")
    A("---")
    A("")
    A("**PHASE 2: NOT STARTED.**")

    return "\n".join(L)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "PHASE 1 read-only EGX universe reconciliation reporter. "
            "This tool cannot write: it has no --apply mode, and adding one is "
            "out of scope for Phase 1."
        ),
        epilog="Report is written to stdout. Redirect it if you want it on disk.",
    )
    parser.add_argument("--workbook", default=DEFAULT_WORKBOOK)
    parser.add_argument("--universe", default=UNIVERSE_SOURCE)
    parser.add_argument("--rubix-db", default=DEFAULT_RUBIX_DB)
    args = parser.parse_args(argv)

    if WRITE_MODE_SUPPORTED:                     # pragma: no cover - constant
        raise SystemExit("Phase 1 forbids a write path.")

    workbook_rows, header, sheets, sector_tabs, exceptions, mapping = (
        load_workbook_rows(args.workbook)
    )
    canonical_records = load_universe(args.universe)
    rubix = load_rubix_evidence(args.rubix_db)

    checks, details = validate_workbook(workbook_rows, sector_tabs, header)
    data = reconcile(workbook_rows, canonical_records, rubix)

    provenance = [
        ("Workbook", f"`{Path(args.workbook).name}` sha256 "
                     f"`{_sha256(args.workbook)}`"),
        ("Workbook sheets", f"{len(sheets)}"),
        ("Canonical universe", f"`{args.universe}` sha256 "
                               f"`{_sha256(args.universe)}`"),
        ("Canonical source stamp",
         f"{canonical_records[0].source} as-of {canonical_records[0].source_as_of}"),
        ("Rubix database", f"`{args.rubix_db}` (opened mode=ro, query_only)"),
        ("Rubix feed watermark", f"`{rubix.watermark}`"),
    ]

    report = render(data, checks, details, rubix, provenance, exceptions, mapping)
    # The report carries set-notation and arrows. A Windows console defaults to a
    # legacy code page that cannot encode them, which would truncate the report
    # at the first such character rather than fail loudly at the end.
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except (AttributeError, ValueError):          # pragma: no cover
        pass
    sys.stdout.write(report + "\n")
    return 0 if all(check[3] == "PASS" for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
