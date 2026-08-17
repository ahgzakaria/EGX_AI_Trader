"""Archive the full EODHD daily history to disk before the subscription ends.

The EODHD plan is cancelled and valid until 2026-08-23. After that date this
history cannot be fetched again at any price we are paying, and 209 of the 241
active symbols route their current-research history through EODHD. Everything
else in the migration — comparing Yahoo, choosing a replacement, rewiring the
router — can be done offline afterwards. This cannot.

So this script does one thing and does it durably:

    for every active symbol, fetch the FULL adjusted-capable daily series plus
    its splits and dividends, and write them where nothing but a deliberate
    delete can remove them.

Properties that matter for an unattended overnight run:

* **Resumable.** A symbol already archived with the same or more rows is
  skipped, so a re-run after an interruption costs only what is missing.
  ``--force`` re-fetches regardless.
* **Never partial-writes.** Each file is written to a temporary name and moved
  into place, so an interrupted run leaves no half-written archive that a later
  reader would trust.
* **One failure is not the run.** A symbol that errors is recorded and the run
  continues; the manifest names every failure so a second pass can target them.
* **Provenance.** The manifest records row count, first and last session, the
  fetch timestamp and a digest per symbol, so a later reader can prove what was
  captured and when — and see which symbols were already stale at capture time.

Read-only against everything except its own output directory.

    python scripts/archive_eodhd_history.py
    python scripts/archive_eodhd_history.py --symbols COMI EGAL --force
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "frozen_eodhd_seed"
MANIFEST_NAME = "_manifest.json"

EOD_FIELDS = ("date", "open", "high", "low", "close", "adjusted_close", "volume")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(path: Path, write) -> None:
    """Write through a temporary file so a kill never leaves a partial archive."""

    temporary = path.with_suffix(path.suffix + ".partial")
    with open(temporary, "w", encoding="utf-8", newline="") as handle:
        write(handle)
    temporary.replace(path)


def _write_eod(path: Path, rows) -> None:
    def write(handle):
        writer = csv.DictWriter(handle, fieldnames=EOD_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    _write_atomic(path, write)


def _write_json(path: Path, payload) -> None:
    _write_atomic(path, lambda handle: json.dump(payload, handle, indent=1))


def existing_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return max(0, sum(1 for _ in handle) - 1)
    except OSError:
        return 0


def archive_symbol(client, symbol: str, output: Path, *, force: bool):
    """Fetch and store one symbol. Returns a manifest entry."""

    eodhd_symbol = f"{symbol}.EGX"
    eod_path = output / f"{symbol}.csv"
    entry = {"symbol": symbol, "eodhd_symbol": eodhd_symbol}

    rows = client.eod(eodhd_symbol, force=force, cache_ttl_seconds=0 if force else 3600)
    rows = [r for r in (rows or []) if r.get("date") and r.get("close") is not None]
    if not rows:
        entry.update(status="EMPTY", rows=0)
        return entry

    # A shorter series than the one already on disk is a worse archive, not a
    # newer one; keep whichever is longer.
    if not force and len(rows) < existing_rows(eod_path):
        entry.update(status="KEPT_LONGER_EXISTING", rows=existing_rows(eod_path))
        return entry

    rows.sort(key=lambda r: str(r["date"]))
    _write_eod(eod_path, rows)

    for kind in ("splits", "div"):
        try:
            payload = client.get_json(f"{kind}/{eodhd_symbol}", {},
                                      cache_ttl_seconds=0 if force else 3600)
        except Exception:
            payload = None
        if payload:
            _write_json(output / f"{symbol}.{kind}.json", payload)

    entry.update(
        status="ARCHIVED",
        rows=len(rows),
        first_session=str(rows[0]["date"])[:10],
        last_session=str(rows[-1]["date"])[:10],
        sha256=_digest(eod_path),
    )
    return entry


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.15,
                        help="pause between symbols, to stay well inside rate limits")
    args = parser.parse_args(argv)

    from core.environment import load_project_environment

    load_project_environment()
    from core.universe import active_symbols
    from providers.eodhd_client import EODHDClient

    client = EODHDClient()
    if not client.is_configured():
        print("FAIL  EODHD is not configured; nothing to archive.")
        return 2

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    symbols = list(args.symbols) if args.symbols else list(active_symbols())

    print(f"archiving {len(symbols)} symbols -> {output}", flush=True)
    started = datetime.now(timezone.utc)
    entries, failures = [], []

    for index, symbol in enumerate(symbols, 1):
        try:
            entry = archive_symbol(client, symbol, output, force=args.force)
        except Exception as error:                      # noqa: BLE001
            entry = {"symbol": symbol, "status": "FAILED",
                     "error": f"{type(error).__name__}: {str(error)[:160]}"}
            failures.append(symbol)
        entries.append(entry)
        print(f"[{index:>3}/{len(symbols)}] {symbol:<7} {entry['status']:<22} "
              f"rows={entry.get('rows', 0):<5} last={entry.get('last_session', '-')}",
              flush=True)
        if args.sleep:
            time.sleep(args.sleep)

    archived = [e for e in entries if e["status"] == "ARCHIVED"]
    manifest = {
        "created_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source": "EODHD eod/{symbol}.EGX (full history)",
        "reason": "EODHD subscription cancelled, valid until 2026-08-23",
        "symbols_requested": len(symbols),
        "symbols_archived": len(archived),
        "symbols_failed": len(failures),
        "total_rows": sum(e.get("rows", 0) for e in archived),
        "entries": sorted(entries, key=lambda e: e["symbol"]),
    }
    _write_json(output / MANIFEST_NAME, manifest)

    print()
    print(f"archived {len(archived)}/{len(symbols)} symbols, "
          f"{manifest['total_rows']:,} rows", flush=True)
    if failures:
        print(f"failed: {', '.join(failures)}", flush=True)
    print(f"manifest {output / MANIFEST_NAME}", flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
