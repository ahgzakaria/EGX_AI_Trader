# Phase 2B Core — Validation

Deterministic research engine for the ORB first-pullback model. Research only:
the maximum outcome is `ENTRY_READY_RESEARCH`. No order, execution, position,
paper trade, alert or dashboard surface exists in this phase.

Base HEAD `5d202e2`. No commit, no push, no merge.

---

## 1. Verdict

# `CORE LOGIC VALIDATED` / `MARKET PATH NOT YET OBSERVED`

# `APPROVED_FOR_PHASE2B_SHADOW_INTEGRATION`

Every condition in the approval rule is met (§6). **This is not a statement that
the strategy is profitable, live-ready, production-ready, or suitable for real
money.** It is narrower than it looks, and §4 states exactly how narrow.

The two headline statements are inseparable. The state machine, its guards and
its arithmetic are validated deterministically. The *market path* through that
machine — breakout, first pullback, reclaim — has never been observed in real
data, because no real completed 5-minute bar has ever closed above its own OR
High. Downstream states are validated **synthetically only**.

---

## 2. What was built

| Module | Role |
|---|---|
| `scalping_orb/states.py` | 24 typed states, Arabic/English labels, 22 rule codes, 40 rejection reasons, legal-transition graph |
| `scalping_orb/strategy_config.py` | every Phase 2B threshold, validated and fingerprinted, labelled `INITIAL_RESEARCH_DEFAULTS` |
| `scalping_orb/indicators.py` | pure intraday ATR/EMA over completed bars, with explicit warm-up and `ATR_UNAVAILABLE` |
| `scalping_orb/engine.py` | the pure deterministic engine — no I/O, no globals, no provider, no database |
| `scalping_orb/strategy_service.py` | repository boundary: load bars, call the engine, persist evidence |
| `scalping_orb/repository.py` | migration 4 and six additive `orb_*` research tables |
| `scripts/audits/replay_orb_phase2b_core.py` | read-only historical replay |

**Configuration is a separate object, not new fields on `OrbDataConfig`.**
`OrbDataConfig.fingerprint` seeds the Phase 2A `session_id`; extending it would
have changed every existing session identity and broken the replay idempotency
Phase 2A was gated on. `OrbStrategyConfig` composes it instead and carries its
own `strategy_fingerprint`. A test pins that the Phase 2A fingerprint is
unchanged.

## 3. Defects found and fixed during implementation

Four, all found by exercising the engine rather than by reading it.

| # | Defect | Fix |
|---|---|---|
| 1 | `effective_reward_risk` measured reward to the 1R target, so it was tautologically `1.0` and the `minimum_reward_risk` gate could never pass or discriminate | reward is measured to the furthest target still reachable before known D-1 resistance |
| 2 | The pullback's **starting** bar was never assessed — a pullback that opened straight through the floor looked healthy for one bar | the starting bar is assessed on its own bar |
| 3 | `maximum_pullback_bars` was a dead knob: the breakout zone spans everything up to the breakout close, so zone entry resolved every pullback on bar 1 | only a return to **OR High** — the primary structural reference — resolves the pullback; zone entry stays context |
| 4 | Structure was never re-checked while waiting for reclaim, so a decisive close under the zone was tolerated indefinitely | a completed close below the zone during `WAIT_RECLAIM` is a structural failure |

`minimum_initial_reward_risk` had also been declared but never consulted; it now
gates the breakout using the breakout bar's own low, and is skipped entirely
when no D-1 resistance is known rather than inventing one.

## 3b. Defects found and fixed during the final pre-commit review

Six more, all of the same family: **configuration or evidence that claimed a
control it did not have.** None loosened a threshold; the strict behaviour of
every gate is preserved exactly.

