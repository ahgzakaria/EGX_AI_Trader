# Phase 2B Core — Implementation Audit

Written before any Phase 2B code, from the merged Phase 2A runtime package and the
Phase 1 contracts. Scope is the deterministic research engine only: breakout,
anti-chase, first pullback, reclaim, structural stop, targets and typed state
transitions, terminating at `ENTRY_READY_RESEARCH`.

Base HEAD `5d202e2`. Phase 2A verdict `APPROVED_FOR_PHASE2B_CORE`.

---

## 1. Blocking constraint found in the audit

**`OrbDataConfig.fingerprint` is the session identity.** `shadow.py:84` derives
`session_id` as `sha256("ORB_PHASE2A|<date>|<config.fingerprint>")`, and
`ensure_session` stores that fingerprint as `config_hash` under
`UNIQUE(session_date, config_hash)`.

Adding Phase 2B fields to `OrbDataConfig` would therefore change every existing
`session_id`, orphaning all persisted Phase 2A events, bars, opening ranges and
capabilities, and silently breaking replay idempotency — the property Phase 2A
was gated on.

**Decision.** Phase 2B settings live in a **separate frozen `OrbStrategyConfig`**
that *composes* an `OrbDataConfig` rather than extending it. Session identity
stays keyed on the Phase 2A fingerprint; Phase 2B rows are additionally keyed on
a distinct `strategy_fingerprint`. This satisfies "extend the typed ORB
configuration" at the configuration *surface* without mutating the Phase 2A
identity contract.

Time settings that already exist on `OrbDataConfig` — `continuous_end`
(= auction start) and `auction_end` — are **referenced, not duplicated**. Only
`earliest_breakout_time`, `latest_research_entry_time` and `expiry_time` are new.

---

## 2. Modules to reuse unchanged

| Module | Reused for | Not touched |
|---|---|---|
| `config.py` | session clock, quality thresholds, `opening_range_minutes`, fingerprint | no new fields — see §1 |
| `session.py` | `OrbSessionClassifier`, `CONTINUOUS_PHASES`, `require_aware` | all phase logic |
| `events.py` | `NormalizedIntradayEvent`, `UniverseMembershipStatus`, `MarketTimeStatus`, `HistoricalReplayStatus`, `LiveFreshnessStatus`, `NormalizationMode` | normalizer, dedup, volume tracker |
| `bars.py` | `CompletedBar` is the engine's only price input | aggregation untouched |
| `opening_range.py` | `OpeningRangeResult`, `OpeningRangeStatus` | freezing/revision logic |
| `capabilities.py` | `PriceReferenceStatus`, `HistoricalBarCapability`, `LiveDecisionCapability` | assessment untouched |
| `replay.py` | `RubixReadOnlyReplaySource` for the historical replay run | read-only source untouched |

The engine consumes `CompletedBar` only. It never re-derives a bar, never reads
an event directly, and never re-computes an opening range.

## 3. Modules to extend

| Module | Extension | Risk control |
|---|---|---|
| `repository.py` | migration **4** (Phase 2A is already at 3), six new `orb_*` tables, typed insert/load methods | additive `ALTER`/`CREATE` only; no Phase 2A table altered |
| `shadow.py` | untouched — a **separate** service class is added instead | avoids destabilising ingestion |

## 4. New modules required

| Module | Responsibility |
|---|---|
| `scalping_orb/states.py` | `OrbResearchState`, Arabic labels, rule codes, rejection reasons, legal transition map |
| `scalping_orb/indicators.py` | pure ATR/EMA over completed bars, warm-up and unavailability states |
| `scalping_orb/strategy_config.py` | `OrbStrategyConfig` — every Phase 2B threshold, validated, defaulted, serialisable |
| `scalping_orb/engine.py` | the pure deterministic engine: typed context in, typed evaluation out |
| `scalping_orb/strategy_service.py` | repository-facing boundary: load bars, call the engine, persist, replay |

**Why new ATR/EMA rather than reuse.** The existing helpers (`_atr_wilder`,
`_ema` in `core/ai_analysis_evidence.py`) are private, pandas-backed and coupled
to the daily AI-analysis pipeline. Importing them would couple a pure intraday
engine to that stack and to pandas. The engine implements small pure functions
over `CompletedBar` tuples instead. Daily ATR remains separate D-1 context and is
never substituted for intraday ATR.

## 5. Input contract

