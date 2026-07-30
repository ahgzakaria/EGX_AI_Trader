"""Materialize the authoritative EGX universe from the official EODHD list.

Retrieves ``exchange-symbol-list/EGX`` through the project's authenticated EODHD
client (no HTML scraping, no Yahoo, nothing typed by hand), validates the active
ticker set, writes a timestamped raw snapshot for reproducibility, and rebuilds
``data/universe/egx_universe.csv`` plus the migration audit under
``reports/audits/universe/``.

The retired 265-symbol ``data/symbols.csv`` is archived and removed from the
runtime tree so it cannot survive as a silent fallback.

Read-only with respect to trading state: no database, trade, run, report or
watchlist is modified. Changes no strategy, threshold, scoring, risk rule or
provider policy.

Usage::

    python scripts/migrate_eodhd_241_universe.py [--expected-count 241] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.universe import (  # noqa: E402
    ENGINE_SUFFIX,
    EODHD_SUFFIX,
    EXCHANGE,
    FIELDNAMES,
    RUBIX_EXCHANGE,
    RUBIX_UNVERIFIED,
    RUBIX_VERIFIED,
    UNIVERSE_SOURCE,
    canonical,
)
from providers.eodhd_client import EODHDClient  # noqa: E402

EXPECTED_ACTIVE_COUNT = 241

EODHD_ENDPOINT = "exchange-symbol-list/EGX"
SOURCE_LABEL = f"EODHD {EODHD_ENDPOINT}"

UNIVERSE_PATH = PROJECT_ROOT / UNIVERSE_SOURCE
SNAPSHOT_DIR = PROJECT_ROOT / "data" / "universe" / "snapshots"
ARCHIVE_DIR = PROJECT_ROOT / "data" / "universe" / "archive"
LEGACY_SOURCE = PROJECT_ROOT / "data" / "symbols.csv"
LEGACY_ARCHIVE = ARCHIVE_DIR / "legacy_symbols_265.csv"
AUDIT_DIR = PROJECT_ROOT / "reports" / "audits" / "universe"
RUBIX_DB = PROJECT_ROOT / "data" / "rubix_live_market.db"


class MigrationAborted(RuntimeError):
    """The live official response failed validation; nothing was activated."""


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #

def fetch_official_rows(client=None, *, delisted=False):
    """Return the official EODHD rows for EGX (live, cache-bypassing)."""

    client = client or EODHDClient()
    params = {"delisted": 1} if delisted else None
    return client.get_json(EODHD_ENDPOINT, params, cache_ttl_seconds=None, force=True)


def latest_snapshot(kind="active"):
    """The newest saved raw response for ``kind``, or ``None``."""

    paths = sorted(SNAPSHOT_DIR.glob(f"eodhd_egx_{kind}_*.json"))
    if not paths:
        return None
    return json.loads(paths[-1].read_text(encoding="utf-8"))


def save_snapshot(rows, retrieved_at, *, kind="active"):
    """Persist the raw provider response verbatim, stamped, for audit."""

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = retrieved_at.strftime("%Y%m%dT%H%M%SZ")
    path = SNAPSHOT_DIR / f"eodhd_egx_{kind}_{stamp}.json"
    path.write_text(json.dumps({
        "endpoint": EODHD_ENDPOINT,
        "delisted": kind == "delisted",
        "retrieved_at_utc": retrieved_at.isoformat(),
        "count": len(rows),
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def validate_active_rows(rows, *, expected_count=EXPECTED_ACTIVE_COUNT,
                         delisted_codes=frozenset()):
    """Raise :class:`MigrationAborted` unless the live list is activatable.

    The count is never forced or truncated: a mismatch stops the migration and is
    reported with the actual figure.
    """

    problems = []
    codes = [str(row.get("Code") or "").strip().upper() for row in rows]

    if len(rows) != expected_count:
        raise MigrationAborted(
            f"EODHD returned {len(rows)} active tickers, expected {expected_count} "
            f"(difference {len(rows) - expected_count:+d}). Endpoint "
            f"'{EODHD_ENDPOINT}'. Nothing was activated."
        )

    blank = [i for i, code in enumerate(codes, start=1) if not code]
    if blank:
        problems.append(f"blank ticker at row(s) {blank}")

    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        problems.append(f"duplicate ticker(s): {', '.join(duplicates)}")

    eodhd_symbols = [f"{code}{EODHD_SUFFIX}" for code in codes]
    if len(set(eodhd_symbols)) != len(eodhd_symbols):
        problems.append("duplicate EODHD symbol after applying the .EGX suffix")

    stripped = [symbol[: -len(EODHD_SUFFIX)] for symbol in eodhd_symbols]
    if len(set(stripped)) != len(stripped):
        problems.append("duplicate ticker after removing the .EGX suffix")

    missing_names = [code for code, row in zip(codes, rows)
                     if not str(row.get("Name") or "").strip()]
    if missing_names:
        problems.append(f"blank company name for: {', '.join(missing_names)}")

    bad_exchange = sorted({str(row.get("Exchange") or "").strip().upper()
                           for row in rows} - {EXCHANGE})
    if bad_exchange:
        problems.append(f"non-{EXCHANGE} exchange value(s): {', '.join(bad_exchange)}")

    missing_currency = [code for code, row in zip(codes, rows)
                        if not str(row.get("Currency") or "").strip()]
    if missing_currency:
        problems.append(f"missing currency for: {', '.join(missing_currency)}")

    missing_type = [code for code, row in zip(codes, rows)
                    if not str(row.get("Type") or "").strip()]
    if missing_type:
        problems.append(f"missing instrument type for: {', '.join(missing_type)}")

    introduced_delisted = sorted(set(codes) & set(delisted_codes))
    if introduced_delisted:
        problems.append(
            f"delisted ticker(s) present in the active list: "
            f"{', '.join(introduced_delisted)}"
        )

    if problems:
        raise MigrationAborted(
            "Official EODHD response failed validation; nothing was activated:\n  - "
            + "\n  - ".join(problems)
        )
    return True


# --------------------------------------------------------------------------- #
# Explicit Rubix mapping
# --------------------------------------------------------------------------- #

def observed_rubix_tickers(db_path=RUBIX_DB):
    """Tickers actually seen on the Rubix feed under the CASE exchange.

    Opened read-only. A mapping is emitted only for an observed ticker, so no
    subscription key is ever produced by blind suffix substitution.
    """

    if not Path(db_path).is_file():
        return frozenset()
    try:
        connection = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return frozenset()
    try:
        rows = connection.execute(
            "SELECT DISTINCT ticker FROM quotes WHERE UPPER(TRIM(exchange)) = ?",
            (RUBIX_EXCHANGE,),
        ).fetchall()
    except sqlite3.Error:
        return frozenset()
    finally:
        connection.close()
    return frozenset(canonical(row[0]) for row in rows if row and row[0])


def rubix_mapping(code, observed):
    """(rubix_symbol, status) — explicit, never guessed."""

    if code in observed:
        return f"{RUBIX_EXCHANGE}~{code}", RUBIX_VERIFIED
    return "", RUBIX_UNVERIFIED


# --------------------------------------------------------------------------- #
# Old universe
# --------------------------------------------------------------------------- #

def read_legacy_universe():
    """Return the retired universe tickers from the live tree or the archive."""

    for path in (LEGACY_SOURCE, LEGACY_ARCHIVE):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(text.splitlines())
        column = "Ticker" if "Ticker" in (reader.fieldnames or []) else None
        if column is None:
            continue
        tickers, seen = [], set()
        for row in reader:
            ticker = canonical(row.get(column))
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
        if tickers:
            return tuple(tickers), path
    return (), None


def archive_legacy_universe(dry_run=False):
    """Copy the retired list into the audit archive and remove it from runtime."""

    if not LEGACY_SOURCE.is_file():
        return LEGACY_ARCHIVE if LEGACY_ARCHIVE.is_file() else None
    if dry_run:
        return LEGACY_ARCHIVE
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    LEGACY_ARCHIVE.write_bytes(LEGACY_SOURCE.read_bytes())
    LEGACY_SOURCE.unlink()
    return LEGACY_ARCHIVE


# --------------------------------------------------------------------------- #
# Universe build
# --------------------------------------------------------------------------- #

def _record(row, *, active, observed, source_as_of):
    code = canonical(row.get("Code"))
    rubix_symbol, rubix_status = rubix_mapping(code, observed)
    return {
        "canonical_symbol": code,
        "eodhd_symbol": f"{code}{EODHD_SUFFIX}",
        "engine_symbol": f"{code}{ENGINE_SUFFIX}",
        "company_name": str(row.get("Name") or "").strip(),
        "exchange": str(row.get("Exchange") or "").strip().upper(),
        "currency": str(row.get("Currency") or "").strip().upper(),
        "instrument_type": str(row.get("Type") or "").strip(),
        "isin": str(row.get("Isin") or "").strip(),
        "rubix_symbol": rubix_symbol,
        "rubix_mapping_status": rubix_status,
        "is_active": "true" if active else "false",
        "source": SOURCE_LABEL if active else f"{SOURCE_LABEL}?delisted=1",
        "source_as_of": source_as_of,
    }


def build_universe_rows(active_rows, delisted_rows, legacy_tickers, observed,
                        source_as_of):
    """Active records plus the archived records history still needs to name."""

    records = [_record(row, active=True, observed=observed,
                       source_as_of=source_as_of) for row in active_rows]
    active_codes = {record["canonical_symbol"] for record in records}

    delisted_by_code = {canonical(row.get("Code")): row for row in delisted_rows}
    archived_codes = (set(delisted_by_code) | set(legacy_tickers)) - active_codes
    for code in sorted(archived_codes):
        row = delisted_by_code.get(code)
        if row is None:
            # Retired from the old operational list with no official record left.
            row = {"Code": code, "Name": "", "Exchange": EXCHANGE,
                   "Currency": "", "Type": "", "Isin": ""}
        record = _record(row, active=False, observed=observed,
                         source_as_of=source_as_of)
        if not delisted_by_code.get(code):
            record["source"] = "retired from legacy operational universe"
        records.append(record)

    records.sort(key=lambda r: (r["is_active"] != "true", r["canonical_symbol"]))
    return records


def write_universe(records, dry_run=False):
    if dry_run:
        return UNIVERSE_PATH
    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with UNIVERSE_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FIELDNAMES))
        writer.writeheader()
        writer.writerows(records)
    return UNIVERSE_PATH


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #

def _write_csv(path, fieldnames, rows, dry_run=False):
    if dry_run:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)
    return path


def build_audit(active_rows, legacy_tickers, records, observed, retrieved_at,
                dry_run=False):
    """Write every migration comparison artefact; return the summary dict."""

    active_by_code = {canonical(row.get("Code")): row for row in active_rows}
    new_codes = set(active_by_code)
    old_codes = set(legacy_tickers)
    common = sorted(old_codes & new_codes)
    removed = sorted(old_codes - new_codes)
    added = sorted(new_codes - old_codes)
    archived_names = {r["canonical_symbol"]: r["company_name"]
                      for r in records if r["is_active"] != "true"}
    by_code = {r["canonical_symbol"]: r for r in records}

    _write_csv(AUDIT_DIR / "eodhd_241_snapshot.csv",
               ("canonical_symbol", "eodhd_symbol", "company_name", "exchange",
                "currency", "instrument_type", "isin"),
               [{"canonical_symbol": code,
                 "eodhd_symbol": f"{code}{EODHD_SUFFIX}",
                 "company_name": str(active_by_code[code].get("Name") or "").strip(),
                 "exchange": str(active_by_code[code].get("Exchange") or "").strip(),
                 "currency": str(active_by_code[code].get("Currency") or "").strip(),
                 "instrument_type": str(active_by_code[code].get("Type") or "").strip(),
                 "isin": str(active_by_code[code].get("Isin") or "").strip()}
                for code in sorted(new_codes)], dry_run)

    _write_csv(AUDIT_DIR / "universe_common.csv",
               ("canonical_symbol", "eodhd_symbol", "company_name"),
               [{"canonical_symbol": code, "eodhd_symbol": f"{code}{EODHD_SUFFIX}",
                 "company_name": by_code[code]["company_name"]} for code in common],
               dry_run)

    _write_csv(AUDIT_DIR / "universe_removed.csv",
               ("canonical_symbol", "archived_company_name", "still_readable_in_history",
                "eligible_for_new_entry"),
               [{"canonical_symbol": code,
                 "archived_company_name": archived_names.get(code, ""),
                 "still_readable_in_history": "true",
                 "eligible_for_new_entry": "false"} for code in removed], dry_run)

    _write_csv(AUDIT_DIR / "universe_added.csv",
               ("canonical_symbol", "eodhd_symbol", "company_name", "isin"),
               [{"canonical_symbol": code, "eodhd_symbol": f"{code}{EODHD_SUFFIX}",
                 "company_name": by_code[code]["company_name"],
                 "isin": by_code[code]["isin"]} for code in added], dry_run)

    # The retired universe carried no company names at all, so every retained
    # symbol gains one. That is recorded as an explicit name change.
    name_changes = [{"canonical_symbol": code, "old_company_name": "",
                     "new_company_name": by_code[code]["company_name"],
                     "change_type": "NAME_ADDED"} for code in common]
    _write_csv(AUDIT_DIR / "universe_name_changes.csv",
               ("canonical_symbol", "old_company_name", "new_company_name",
                "change_type"), name_changes, dry_run)

    mapping_changes = []
    for code in sorted(new_codes):
        record = by_code[code]
        mapping_changes.append({
            "canonical_symbol": code,
            "old_provider_symbol": f"{code}{ENGINE_SUFFIX}" if code in old_codes else "",
            "new_eodhd_symbol": record["eodhd_symbol"],
            "rubix_symbol": record["rubix_symbol"],
            "rubix_mapping_status": record["rubix_mapping_status"],
        })
    _write_csv(AUDIT_DIR / "universe_mapping_changes.csv",
               ("canonical_symbol", "old_provider_symbol", "new_eodhd_symbol",
                "rubix_symbol", "rubix_mapping_status"), mapping_changes, dry_run)

    missing_names = sorted(code for code in new_codes
                           if not by_code[code]["company_name"])
    ambiguous_rubix = sorted(code for code in new_codes
                             if by_code[code]["rubix_mapping_status"] != RUBIX_VERIFIED)
    names_to_codes = {}
    for code in sorted(new_codes):
        name = by_code[code]["company_name"]
        if name:
            names_to_codes.setdefault(name, []).append(code)
    duplicate_names = sorted(name for name, codes in names_to_codes.items()
                             if len(codes) > 1)
    ambiguous_names = {name: names_to_codes[name] for name in duplicate_names}

    summary = {
        "endpoint": EODHD_ENDPOINT,
        "source": SOURCE_LABEL,
        "retrieved_at_utc": retrieved_at.isoformat(),
        "old_count": len(old_codes),
        "new_count": len(new_codes),
        "expected_count": EXPECTED_ACTIVE_COUNT,
        "exactly_expected": len(new_codes) == EXPECTED_ACTIVE_COUNT,
        "common": common,
        "removed": removed,
        "added": added,
        "missing_names": missing_names,
        "duplicate_company_names": duplicate_names,
        "ambiguous_company_names": ambiguous_names,
        "duplicate_symbols": [],
        "name_changes": len(name_changes),
        "rubix_verified": sorted(code for code in new_codes
                                 if by_code[code]["rubix_mapping_status"] == RUBIX_VERIFIED),
        "rubix_unverified": ambiguous_rubix,
        "archived_records": sum(1 for r in records if r["is_active"] != "true"),
        "rubix_feed_observations": len(observed),
    }
    _write_markdown(summary, by_code, archived_names, dry_run)
    return summary


def _write_markdown(summary, by_code, archived_names, dry_run=False):
    path = AUDIT_DIR / "EODHD_241_UNIVERSE_MIGRATION.md"
    if dry_run:
        return path

    def _bullets(codes, names):
        if not codes:
            return "_none_\n"
        return "".join(f"- `{code}` — {names.get(code) or 'Historical / Inactive Symbol'}\n"
                       for code in codes)

    lines = [
        "# EODHD 241 Universe Migration",
        "",
        f"- **Source**: `{summary['source']}` (authenticated EODHD REST; no HTML "
        "scraping, no Yahoo, no manual entry)",
        f"- **Retrieved at (UTC)**: `{summary['retrieved_at_utc']}`",
        f"- **Active tickers returned**: **{summary['new_count']}**",
        f"- **Exactly {summary['expected_count']}**: "
        f"**{'yes' if summary['exactly_expected'] else 'no'}**",
        f"- **Old operational universe**: {summary['old_count']} symbols",
        f"- **Common**: {len(summary['common'])} · **Removed**: "
        f"{len(summary['removed'])} · **Added**: {len(summary['added'])}",
        f"- **Archived (inactive) records retained for history**: "
        f"{summary['archived_records']}",
        "",
        "Every active record carries the full company name, the official "
        "`.EGX` symbol, exchange, currency, instrument type and ISIN where "
        "supplied. The retired 265-symbol list is archived under "
        "`data/universe/archive/legacy_symbols_265.csv` and is not on any runtime "
        "fallback chain.",
        "",
        "## Removed symbols",
        "",
        "Not eligible for a new entry. Still readable in historical trades, saved "
        "runs and reports, and still monitorable while an open paper position "
        "exists.",
        "",
        _bullets(summary["removed"], archived_names),
        "## Added symbols",
        "",
        _bullets(summary["added"],
                 {code: by_code[code]["company_name"] for code in summary["added"]}),
        "## Data quality",
        "",
        f"- Missing company names: {summary['missing_names'] or '_none_'}",
        f"- Duplicate symbols: {summary['duplicate_symbols'] or '_none_'}",
        f"- Company names added to retained symbols: {summary['name_changes']} "
        "(the retired list stored tickers only)",
        "",
        "### Ambiguous company names",
        "",
        "Distinct EODHD tickers that share one company name. Both remain separate "
        "universe records; the pair is flagged for operator review.",
        "",
        ("".join(
            f"- \"{name}\" — "
            + ", ".join(f"`{code}`" for code in codes) + "\n"
            for name, codes in sorted(summary["ambiguous_company_names"].items()))
         or "_none_\n"),
        "## Rubix mapping",
        "",
        f"- Verified from live feed observation (`CASE~TICKER`): "
        f"**{len(summary['rubix_verified'])}**",
        f"- Unverified — no feed observation, no key emitted: "
        f"**{len(summary['rubix_unverified'])}**",
        "",
        "Mappings are explicit. A ticker never observed on the Rubix `CASE` feed "
        "produces no subscription key; nothing is derived by suffix substitution.",
        "",
        "Unverified: "
        + (", ".join(f"`{code}`" for code in summary["rubix_unverified"]) or "_none_"),
        "",
        "**Operational consequence.** An unverified symbol is a full member of the "
        "operational universe — daily refresh, historical loading, scans, selectors, "
        "AI analysis and backtests all include it — but it contributes no Rubix "
        "subscription key until its mapping is verified against a real feed "
        "observation. `build_rubix_subscription_plan` reports these under "
        "`unmapped_symbols` rather than fabricating `CASE~TICKER`.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def migrate(*, expected_count=EXPECTED_ACTIVE_COUNT, dry_run=False, client=None,
            from_snapshot=False):
    """Rebuild the universe and audit. ``from_snapshot`` replays the saved raw
    response instead of calling EODHD again (same data, no new API spend)."""

    snapshots = []
    if from_snapshot:
        active_snapshot = latest_snapshot("active")
        delisted_snapshot = latest_snapshot("delisted")
        if not active_snapshot:
            raise MigrationAborted(
                f"no saved snapshot under {SNAPSHOT_DIR}; run without --from-snapshot")
        active_rows = active_snapshot["rows"]
        delisted_rows = (delisted_snapshot or {}).get("rows", [])
        retrieved_at = datetime.fromisoformat(active_snapshot["retrieved_at_utc"])
    else:
        retrieved_at = datetime.now(timezone.utc)
        client = client or EODHDClient()
        active_rows = fetch_official_rows(client)
        delisted_rows = fetch_official_rows(client, delisted=True)
        if not dry_run:
            snapshots.append(save_snapshot(active_rows, retrieved_at, kind="active"))
            snapshots.append(save_snapshot(delisted_rows, retrieved_at, kind="delisted"))

    delisted_codes = {canonical(row.get("Code")) for row in delisted_rows}
    validate_active_rows(active_rows, expected_count=expected_count,
                         delisted_codes=delisted_codes)

    legacy_tickers, legacy_path = read_legacy_universe()
    observed = observed_rubix_tickers()
    source_as_of = retrieved_at.isoformat()

    records = build_universe_rows(active_rows, delisted_rows, legacy_tickers,
                                  observed, source_as_of)
    write_universe(records, dry_run)
    summary = build_audit(active_rows, legacy_tickers, records, observed,
                          retrieved_at, dry_run)
    archive_path = archive_legacy_universe(dry_run)
    summary["legacy_source"] = str(legacy_path) if legacy_path else ""
    summary["legacy_archive"] = str(archive_path) if archive_path else ""
    summary["snapshots"] = [str(path) for path in snapshots]
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-count", type=int, default=EXPECTED_ACTIVE_COUNT)
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and report without writing any file")
    parser.add_argument("--from-snapshot", action="store_true",
                        help="rebuild from the newest saved raw snapshot "
                             "instead of calling EODHD again")
    args = parser.parse_args(argv)

    try:
        summary = migrate(expected_count=args.expected_count, dry_run=args.dry_run,
                          from_snapshot=args.from_snapshot)
    except MigrationAborted as error:
        print(f"MIGRATION ABORTED\n{error}", file=sys.stderr)
        return 2

    print(f"source              : {summary['source']}")
    print(f"retrieved_at_utc    : {summary['retrieved_at_utc']}")
    print(f"active tickers      : {summary['new_count']} "
          f"(exactly {summary['expected_count']}: {summary['exactly_expected']})")
    print(f"old / common        : {summary['old_count']} / {len(summary['common'])}")
    print(f"removed / added     : {len(summary['removed'])} / {len(summary['added'])}")
    print(f"archived records    : {summary['archived_records']}")
    print(f"rubix verified      : {len(summary['rubix_verified'])}")
    print(f"rubix unverified    : {len(summary['rubix_unverified'])}")
    print(f"legacy archived to  : {summary['legacy_archive'] or '(already archived)'}")
    for path in summary["snapshots"]:
        print(f"snapshot            : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
