# EODHD EGX Capability, Finalization-Time & Data-Semantics Audit

**Status: BLOCKED at the entitlement gate — `API_KEY_MISSING`.** No EODHD API key
is configured anywhere (environment, `.env`, `config/settings.json` all empty), and
the prior [EODHD_PHASE1_REPORT.md](EODHD_PHASE1_REPORT.md) (2026-07-18) already found
the free account **exhausted** (20/20 calls, ~1-year history, none reaching the
250-bar minimum). Network to `eodhd.com` **is reachable** (HTTP 200), so the *only*
blocker is the key.

Every phase that measures real EODHD behaviour — entitlement detail, per-symbol
coverage, finalization publication time, revision stability, Rubix reconciliation,
volume semantics, historical quality, corporate actions and strategy impact —
requires authenticated live calls. **None of these were fabricated.** What could be
built without a key was built and tested; the rest is deferred with the exact
mechanism to complete it once a key exists.

## Absolute rules honored

No provider activated; Rubix latest-session finalization and the normalized cache
untouched; Expected Range / Swing-Daily / TP / SL / weights / percentiles
unchanged; the API key is never printed, logged, or placed in a surfaced error/URL;
no plan purchased or upgraded. All external-provider flags remain disabled.

## What was delivered (key-independent)

| Deliverable | State |
|---|---|
| [core/providers/eodhd_daily.py](../../../core/providers/eodhd_daily.py) | Isolated adapter: entitlement / EOD / dividends / splits / exchange-list / bulk endpoints, full failure taxonomy, token never exposed, rate-limit throttle. |
| [scripts/observe_eodhd_egx_finalization.py](../../../scripts/observe_eodhd_egx_finalization.py) | Restart-safe, rate-limited finalization observer; dedups poll rows by (symbol, poll-ts, response-hash); stores response hashes; refuses to fabricate (records `API_KEY_MISSING`). |
| [scripts/run_eodhd_capability_audit.py](../../../scripts/run_eodhd_capability_audit.py) | Runs the real Phase 1 probe, offline Phase 2 mapping, analytical Phase 10 cost model, emits all CSVs. |
| 9 report CSVs | `eodhd_symbol_coverage.csv` (mapping filled, live cols `PENDING_API_KEY`), `eodhd_cost_capacity.csv` (analytical), plus 7 schema/status files marked `API_KEY_MISSING`. |
| [tests/test_eodhd_daily.py](../../../tests/test_eodhd_daily.py) | 8 tests: failure taxonomy incl `API_KEY_MISSING`, HTTP-code classification, credential non-exposure, mapping, observer key-missing + dedup. |

## Phase 1 — entitlement probe (real)

`probe_summary` → `API_KEY_MISSING` (no network call attempted without a key).
Failure taxonomy implemented and tested: `API_KEY_MISSING`, `INVALID_KEY` (401),
`PLAN_NOT_ENTITLED` (402/403), `RATE_LIMITED` (429), `SYMBOL_UNSUPPORTED` (404),
`ENDPOINT_UNAVAILABLE` (other), `NETWORK_ERROR`, `MALFORMED_RESPONSE`.

## Phase 2 — symbol mapping coverage (offline, SYNTACTIC ONLY)

All **265 / 265** project symbols produce a syntactically valid EODHD `.EGX` code
(COMI.CA → COMI.EGX), **0 ambiguous**. **This is symbol-string mapping only — it does
NOT mean 265 symbols are supported, listed, or entitled on EODHD.** Whether each
`.EGX` code actually resolves to real data (lookup success, first/last date, row
count, currency, Volume/adjusted availability) is `PENDING_API_KEY` in
`reports/eodhd_symbol_coverage.csv` and can only be confirmed with a live entitled
key. Real supported coverage is currently **unknown**.

## Phase 10 — cost & capacity (analytical, from EODHD's documented API model)

| Item | Requests |
|---|--:|
| Initial full backfill (single-symbol EOD × 265) | 265 |
| Daily incremental via `eod-bulk-last-day/EGX` | **1** |
| Daily incremental single-symbol | 265 |
| Finalization polling / session (8-symbol basket × 24 polls) | 192 |
| Finalization polling / session via bulk | 24 |
| Corporate-action refresh (div+splits × 265) | 530 |
| **Estimated normal day (bulk update + bulk polling)** | **~25** |
| Free-tier daily allowance | 20 (exhausted) |
| Paid EOD-Historical daily allowance (documented) | 100,000 |

A normal day needs ~25 bulk calls; even a full backfill (265) trivially fits a paid
EOD plan but **exceeds the free tier**, which cannot do a single backfill.

## Deferred (require a configured key) — mechanism ready

