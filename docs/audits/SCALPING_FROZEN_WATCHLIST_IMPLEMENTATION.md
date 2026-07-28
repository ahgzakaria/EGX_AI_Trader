# Frozen Historical Scalping Watchlist — Phase 3A

**Status:** implemented and validated for research/paper decision support

**Branch:** `fix/scalping-historical-volatility-selection`

**Storage schema version:** `1`

**Default path:** `data/scalping_historical_watchlists.db`

**Validated target session:** 2026-07-28

**Validated historical cutoff:** 2026-07-27

**Provider:** `EODHD_DAILY`

## Scope and stop condition

Phase 3A persists the accepted Phase 2B historical selector as an immutable
session watchlist. It stores the complete hard-eligible ranking and separately
marks the displayed Top-N. It adds a read-only dashboard panel, an explicit
confirmed research rebuild, stored-list comparison and fail-closed typed
states.

It does not implement Live Entry Readiness, a scheduler, a live signal, a
broker route, production execution, portfolio behavior or automatic order
placement. No current Rubix field is accepted by the generator or stored in a
member.

## Architecture

The implementation is isolated in
`scalping_expected_range/frozen_watchlist.py`. It has four layers:

1. deterministic session, cutoff, universe and source identity helpers;
2. `FrozenWatchlistRepository`, a WAL SQLite repository;
3. `FrozenHistoricalWatchlistService`, the preparation/read/comparison API;
4. a historical-only panel in `dashboard/scalping.py`.

The default operational path is configurable with
`SCALPING_HISTORICAL_WATCHLIST_DB`. Tests and smoke checks inject operating
system temporary paths. The controlled real-data validation used two dedicated
temporary databases and removed them afterward; no watchlist was published to
the main workspace.

## Validated universe and source loading

The universe comes from
`data/eodhd/historical_symbol_routing.json`, excluding:

- `coverage_gap` entries; and
- the `EXCLUDED_NON_EQUITY` EGX30 ETF.

This deterministically resolves the accepted strict **225-symbol** population.
The loader calls only `load_eodhd_daily_history`, with EODHD Daily corporate
action and event-specific volume handling. There is no fallback.

For the real validation, a cache-only adapter read the approved main-workspace
EODHD cache with the API call budget set to zero. The three expected unavailable
EOD documents were DEIN, MEGM and TRTO. No network request was possible.

## Session and cutoff lifecycle

For a requested EGX session:

1. convert the current moment to Africa/Cairo when no date is supplied;
2. if the requested date is a weekend or confirmed holiday, resolve the next
   trading session;
3. walk backward to the previous valid EGX trading session;
4. use that date as the maximum historical cutoff;
5. fingerprint only completed observations through the cutoff;
6. claim the deterministic identity;
7. calculate the accepted 60/30 selector once;
8. publish the complete eligible universe and Top-N markers atomically.

The calendar honors the project holiday facade and the Sunday–Thursday EGX
week. Tests cover an ordinary weekday, Friday/Saturday weekend, confirmed
holiday, missing latest daily session, incomplete bar, stale cache and a UTC
moment that has crossed midnight in Cairo.

For target D, rows after the D-1 cutoff do not enter the source fingerprint or
selector. A D row becomes usable only when preparing D+1.

## Watchlist identity

The unique identity basis is canonical, sorted JSON containing:

```text
schema_version
target_session_date
historical_data_cutoff
provider
primary_lookback
recent_lookback
metric_version
config_version
source_fingerprint
universe_fingerprint
top_n
```

`identity_key = SHA-256(canonical JSON)`.

`watchlist_id = "FHW-" + first 24 hexadecimal identity-key characters`.

`generated_at`, `generation_run_id`, `generation_reason` and fingerprint
verification status are stored on the identity record. They are generation
metadata rather than uniqueness inputs: retrying the same historical identity
must return the existing record, not create another watchlist merely because
the clock or run UUID changed.

### Source fingerprint

For each normalized universe symbol, the service takes the final 60 completed
OHLCV observations through the cutoff and uses the accepted selector frame
fingerprint. Unavailable symbols receive a deterministic `UNAVAILABLE` marker.
The sorted `(symbol, fingerprint)` collection is hashed again:

```text
source_fingerprint = "sha256:" + SHA-256(sorted per-symbol source identities)
```

