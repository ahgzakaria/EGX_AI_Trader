# Phase 2A → Phase 2B Final Gate

## Verdict

`APPROVED_FOR_PHASE2B_CORE`

The deterministic ORB data foundation is approved for construction of isolated
Phase 2B core logic. This is not approval to emit, display, paper-trade or act on
an ORB decision. It is not a performance or profitability finding.

## Why the core gate is approved

- A strict read-only Rubix source (`mode=ro`, `query_only=ON`) supplied 86,310
  real rows to the actual Phase 2A normalization, bar, Opening Range and
  persistence path.
- The source SHA-256 remained
  `bd597a8f8d9cc80249e7963ec113165ac8353126b73884de712df6ab7ae25ab3`.
- 37,723 unique events were persisted; 48,587 exact redeliveries were removed;
  1,316 same-timestamp distinct events were preserved; no market-identity hash
  collision was observed.
- A clean process restart and second identical replay inserted zero events and
  left event/bar/range row counts unchanged. Bars and Opening Range records were
  byte-equivalent.
- Against Rubix `candles_1m`, 2,927 minute bars were exact OHLC matches, 44
  differences were attributable to the documented event-set difference, 94
  vendor minutes lacked reconstructable quote evidence, and zero differences
  remained unexplained.
- The dense quote-derived session produced 168 ready active Opening Ranges versus
  162 vendor-candle references. The six quote-only results were traced to a
  non-positive or missing 10:00 vendor OHLC row while normalized quotes supplied
  a valid price bar; there were no vendor-only ready ranges.
- Valid exchange market time controls historical minute placement. Receive time
  controls delivery freshness. Delayed or unreliable evidence cannot populate a
  current quote capability, and all live decisions retain an explicit
  `LIVE_DECISION_DISABLED_*` state.
- Migration, rollback, read-only repository, retention, restart seeding,
  versioned Opening Range corrections, universe eligibility, empty reports and
  bounded dedup state have focused regression coverage.

## Binding blockers beyond core construction

The following remain binding and are deliberately not inferred away:

1. **Additional independent sessions are required.** The 162 vendor-candle
   ready ranges and 168 quote-derived ready ranges are concentrated on
   2026-08-02. This is insufficient for parameter selection, calibration or a
   performance claim.
2. **One real live-session Shadow observation is required.** Historical replay
   took 63.093 seconds and 800 research write transactions. No live collector was
   started, so scheduler latency and end-to-end live operational behavior remain
   unvalidated.
3. ~~**Infographic test portability remains blocked by the execution
   environment.**~~ **RESOLVED at finalization.** The failures were not a missing
   FWRY cache file: `analyze_symbol` returned `DATA_UNAVAILABLE` because
   `EODHD_API_TOKEN`/`EODHD_API_KEY` is unset in an isolated worktree, so the
   provider never reached any cache. The module-scoped fixture in
   `tests/test_ai_analysis_infographic.py` now injects a deterministic frame
   through the existing `analyze_symbol(history_loader=...)` seam
   (`tests/fixtures/analysis_history.py`), so the real Core still computes every
   indicator, level, scenario and pullback diagnostic with no credential, no
   network and no local cache. No assertion was weakened, skipped or xfailed.
   Verified with the copied FWRY cache files removed and no token configured.

These blockers mean Phase 2B must remain pure, isolated and decision-disabled.
No signal, alert, entry, sizing, trade-management, paper or production route is
approved. No threshold is calibrated by this gate.

## Safety boundary for Phase 2B

Phase 2B core may consume only typed, versioned Opening Range evidence and
explicit capabilities. It must preserve historical/live separation and must not
turn `HISTORICAL_REPLAY_ACCEPTED`, delayed evidence, unavailable volume, archived
membership or a research correction into contemporaneous knowledge. Phase 2B
must remain functional with true VWAP and time-of-day RVOL unavailable.

The machine-readable and tabular replay evidence remains generated under
`reports/audits/strategies/orb_first_pullback/phase2a_gate_closure/`. The complete
gate-by-gate status is recorded in `PHASE2A_GATE_TRACKER.md` there.

## Final validation record

| Validation | Result |
|---|---|
| Phase 2A + review + gate-closure focused tests | **122 passed, 0 failed** |
| Existing session/Rubix/scalping regressions | **160 passed, 0 failed** |
| Infographic regression file | **56 passed, 0 failed** — after the portable-fixture fix, with the copied FWRY cache removed and no EODHD token configured |
| Full suite | **2023 passed, 7 skipped, 0 failed** |
| Readiness audit rerun | **SUCCESS**, read-only; 13 sessions and 265 symbols |
| `git diff --check` | clean |
| Dashboard/old-strategy diff | none |

The full suite is now green in an isolated worktree with no credential, no
network and no machine-local provider cache, so the checkout is portable. The
core-construction verdict remains `APPROVED_FOR_PHASE2B_CORE`; blockers 1 and 2
remain binding.

## Repository and protected-data integrity

- Worktree HEAD remained
  `3d6240f7809602ebed0d7a13d79539f3c74377de`; no commit or push occurred.
- Main remained at the same HEAD and retained only its pre-existing working-tree
  entries: modified `data/paper_trades.csv`, modified
  `docs/audits/strategies/SCALPING_MULTI_SESSION_VERDICT.md`, and untracked
  `.codex/`.
- The Rubix main database SHA-256 remained
  `bd597a8f8d9cc80249e7963ec113165ac8353126b73884de712df6ab7ae25ab3`.
  An already-running production launcher (started before this review) kept the
  WAL/SHM handles open; this task neither started nor stopped it. Their sizes and
  last-write timestamps remained the reviewed values, while direct re-hashing
  was refused by the operating-system share lock.
- Protected paper-trades and scalping-verdict hashes remained respectively
  `e9e9620d37e973bb860d1bd42b9cd90d1bd74498334ba10fe165af5a054aaa48`
  and
  `6fc81b4356a850e9e5383affdd75c90029a4353fc547096a2ab4c163a64e0aa6`.
- No Rubix or production database write was made by Phase 2A validation. All
  replay writes targeted new ignored research databases. No Yahoo request,
  authentication material, full quote payload, Phase 2B strategy state,
  dashboard change, commit or push was produced.