| # | Defect | Fix |
|---|---|---|
| 5 | The `BREAKOUT_TOO_EXTENDED` transition always recorded `extension_percent=…` as its evidence, even when the gate that actually fired was the ATR bar-range one. The stored reason contradicted the stored measurement | each anti-chase gate has its own reason (`EXTENSION_ABOVE_OR_HIGH_EXCEEDED`, `EXTENSION_ABOVE_OR_HIGH_ATR_EXCEEDED`, `BREAKOUT_BAR_RANGE_ATR_EXCEEDED`), carried on a new `BreakoutAssessment.extension_reasons` and persisted in `orb_breakouts.extension_reasons_json`; the transition records the firing gate **and** every measurement |
| 6 | `ReclaimRule.CLOSE_ABOVE_THEN_PREVIOUS_BAR_HIGH` and `REJECTION_BAR_HIGH_BREAK` did not implement their documented semantics — neither ever checked a break of a prior bar's high, and the first reduced algebraically to `close > OR High`, i.e. the default rule. Selecting either changed the recorded rule name without changing a single decision | both are declared-but-unimplemented; `OrbStrategyConfig` refuses them and the engine raises defensively. `IMPLEMENTED_RECLAIM_RULES` names the one rule that is real |
| 7 | `require_close_above_or_high`, `require_previous_bar_high_break` and `allow_rejection_candle_break` were inert: the engine never read them, and they duplicated the decision `reclaim_rule` already expresses | removed. `require_completed_bar` and `require_first_pullback_only` are now validated as non-disableable rather than left as switches that silently do nothing |
| 8 | The multi-bar breach gates inside the pullback assessment were **unreachable**. A pullback resolves on the first bar whose low reaches OR High, and any close below OR High implies such a low, so `closes_below` and `breaches` can never exceed 1. `maximum_structural_breach_bars` (then 1) and the closes-below gate could never fire | the counters remain as recorded evidence; `maximum_structural_breach_bars` now governs `WAIT_RECLAIM`, the only phase that can observe more than one breaching close. Its default is **0**, which reproduces the previous observable behaviour exactly — the first decisive close still fails |
| 9 | The pullback low-failure threshold applied `maximum_close_below_or_high_percent` twice — once building the zone floor, once again beneath it — so the wick allowance could not be tuned without moving the zone | the wick allowance is its own named threshold, `maximum_low_below_zone_lower_percent`, defaulted to reproduce the previous value |
| 10 | `evaluate_symbol` loaded **every** event in the session and filtered in Python, once per symbol — quadratic in symbol count, and the dominant cost of the 510 s replay | `load_events` takes an optional `canonical_ticker` and filters in SQL, served by the existing `(session_id, canonical_ticker, market_timestamp_utc)` index. Measured **25× faster** on a 60-symbol / 3,600-event session, with identical results (the ticker is normalized to upper case at ingestion, so the SQL predicate is exactly the previous Python one) |

Also removed: a dead `pullback_resolved` local, and a redundant first clause in
the `touched_or_high` test that the second clause already subsumed.

## 4. Historical replay — the honest result

Read-only over the real Rubix database (`mode=ro`, `query_only=ON`) into a
throwaway research database.

| Measure | Result |
|---|---|
| Sessions evaluated | 3 |
| Symbols evaluated | 230 |
| Normalized events reconstructed | 37,723 |
| Completed 1-minute bars | 3,193 |
| Completed 5-minute bars | **571** |
| Symbols with a READY opening range | 174 |
| **Breakout candidates** | **0** |
| Overextended breakouts | 0 |
| First pullbacks observed | 0 |
| Healthy pullbacks | 0 |
| Structural failures | 0 |
| Reclaim confirmations | 0 |
| `ENTRY_READY_RESEARCH` | **0** |
| Replay idempotent on a second identical pass | **yes** — all six tables unchanged |
| Replay duration | 510 s |

State distribution evaluated at 13:29 Cairo, before the auction cutoff so the
result is not auction-masked:

| Session | WAIT_BREAKOUT | DATA_UNAVAILABLE | FAILED |
|---|---:|---:|---:|
| 2026-07-14 | 0 | 2 | 1 |
| 2026-07-16 | 0 | 2 | 1 |
| 2026-08-02 | 168 | 56 | 0 |

Rejection reasons: `OPENING_RANGE_NOT_READY` 60, `ARCHIVED_SYMBOL_INELIGIBLE` 2,
`AUCTION_PHASE_REACHED` 168 (an artefact of evaluating at 14:15).

### Why zero breakouts, and what it means

This was checked directly rather than assumed: of the **504** five-minute bars
belonging to a READY opening range, **not one closes above its own OR High**.
Zero breakouts is therefore a property of the data, not an engine defect.

The cause is 5-minute bar scarcity. A completed 5-minute bar requires five
consecutive completed 1-minute bars, and the reconstructed feed yields a median
of about **3** five-minute bars per symbol against a possible 39 in the
10:15–14:15 window. The confirmation interval the strategy depends on barely
exists in the current data.

**Consequence, stated plainly: real data exercised only `WAIT_BREAKOUT`,
`DATA_UNAVAILABLE` and `FAILED`. Anti-chase, first-pullback, pullback quality,
reclaim, structural stop and reward/risk are validated by the synthetic matrix
only.** No threshold in `INITIAL_RESEARCH_DEFAULTS` has been calibrated against
observed market behaviour, and none should be treated as meaningful until it
has been. `MARKET PATH NOT YET OBSERVED` means exactly this.