The universe fingerprint is the SHA-256 of the sorted normalized symbol list.
Provider, raw/adjusted behavior and metric/config versions remain separate
identity dimensions.

Changing a completed historical value creates a new source fingerprint and
watchlist ID while preserving the previous READY record. Changing a Rubix
attribute or a D row does not.

## Storage schema

SQLite schema version 1 contains three bounded entities.

### `watchlist_schema`

- installed schema version;
- installation timestamp.

### `watchlist_headers`

- deterministic `watchlist_id` primary key;
- unique `identity_key`;
- schema version;
- target session and historical cutoff;
- EODHD provider;
- 60-session and 30-session lookbacks;
- selector metric/config versions;
- aggregate source and universe fingerprints;
- source fingerprint verification status;
- Top-N;
- eligible and displayed counts;
- generated time and generation run ID;
- lifecycle status;
- generation reason;
- sanitized failure code/detail.

### `watchlist_members`

Each hard-eligible member retains:

- contiguous eligible historical rank and all-scoreable rank;
- displayed-candidate marker;
- confirmed and primary historical scores;
- Movement Potential;
- Range Stability;
- upper, lower and combined Zone Consistency;
- Zone confidence label;
- Liquidity Score;
- median range and P25–P75 normal range band;
- 2% range-hit frequency;
- typical upper and lower excursions;
- 60-session and 30-session valid counts and states;
- recent penalty;
- hard eligibility status;
- provider, per-member source fingerprint, latest session and cutoff;
- metric/config versions;
- deterministic historical explanation.

No current price, current change, live volume, RVOL, spread, VWAP, breakout,
momentum, live rank or entry state exists in the table.

Constraints enforce:

- foreign-key integrity;
- one symbol per watchlist;
- one historical rank per watchlist;
- positive ranks and Top-N;
- boolean displayed markers;
- allowed lifecycle statuses.

Indexes are limited to session/status lookup, latest READY lookup, member rank
and displayed-member rank.

## WAL, atomic publication and duplicate prevention

Every connection enables foreign keys and a 30-second busy timeout. The
database uses:

```text
PRAGMA journal_mode=WAL
PRAGMA synchronous=FULL
PRAGMA foreign_keys=ON
```

Generation claims the unique identity under `BEGIN IMMEDIATE` before the
expensive selector calculation. A second observer sees the existing
`GENERATING` or `READY` header and does not start another calculation.

After calculation, publication uses one transaction:

1. verify the header is still `GENERATING`;
2. validate nonempty members;
3. require contiguous ranks `1..eligible_count`;
4. require unique symbols and ranks;
5. require each member to be EODHD with a SHA-256 source fingerprint;
6. require displayed markers to equal `rank <= Top-N`;
7. insert every member;
8. update counts and status to `READY`;
9. query the stored count, distinct ranks and displayed count;
10. commit only if every check agrees.

A failure rolls back all member inserts. The service then records a sanitized
`FAILED` header in a separate transaction. An operating-system interruption
can leave a visible `GENERATING` claim, but it cannot expose a partial READY
watchlist.

## Immutability rules

SQLite triggers enforce the application contract:

- a new header must start as `GENERATING`;
- header identity fields can never change;
- a header whose status is no longer `GENERATING` cannot be updated;
- headers cannot be deleted;
- members can be inserted only while their header is `GENERATING`;
- members cannot be updated or deleted.

Previous READY records are preserved as immutable versions. The normal
application provides no update, delete or overwrite method.

## Top-N policy

Hard eligibility and display remain separate:

- all hard-eligible members are stored with contiguous eligible ranks;
- default display is Top 20;
- Top-N is configurable and part of the identity;
- changing Top-N creates a separate identity but does not change member scores
  or the hard-eligible universe.

Tests generated Top 5 and Top 10 identities from the same histories and proved
that all 25 synthetic eligible scores were byte-for-byte equal.

## Service API and typed states

The dedicated service exposes:

- `prepare_for_session(...)`;
- `get_for_session(...)`;
- `get_latest_ready()`;
- `rebuild_for_research(...)`;
- `compare_watchlists(...)`.

Presentation calls only `get_for_session`. Generation is reachable only
through explicit preparation or an authorized research rebuild.

Fail-closed states are:

