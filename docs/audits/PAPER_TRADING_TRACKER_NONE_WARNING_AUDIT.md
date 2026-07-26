# Paper Trading Tracker None-Value Warning Audit

Date: 2026-07-26  
Worktree: `D:\EGX_AI_Trader_PAPER_TRACKER_FIX`  
Branch: `fix/paper-trading-none-warning`  
Base: `cf2e623124f5ae0ae323512643c2274e37c36094`

## Scope and invariants

This repair is limited to PaperTradingTracker input validation, typed operational
outcomes, and error reporting. It does not change strategy, AI, provider routing,
ranking, entry/exit rules, portfolio sizing, commissions, slippage, fill
assumptions, P/L, or production gates.

## Exact warning and reproduction

The original operational scan completed as `RUN_20260726_132729` and printed:

```text
Paper Trading Tracker Error -> float() argument must be a string or a real number, not 'NoneType'
```

This was not emitted by a Python logger. It was an unstructured `print` in
`core.scanner.scan_symbols`, after the tracker exception escaped. Reproduction
against a temporary copy of the real paper-trade CSV produced this sanitized
traceback:

```text
TypeError: float() argument must be a string or a real number, not 'NoneType'
  core/paper_trading.py, PaperTradingTracker.update_open_trades
    buy_low = float(row.get("BuyLow", row["EntryPrice"]))
```

The first affected record was:

- Symbol: `MIPH.CA`
- Record state: `OPEN`
- Signal session: `2026-07-06`
- Missing field: `BuyLow`
- Source: legacy `data/paper_trades.csv` row
- Frequency: once per scan, because the exception aborted the whole tracker
  update at the first affected row; it would recur on later scans.

No API key, environment value, broker credential, or unrelated private record
was printed during reproduction.

## Root cause

The production CSV uses an older schema that has no `BuyLow` column. `load()`
adds absent columns as nullable values for backward compatibility. Once the
column exists, `Series.get("BuyLow", row["EntryPrice"])` returns its `None`
value; the second argument is only used when the key is absent. The unconditional
`float(None)` then fails before the code checks whether the record is
`PENDING_ENTRY` or `OPEN`.

`BuyLow` is required to decide whether a pending order's entry band was touched.
It is not used by the existing exit manager for an already-open position. The
field is therefore:

- required for a new/pending entry;
- a **D. legacy record field** for old `OPEN` rows;
- not a reason to fabricate a price or stop exit tracking.

`EntryDate` is also missing on these legacy rows. Existing behavior already used
the immutable `SignalDate` in memory for exit evaluation. The repair preserves
that behavior and does not write a fabricated `EntryDate`.

## Field flow

```text
legacy paper_trades.csv (no BuyLow column)
    -> PaperTradingTracker.load()
    -> compatibility column BuyLow=None
    -> update_open_trades()
    -> old unconditional float(BuyLow)
    -> TypeError
    -> scanner print warning
```

After the repair:

```text
legacy OPEN row
    -> validate immutable identity and frozen exit fields
    -> BuyLow remains None
    -> LEGACY_INCOMPLETE (DEBUG)
    -> existing ExitManager receives unchanged entry/stop/targets

PENDING_ENTRY row with missing BuyLow
    -> SKIPPED_MISSING_REQUIRED_FIELD (deduplicated WARNING)
    -> no fill calculation
    -> no partial write
```

## Handling policy

- Missing values stay missing. No `fillna(0)`, `value or 0`, current timestamp,
  current price, or substitute execution value is used.
- Required financial inputs must be finite. Entry, stop, and target prices must
  also be positive.
- Zero remains valid only for commission and slippage.
- `NaN`, infinity, booleans, invalid dates, and negative costs are rejected.
- Expected provider unavailability is `PRICE_UNAVAILABLE` at DEBUG severity.
- No future candle is `EVALUATION_NOT_DUE` at DEBUG severity.
- A complete but still-open position is `PENDING_DATA` at DEBUG severity.
- Missing legacy-only fields are `LEGACY_INCOMPLETE` at DEBUG severity.
- Missing required creation/update fields are explicit contract violations and
  generate one deduplicated WARNING per symbol/date/field/state.
- Unexpected market-data preparation exceptions are logged with a full traceback
  and re-raised. They are not swallowed.