Nothing was adjusted to manufacture a signal. Specifically, this phase did
**not** loosen a threshold, switch to wick breakouts, admit incomplete bars,
substitute vendor daily candles, or drop to 1-minute closes in place of the
configured 5-minute confirmation. Zero remains the honest count.

This directly reinforces the Phase 2A blocker: independent high-density sessions
are required, and the requirement is sharper than previously stated — density
must be sufficient to build *5-minute* bars, not merely 1-minute coverage.

### Source integrity note

The Rubix sha256 differs before and after the replay. The live production
collector was running throughout (it was started before this work and was
neither started nor stopped here), and it appends to that database continuously.
All Phase 2B access was `mode=ro` with `query_only=ON`; nothing in this phase
writes to Rubix. Every write went to
`data/research/orb_phase2b_core_replay.db`, which is gitignored.

No performance measure is reported. Win rate, expectancy, profit factor and
simulated return require an execution and evaluation phase that does not exist;
quoting them from a state machine would be a fabricated claim.

## 5. Test results

| Suite | Result |
|---|---|
| Phase 2B Core synthetic matrix | **131 passed** |
| Phase 2A suites (incl. review + gate closure) | **122 passed** |
| Infographic + card generator | **97 passed** |
| **Full suite** | **2154 passed, 7 skipped, 0 failed** |
| `git diff --check` | clean |

The 7 skips are pre-existing EODHD data-cache guards in
`tests/test_current_research_correctness.py`, untouched by this phase. No skip
was added.

The review added 21 tests to the matrix, each pinning a defect in §3b so it
cannot silently return: anti-chase gate identification and the exact percent
boundary; refusal of the two unimplemented reclaim rules; the breach counters as
evidence rather than gates, and the live `WAIT_RECLAIM` tolerance; the wick
allowance as an independent threshold; the exact `minimum_reward_risk` boundary
and the model's honest limit when no resistance is known; a real populated v3→v4
upgrade with row-count and `CREATE TABLE` equality, repeat-migration stability,
and atomic rollback under an injected failure; and a per-symbol event-load
regression.

## 6. Approval conditions

| Condition | Status |
|---|---|
| Synthetic tests green | 131 passed |
| Historical replay deterministic | idempotent across a second identical pass |
| No future leakage | a bar ending 1 µs after `as_of` is unusable (tested) |
| No incomplete bar advances state | tested for breakout and reclaim |
| State transitions reproducible | identical transition ids across runs |
| Persistence idempotent | all six tables unchanged on replay |
| Unavailable metrics remain unavailable | true VWAP, turnover, trade count, TOD RVOL |
| Live freshness fails closed | `LiveDecisionCapability` has **no** enabled member |
| No execution or dashboard code | asserted by source scan, code-only |
| Migration additive over populated v3 | row counts and `CREATE TABLE` text identical; rollback atomic |
| Full suite zero failures | 2154 passed, 0 failed |

## 7. Remaining blockers

1. **High-density sessions producing 5-minute bars.** Zero breakout candidates
   exist in the current data; the downstream engine is unvalidated against real
   market behaviour.
2. **One real live Shadow session.** Never run. Live remains disabled by
   construction.
3. **No threshold is calibrated.** Defaults are `INITIAL_RESEARCH_DEFAULTS`.
4. **No profitability evidence of any kind**, and none is claimed.
5. **`REPLAY_SESSION_BATCHING_REQUIRED`** — carried into the next phase.

   The measured 510 s covered 3 sessions. Its largest single cause, the
   per-symbol full-session event reload, is fixed (§3b #10, 25× on a
   60-symbol session) and a regression test now guards it. **This is not the
   whole blocker.** What remains is architectural and deliberately out of
   scope here: the opening-range lookup and bar aggregation still run once per
   symbol, and the idempotency pass re-evaluates every symbol a second time.
   Batching a session's symbols into a single load-and-aggregate pass is the
   next phase's work; correctness was not traded for speed in this one.

   `replay_seconds`, `event_loads_total` and `event_rows_read_total` are now
   reported per run, with `evaluate_seconds`, `event_loads` and
   `event_rows_read` per session, so the next attempt starts from a
   measurement rather than an estimate. `event_rows_read` growing as the square
   of the symbol count is the signature of a per-symbol reload returning.

## 8. Not started

Phase 2B Shadow integration has not begun. No dashboard file, alert, paper
order, position size, portfolio-heat or broker path was created or modified in
this phase.