- `WATCHLIST_READY`;
- `WATCHLIST_NOT_GENERATED`;
- `INSUFFICIENT_DAILY_HISTORY`;
- `SOURCE_UNAVAILABLE`;
- `PROVENANCE_REJECTED`;
- `CONFIG_VERSION_MISMATCH`;
- `SOURCE_FINGERPRINT_CHANGED`;
- `GENERATION_FAILED`;
- `NO_ELIGIBLE_SYMBOLS`.

Scores are never replaced by zero. There is no fallback to legacy ERS, Range
Scanner, Yahoo, Rubix movers or current-session percentage change.

Stored records are revalidated on read for:

- schema and 60/30 lookback compatibility;
- metric/config version;
- provider;
- target-session cutoff;
- header fingerprints and verification status;
- member provider, cutoff, versions and fingerprints.

## Dashboard behavior

The panel appears before the live scan gate under:

```text
HISTORICAL SCALPING WATCHLIST
قائمة السكالبنج التاريخية الثابتة
```

It displays:

- target session and cutoff;
- provider and selector versions;
- generated time and watchlist ID;
- eligible and displayed counts;
- 60/30 windows;
- fingerprint status;
- frozen/immutable status;
- the Top-N table;
- a sortable complete eligible universe;
- the prior/current stored-watchlist comparison.

The historical table contains no current price or current-change field. It
states that the list uses completed EODHD Daily history, may include official
closing-auction effects and has no ready intraday historical enrichment.

`Load frozen watchlist` is read-only. Rendering, rerunning, navigation and
browser refresh never call the preparation method.

`Research Rebuild` is inside a warning expander, displays the proposed session
and cutoff, requires an explicit confirmation checkbox, and remains disabled
without confirmation. An unchanged identity returns the existing READY record.

The READY watchlist ID is mirrored in Streamlit session state for workspace
continuity, but SQLite identity is authoritative. A fresh browser or second
observer obtains the same ID from storage.

## Stored comparison

The read-only comparison reports:

- previous/current target sessions and cutoffs;
- displayed-candidate overlap;
- additions and removals;
- historical rank changes;
- hard-eligible count change;
- Top-N turnover;
- metric/config version changes.

Top-N turnover is `1 - overlap / max(previous size, current size)`. The UI
explicitly states that additions and removals are list comparisons, not trade
signals.

## Provenance controls

Before claiming an identity, every nonempty frame must pass the accepted EODHD
provenance check. Yahoo, Rubix, unknown and missing-provider frames reject the
entire generation as `PROVENANCE_REJECTED`.

Every eligible member must have:

- `source == EODHD_DAILY`;
- an individual `sha256:` source fingerprint;
- matching header/member cutoff;
- matching metric/config versions;
- 60-session primary and 30-session recent profiles.

Missing eligible-member fingerprints fail the generation before publication.
The nine Phase 2B unresolved-volume histories remain hard excluded.

## No-lookahead and live invariance evidence

Tests prove:

- D-1 is the maximum input for D;
- changing D OHLCV cannot change D's fingerprint, ID, members or order;
- D changes the identity only when preparing D+1;
- an incomplete latest bar is excluded;
- a missing latest bar is disclosed through `latest_session`, never
  fabricated;
- a stale cache fails without READY members;
- weekend and holiday resolution use the previous real EGX session;
- Cairo midnight, not host-local midnight, controls the implicit date;
- adding arbitrary Rubix current price, change, volume, RVOL and spread
  metadata changes neither identity nor order.

The live mutation test also verifies that no stored column name contains a
current or Rubix field.

## Multi-observer and refresh evidence

One generated database was opened by three independent service instances
representing:

- the original page;
- browser refresh/navigation return;
- a second observer.

All returned the same watchlist ID without calling the selector again.

The concurrent-generation test pauses the first builder after its database
claim. A second service call returns the existing `GENERATING` identity and
the builder call count remains one.

## Controlled real-data validation

The approved 225-symbol EODHD cache was read with live calls impossible.