- Existing public return values remain integer counts. Typed outcomes are
  additive through `PaperTradingTracker.last_outcomes`.

## Database and record safety

The affected tracker persists to CSV, not SQLite. No database schema or
production migration is required.

- Required-field validation occurs before record creation or mutation.
- A missing required creation field produces no row.
- An invalid exit result is validated before any close fields are written.
- Existing legacy rows are not rewritten merely to add `BuyLow` or `EntryDate`.
- Saves continue to occur only after a valid existing state transition.
- Tests redirect `PAPER_TRADES_FILE` to per-test temporary paths.
- The operational scan used a temporary copy of the paper CSV.
- The source tracker SHA-256 remained
  `4397d59481526c9f47004a41936b3def9d3906c9b9f7b67e45f20f827eea5b3d`.
- No user paper-trading SQLite database, append-only trigger, UUID, or forward
  record was modified by the tests.

## Golden regression comparison

A deterministic complete lifecycle was exercised with the existing
`ExitManager`:

| Field | Expected and observed |
|---|---:|
| Initial state | `PENDING_ENTRY` |
| Entry session | `2026-07-02` |
| Frozen buy-high | `101.0` |
| Entry execution price | `101.0505` |
| Stop | `95.0` |
| Target 1 | `110.0` |
| Target 2 | `115.0` |
| Exit session | `2026-07-03` |
| Exit execution price | `109.945` |
| Exit reason | `Target1` |
| Holding days | `1` |
| Final state | `CLOSED` |

The tracker has no quantity, portfolio heat, realised/unrealised P/L, or
commission columns. Those values remain owned by their existing portfolio and
backtest components and were not added, defaulted, or recalculated here. The
full suite confirms those components remain unchanged.

## Real operational check

A real research/paper Market Scan was run with the current authenticated
provider configuration for `COMI.CA`, `SWDY.CA`, and `FWRY.CA`. The scan used a
temporary copy of the paper tracker, and no broker/order interface was invoked.

- Run ID: `RUN_20260726_143514`
- Symbols analysed successfully: 3
- Tracker calls: 2 (`update_open_trades`, then `record_signals`)
- Old None warning before repair: 1 per scan
- None warning after repair: 0
- New records created: 0
- Required-field skips: 0
- Existing records still pending: 6
- Valid existing records updated in the temporary copy: 17
- Legacy incomplete events: 46 (two nullable legacy fields across 23 open rows)
- Source tracker changed: No
- Production: Disabled; research/paper execution only

The earlier all-symbol operational attempt was terminated after its bounded
timeout and its two matching child processes were explicitly stopped. It made no
change to the source paper-trade file. The successful three-symbol run then
validated the same scanner/tracker integration boundary deterministically.

## Validation

| Check | Result |
|---|---|
| New None-handling tests | 26 passed |
| Paper/Forward/Scanner/Portfolio/Risk/Reporting selection | 77 passed |
| AI Stock Analysis and AI Narrative selection | 388 passed |
| Launcher/Rubix selection | 122 passed |
| Related consistency selection | 102 passed |
| Full repository suite | 1094 passed, 3 skipped |
| Streamlit smoke | `STREAMLIT_SMOKE_OK` |
| Python syntax/import check | Passed |
| `git diff --check` | Passed |

The three full-suite skips are the repository's existing conditional skips; this
change added none.

## Files changed

- `core/paper_trading.py`
  - typed tracker outcomes;
  - strict finite/date validation;
  - legacy `OPEN` compatibility without value fabrication;
  - deduplicated logging;
  - safe exit-contract validation.
- `core/scanner.py`
  - replaces the unstructured warning `print` with traceback-preserving logger
    handling for genuinely unexpected tracker failures.
- `tests/test_paper_trading_none_handling.py`
  - temporary-storage edge cases and golden lifecycle regression.
- `docs/audits/PAPER_TRADING_TRACKER_NONE_WARNING_AUDIT.md`
  - this audit.

## Conclusion

The warning was caused by treating an optional legacy `OPEN`-record field as an
unconditional numeric entry field. The repaired boundary preserves unknown
values as unknown, prevents incomplete new records, keeps valid legacy positions
trackable, and leaves every financial calculation and provider decision
unchanged.