- **Phase 3 finalization time / Phase 4 revisions**: run
  `observe_eodhd_egx_finalization.py` from ~14:30 Cairo across ≥3 live sessions; it
  records FIRST_VISIBLE / FIRST_COMPLETE_OHLCV / STABLE_FINAL and any revisions with
  response hashes. **Publication time cannot be answered from documentation** and is
  intentionally left unmeasured rather than guessed.
- **Phases 5–9** (reconciliation, volume semantics, historical quality, corporate
  actions, strategy impact): the adapter + Rubix bars are ready; these run once EODHD
  returns real history.

---

## Final questions

1. **How many project EGX symbols are supported?** **Unknown / unverified.** Only
   *syntactic* mapping is done — 265/265 produce a valid `.EGX` code, 0 ambiguous —
   which is **not** the same as being supported or entitled. Actual supported coverage
   requires a live entitled key (`API_KEY_MISSING`).
2. **Exact Cairo time today's EOD bar first appeared?** **Not measured** — requires
   the live observer across real sessions; not answerable from docs.
3. **When did it become stable?** Not measured (deferred to the observer).
4. **Was the first visible bar later revised?** Not measured (Phase 4 deferred).
5. **Does Close match Rubix AuctionLast?** Not measured — needs live EODHD closes vs
   the Rubix auction-confirmed official close (bridge already computes the latter).
6. **Does Volume match cumulative traded shares?** Not measured. Rubix volume is
   confirmed cumulative shares; EODHD's volume scale must be verified before any use.
7. **Is adjusted history safe for indicators?** Not measured — and by policy,
   adjusted **volume** must not feed liquidity ranking without an explicit validated
   policy.
8. **Is unadjusted history safe for liquidity ranking?** Not measured; unadjusted is
   the expected choice, pending confirmation of EODHD's volume semantics.
9. **How much do candidate rankings change?** Not measured — the A/B/C shadow
   (Yahoo / EODHD / hybrid) needs EODHD history.
10. **Is the current API plan sufficient?** **No key is configured**, and the prior
    free tier (20/day) is exhausted and cannot perform even one 265-symbol backfill.
    A paid EOD-Historical plan's **request capacity appears analytically sufficient**
    (~25 calls on a normal day vs a documented 100k/day limit) — but "sufficient
    capacity" is **not** "sufficient plan": EGX entitlement, provider symbol coverage,
    historical depth and Volume semantics remain **unverified** and must pass before
    any plan is deemed adequate.
11. **Which EODHD roles are approved?** **NOT_APPROVED** for all roles
    (`HISTORICAL_PRICE_BACKFILL` / `HISTORICAL_VOLUME` / `CORPORATE_ACTION_REFERENCE`
    / `DAILY_FALLBACK`) — approval requires live evidence that cannot be gathered
    without a key.
12. **Should Rubix remain the latest-session primary?** **Yes.** Rubix finalizes the
    completed session locally at ~14:35 Cairo from captured events; EODHD's EGX EOD
    publication time is later and currently unmeasured. Rubix is more timely, so it
    stays primary regardless of the EODHD outcome — EODHD is only ever a
    backfill/reference/fallback.
13. **Did any strategy or production setting change?** **No.** Only the isolated
    `core/providers/eodhd_daily.py`, the observer/audit scripts, honest CSVs and tests
    were added. TP/SL, weights, percentiles, Expected Range, Swing/Daily and the Rubix
    bridge are untouched; production and all external-provider flags stay disabled.
    387 tests pass.

## EODHD's role and current state

EODHD is needed **only** for: historical backfill, external reconciliation,
corporate-action research, and a potential future Yahoo historical replacement. It
is **not** needed for latest-session freshness — Rubix already finalizes the
completed session locally and removes the operational Yahoo freshness dependency for
EXPECTED_RANGE_SCALPER. All EODHD infrastructure is **ready but disabled**; it is not
in the provider chain and the finalization observer is **not scheduled** (an
unentitled key would only produce repeated `API_KEY_MISSING` records and noise).

## Required activation sequence (only when an entitled key is configured)

Set an entitled key in the `EODHD_API_TOKEN` environment variable — do **not** paste
it in chat; the adapter reads it from the environment and never exposes it. Then, in
order:

1. Run the capability audit manually first (`run_eodhd_capability_audit.py`).
2. Probe a representative **30-symbol basket** (coverage, depth, formats).
3. Verify EODHD **Volume** against Rubix cumulative traded shares.
4. Verify EODHD **Close** against the Rubix auction-confirmed official close.
5. Confirm **historical depth** and **corporate-action** behaviour.
6. **Only after 1–5 pass**, register `observe_eodhd_egx_finalization.py` (~14:30
   Cairo, Sun–Thu) for at least **3 live sessions** to measure real publication time.

Do not activate EODHD in the provider chain and do not alter any strategy or
production setting until this evidence is reviewed.