| Field | Result |
|---|---|
| Target session | 2026-07-28 |
| Historical cutoff | 2026-07-27 |
| Provider | `EODHD_DAILY` |
| Metric version | `DAILY_HISTORICAL_SELECTION_V2` |
| Config version | `DAILY_HISTORICAL_SELECTION_CONFIG_V2` |
| Universe | 225 |
| Cache-only unavailable | 3: DEIN, MEGM, TRTO |
| Hard eligible | **179** |
| Displayed | **20** |
| Watchlist ID | `FHW-384cf17b13194e43370bfabc` |
| Source fingerprint | `sha256:966eb87649cbbbb32447132233f89bcec3591a275bf00e148a06a04acf50aa4f` |
| Universe fingerprint | `sha256:27f5c820d06cf1f0ad8c42f99e5265ddaa69fa57b85f457fd8d93f4f7113d5fa` |
| SQLite integrity | `ok` |
| Journal / foreign keys | WAL / enabled |
| Reference list match | true |
| Refresh same ID | true |
| Second observer same ID | true |
| D + live mutation same ID/order | true |
| Independent database same ID/scores | true |

The reproduced Top 20 is:

1. RAYA
2. ACAMD
3. EGTS
4. CCRS
5. ZMID
6. OCDI
7. PRCL
8. AREH
9. EGCH
10. CCAP
11. VALU
12. CRST
13. RREI
14. MASR
15. NIPH
16. AMER
17. PHDC
18. EEII
19. GBCO
20. POUL

The cutoff and config match Phase 2B, so no cutoff-difference list is needed.
The result is calculated, not hard-coded; the reference tuple existed only in
the temporary validation assertion. Both temporary real-data databases were
removed after capturing this evidence.

## Automated validation

| Suite | Result |
|---|---:|
| New frozen-watchlist lifecycle/UI tests | **36 passed** |
| Frozen watchlist + daily selector/Zone tests | **98 passed** |
| Scalping UI, EODHD/provider, paper/production safety | **265 passed, 7 skipped** |
| Complete repository suite | **1486 passed, 7 skipped in 78.08s** |
| Streamlit spare-port smoke | health 200; root 200 |
| Final branch `git diff --check` | passed |

The seven full-suite skips are unchanged, environmental real-cache
prerequisites for COMI, EAST, SWDY, KZPC, UNIP and ORAS volume correction plus
the KZPC event-window case. They predate this branch and are not selector or
watchlist skips.

The Streamlit smoke:

- used port 8766;
- redirected watchlist storage to an operating-system temporary path;
- blanked EODHD credentials;
- returned HTTP 200 from `/_stcore/health` and `/`;
- terminated only its verified PIDs;
- left the existing port 8501 listener untouched.

Full-suite generated cache/database/launcher fixtures in the feature worktree
were removed. No decision-support, paper, forward-testing, report, archive,
production or broker store was modified by Phase 3A.

## Data safety

The two pre-existing main-worktree modifications remain uncommitted and retain
their accepted hashes:

| File | Git blob | SHA-256 |
|---|---|---|
| `data/paper_trades.csv` | `323bc8b6c8547720d30e38ba3cab024ad55bb080` | `09d3b7f66f6e32a8bc7dd9fc0e21007899388cec2ab57976b3de38c90ddfa68d` |
| `docs/audits/strategies/SCALPING_MULTI_SESSION_VERDICT.md` | `640d9c0e98154d75e983916d6b04480e7cdc2b3d` | `37b4b58231937ce69da3360c1f8e1d03b784389f41fbd3630fc6b677b80a7eed` |

## Remaining risks

- There is no production scheduler. Session preparation is explicit.
- A process kill can leave a non-READY `GENERATING` claim. It remains safely
  invisible as READY, but automated abandoned-claim recovery is deferred.
- SQLite coordinates observers sharing this filesystem. It is not a
  distributed multi-host database.
- The default database is initialized when the panel first reads it; no real
  watchlist is generated until an authorized action.
- The controlled real-data watchlist was deliberately removed rather than
  seeding the main operational path.
- Three strict-universe symbols lacked cache-only EOD documents in the
  validation run.
- Daily candles may contain closing-auction effects and cannot establish
  intraday path or first touch.
- Intraday historical enrichment and Live Entry Readiness remain unavailable.
- Top-N membership can legitimately change at the ranking boundary when a new
  completed session or approved source/config version changes.

## Boundary for future Live Entry Readiness

A future phase may consume the immutable historical candidates as an input,
then calculate separately typed Rubix live readiness. It must not update:

- historical score or rank;
- candidate membership or order;
- historical explanation;
- stored source identity.

Any future live state must live in a separate table/service and be joined for
display only. Phase 3A stops before that boundary.