```
ORBStrategyContext
  canonical_ticker, session_date
  universe_membership_status, operationally_eligible
  opening_range: OpeningRangeSnapshot(revision, high, low, frozen_at_utc,
                                      source_identity, status)
  opening_range_version_mode: DECISION_TIME_ORIGINAL_VERSION | LATEST_RESEARCH_REVISION
  daily: DailyContext(available, previous_close, atr14, resistance_levels)
  market_time_status, historical_replay_status, live_freshness_status
  volume_capability, live_decision_capability
  evaluation_mode: HISTORICAL_REPLAY | SHADOW_LIVE | LIVE_DISABLED
  data_config_fingerprint, strategy_fingerprint, engine_version

CompletedBarSequence
  one_minute: tuple[CompletedBar, ...]
  five_minute: tuple[CompletedBar, ...]
  as_of_utc
```

The engine reads no global state, opens no database, and performs no I/O. D-1
context is supplied by the caller — it is never fetched inside the engine, so no
provider is reachable from engine code.

## 6. Output contract

```
ORBResearchEvaluation
  final_state, terminal
  transitions: tuple[StateTransition, ...]
  breakout: BreakoutAssessment | None
  pullback: PullbackAssessment | None
  reclaim: ReclaimAssessment | None
  risk: StructuralRiskProposal | None
  targets: TargetProjection | None
  rejection_reasons: tuple[RejectionReason, ...]
  evidence_fingerprint, evaluation_identity
```

Each `StateTransition` carries transition id, ticker, session date, OR version,
prior state, new state, exchange timestamp, as-of timestamp, rule code, evidence,
frozen price references, data-quality status, historical/live capability, config
identity and engine version — as the state-machine contract requires.

## 7. Unavailable data — must stay unavailable

| Metric | Handling |
|---|---|
| True VWAP | `TRUE_VWAP_UNAVAILABLE`; never required, never derived |
| Turnover, trade count | absent from the Rubix schema; never fabricated |
| Time-of-day RVOL | `TIME_OF_DAY_RVOL_INSUFFICIENT_HISTORY`; not a gate |
| Bar-weighted typical-price proxy | Research Only context; never named VWAP; **never a gate and never controls a transition** in the default model |
| Intraday ATR before warm-up | `ATR_UNAVAILABLE`; percentage gates only where config permits; daily ATR never silently substituted |
| Bar volume | `VOLUME_UNAVAILABLE`; no zero fabricated; price-only mode must be explicitly enabled |

## 8. State-transition ownership

The **pure engine** owns every transition. The service layer may only persist
what the engine returned. No LLM, no provider and no repository participates in
a transition decision.

Terminal states admit no further transition:
`BREAKOUT_REJECTED_*` (per candidate), `PULLBACK_STRUCTURE_FAILED`,
`RECLAIM_FAILED`, `ENTRY_READY_RESEARCH`, `ENTRY_EXPIRED`, `AUCTION_PHASE`,
`DATA_UNAVAILABLE`, `FAILED`.

`ENTRY_READY_RESEARCH` is the maximum output. `TRADE_ACTIVE`, `PARTIAL_EXIT`,
`TRAILING` and `EXITED` are deliberately **not** implemented.

## 9. Persistence changes required

Migration **4**, additive only:

| Table | Purpose | Uniqueness |
|---|---|---|
| `orb_candidates` | one activation per ticker/session/OR-version/mode/config | `(session_id, ticker, or_revision, evaluation_mode, strategy_fingerprint)` |
| `orb_state_transitions` | full ordered history | `(candidate_id, transition_fingerprint)` |
| `orb_breakouts` | frozen breakout bar + zone | `(candidate_id, breakout_identity)` |
| `orb_pullbacks` | first-pullback measurement | `(candidate_id, pullback_ordinal)` |
| `orb_reclaims` | confirmation bar + rule | `(candidate_id, reclaim_identity)` |
| `orb_research_setups` | frozen trigger/stop/targets/RR | `(candidate_id)` |

No orders, executions, positions, trades, P&L or broker events. Replay must be
idempotent: a second identical replay inserts zero rows in all six tables.

## 10. Tests required

Synthetic matrix per the brief: breakout (11 cases), anti-chase (6), first
pullback (11), volume (6), reclaim (7), risk (9), state machine (7), time (6),
persistence (8), plus regression proof that Phase 2A, the dashboard, the other
strategies, AI analysis and the infographic are unchanged, with no Yahoo and no
execution code.

Every test uses deterministic synthetic bars with fixed Cairo timestamps. No
test starts Rubix, opens a provider, or reaches the network.

## 11. Deferred, with reasons

| Item | Why deferred |
|---|---|
| Position sizing, portfolio heat, daily-loss caps | explicitly out of Phase 2B Core |
| Dashboard / Streamlit surface | out of scope; no UI file is touched |
| Alerts, paper orders, execution | out of scope; `ENTRY_READY_RESEARCH` is terminal |
| Live shadow enablement | freshness evidence fails closed (165 s median lag, 58.9% over budget) |
| Threshold calibration | defaults are `INITIAL_RESEARCH_DEFAULTS`, explicitly not optimal |
