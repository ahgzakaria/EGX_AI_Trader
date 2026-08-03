# ORB + First Pullback — Exact Implementation Plan

Status: Phase 1 plan. Major implementation must wait for review.

## Phase 2A reviewed data-foundation decision

Phase 2A implements only typed configuration, Cairo session phases, Rubix
normalization, cumulative-volume quality, completed 1m/5m bars, the frozen
opening range, capability reporting, isolated research persistence and Shadow
collection. The audited constraints now carried into Phase 2B are:

- true trade-level VWAP remains `TRUE_VWAP_UNAVAILABLE`; the optional research
  calculation is named only `BAR_WEIGHTED_TYPICAL_PRICE_PROXY` and is never a
  VWAP field or mandatory gate;
- time-of-day RVOL is deferred while the local store has fewer than the
  configured quality-approved history sessions; Phase 2B receives
  `TIME_OF_DAY_RVOL_INSUFFICIENT_HISTORY` rather than a daily-volume proxy;
- OR High and the frozen breakout zone are the initial primary structural
  references for Phase 2B reclaim logic;
- Phase 2B must remain functional without true VWAP, turnover, trade count or
  true time-of-day RVOL and must not substitute fabricated values;
- no momentum, anti-chase, pullback, entry, sizing, trade management, alert or
  dashboard decision belongs to Phase 2A.

## Delivery strategy

Keep the work on `feat/scalping-opening-range-first-pullback` in the isolated worktree. Use small review gates by phase; do not push and do not enable broker/production execution.

The new domain is isolated under `scalping_orb/`. Existing Stable Range-Bound and Daily Uptrend Pullback backends remain unchanged. Their UI role changes only in Phase 4.

## Proposed typed configuration

Create one frozen `OrbFirstPullbackConfig` plus a versioned JSON setting, loaded and validated in one place. No strategy module may define a numeric threshold independently.

### Identity, mode and session

| Field | Type | Proposed initial value/status |
|---|---|---|
| `enabled` | bool | `false` until explicitly enabled for Shadow/Paper |
| `mode` | enum | `SHADOW` or `PAPER`; never production |
| `timezone` | str | `Africa/Cairo` |
| `continuous_open` | time | `10:00` |
| `continuous_close` | time | `14:15` |
| `auction_end` | time | `14:25` |
| `opening_range_minutes` | int | `15` |
| `bar_intervals_minutes` | tuple[int] | `(1, 5)` |
| `earliest_entry_time` | time | not before a valid post-OR completed confirmation |
| `latest_entry_time` | time | research setting; choose explicitly between suggested `13:15` and `13:30` during Phase 2 review |

The engine derives intervals from these fields. It does not copy times into other modules.

### Pre-session context

Fields: minimum completed EODHD history, maximum D-1 lag, median daily volume/turnover, minimum ATR/tradable movement, price bounds, minimum resistance upside, required verified Rubix mapping, optional EMA20/EMA50 context, optional EGX30/sector relative strength capability, candidate soft maximum for presentation.

Threshold values not explicitly supplied by the task remain `None`/review-required in Phase 1. Phase 2 must use documented existing liquidity conventions only where semantically identical; otherwise tests set synthetic values. Candidate count is never forced.

### Live data quality

Fields: quote maximum age, maximum receive/market lag, maximum spread percent, minimum live cumulative volume/turnover when supported, minimum observed updates, abnormal-gap rule, duplicate-event policy, required 1m slots per 5m bar, collector health policy, bid/ask requirement, turnover/trade-count capability policy.

Defaults are fail closed for a configured required field. Missing bid/ask returns `SPREAD_UNAVAILABLE`; it is never substituted with last price.

### Volume and VWAP capability

| Field | Proposed default |
|---|---|
| `minimum_rvol_history_sessions` | `20` quality-approved sessions per symbol/time bucket |
| `volume_evidence_mode` | `TRUE_TIME_OF_DAY_RVOL_ONLY` |
| `allow_provisional_volume_proxy` | `false` pending explicit approval |
| `require_true_vwap_for_momentum` | capability-aware; do not enable while inputs unavailable |
| `allow_ohlcv_weighted_price_proxy` | `false` pending explicit approval |

The state result always includes capability enum, sample count, numerator cutoff and baseline fingerprint.

### Momentum, anti-chase and pullback

Fields: breakout buffer, required completed 5m closes, intraday ATR lookback/minimum bars, maximum breakout candle range ATR, maximum distance above OR high, maximum distance above supported true VWAP, maximum stop distance, minimum resistance room, pullback minimum/maximum depth percent and ATR, maximum pullback bars, selling-volume comparison rule, OR/VWAP/EMA tolerance, first-pullback-only flag, confirmation rule set and trigger expiry bars.

No numeric defaults are asserted in Phase 1 because they require EGX intraday research. Phase 2 synthetic tests pass explicit config values. A reviewed baseline config may then be added as a clearly versioned research hypothesis, not a proven threshold.

### Risk and management

| Field | Proposed initial value/status |
|---|---|
| `risk_per_trade_percent` | `0.25` |
| `max_concurrent_orb_trades` | choose `1` initially; `2` remains a research variant |
| `max_daily_losses` | `2` |
| `max_daily_realized_loss_percent` | configurable, reviewed before enablement |
| `portfolio_heat_percent` | configurable; reuse existing account-risk concept |
| `max_exposure_per_symbol_percent` | configurable |
| `intraday_atr_stop_buffer` | research setting |
| `minimum_risk_reward` | research setting |
| `partial_exit_at_r` | candidate `1.0R`, configurable |
| `partial_exit_fraction` | configurable |
| `target_2_r` | candidate `2.0R`, configurable |
| `time_stop_completed_5m_bars` | candidate 3–4; choose explicitly during review |
| `trail_mode` | enum: completed 5m higher lows or supported true VWAP |
| `no_averaging_down`, `no_stop_widening` | always `true`, not user-disableable |

Account capital/equity is runtime input, never a strategy constant.

## Phase 2 — Core engine and synthetic tests

### Files to create

```text
scalping_orb/__init__.py
scalping_orb/config.py
scalping_orb/models.py
scalping_orb/session.py
scalping_orb/data_quality.py
scalping_orb/bars.py
scalping_orb/pre_session.py
scalping_orb/opening_range.py
scalping_orb/volume_evidence.py
scalping_orb/momentum.py
scalping_orb/first_pullback.py
scalping_orb/state_machine.py
scalping_orb/risk.py
scalping_orb/trade_management.py
scalping_orb/service.py
config/orb_first_pullback.json

tests/test_orb_session.py
tests/test_orb_bars.py
tests/test_orb_opening_range.py
tests/test_orb_momentum.py
tests/test_orb_first_pullback.py
tests/test_orb_state_machine.py
tests/test_orb_risk.py
tests/test_orb_trade_management.py
tests/test_orb_data_quality.py
tests/fixtures/orb_first_pullback/...
```

### Existing files that may change in Phase 2

- `core/egx_session.py` only if a small public continuous/auction boundary API is needed; do not duplicate or change Swing/Daily semantics.
- `providers/rubix_sqlite_provider.py` only if a read-only, typed batch/cursor method cannot be composed externally; no schema/write changes.
- `config/settings_manager.py` only to expose the isolated ORB config path, without changing current scalping settings.

Preferred outcome is zero changes to old strategy modules, dashboards, databases, and provider policy in Phase 2.

### Build order

1. Define enums/dataclasses/config validation and identity/fingerprint contracts.
2. Wrap existing Cairo boundaries in an ORB session policy and prove auction exclusion.
3. Implement deterministic 1m ingestion and 5m aggregation with completion/quality states.
4. Implement frozen 15-minute opening range.
5. Implement capability-aware volume/VWAP evidence: unavailable is a first-class result.
6. Implement completed-5m momentum and anti-chase assessments.
7. Implement first-pullback identity and reclaim confirmation.
8. Compose the pure deterministic state machine.
9. Implement structural stop, risk-by-distance sizing and paper trade-management model.
10. Run focused and mandatory regressions before requesting Phase 3 review.

Phase 2 does not create the forward-test database, change the main dashboard, start Rubix, or claim historical performance.

## Phase 3 — Shadow collection and forward testing

Create the isolated repository/migrations specified in `ORB_FIRST_PULLBACK_DATABASE_SCHEMA.md`, plus a scheduler/CLI that reads Rubix in read-only mode and writes only the ORB research database.

Proposed files:

```text
scalping_orb/repository.py
scalping_orb/migrations/001_initial.sql (or equivalent versioned Python migration)
scalping_orb/shadow_monitor.py
scalping_orb/session_review.py
scripts/run_orb_first_pullback_shadow.py
scripts/run_orb_session_finalizer.py
tests/test_orb_repository.py
tests/test_orb_shadow_restart.py
tests/test_orb_session_review.py
```

Requirements: restart-safe cursor, idempotent transitions/signals, immutable evidence, explicit data-quality samples, no broker API, no writes to Rubix/old databases, and `INSUFFICIENT_INTRADAY_HISTORY` until per-symbol quality gates pass.

Stop for review after multiple dry-run/replay fixtures and at least one Shadow session validation.

## Phase 4 — Dashboard

Implement the wireframe after engine/repository contracts stabilize.

Likely files:

```text
dashboard/scalping.py
dashboard/orb_first_pullback.py (new presentation component)
app.py (only if route labeling/composition requires it)
tests/test_orb_scalping_ui.py
tests/test_clean_scalping_ui.py
tests/test_app_navigation.py
```

Move old selector views under Secondary Research Filters in presentation only. Remove their primary entry-readiness role from the ORB dashboard, while leaving their backends/results intact. Verify no raw enums, no BUY/production badges, current-session refresh, auction/no-data states and exact missing requirements.

Stop for visual review with synthetic/read-only session fixtures; do not start Rubix or connect to a broker.

## Phase 5 — Calibration and evaluation

Start only when data readiness reports enough quality-approved timestamped sessions. Never use daily OHLC.

Evaluator requirements:

- next valid executable price, bid/ask/spread and configured slippage/fees;
- strict chronological event processing;
- stop/target first-touch ordering and conservative same-bar ambiguity;
- partial fills/exits, time stops and trails;
- no returns after exit;
- chronological equity, portfolio heat and drawdown;
- reports by symbol, month, OR width, TOD-RVOL bucket, spread bucket, entry time, breakout extension and market regime;
- train/calibration/test separation or forward-only evaluation appropriate to sample size.

No profitability claim is emitted while validation remains `INSUFFICIENT_INTRADAY_HISTORY`.

## Complete test plan

### Session

- aware UTC/Cairo conversion and DST-safe zone behavior;
- continuous `10:00–14:15`, auction `14:15–14:25` separation;
- OR `[10:00,10:15)` completion and no entry before completion;
- configured late cutoff and no auction entry;
- no auction tick/bar contamination.

### Bars

- ordered aggregation, duplicate identical event dedupe and conflicting duplicate rejection;
- 1m/5m half-open alignment;
- partial bar unavailable for confirmation;
- stale quote, missing volume, missing minute slot and session reset;
- no future row read and no cross-symbol quote inheritance.

### Opening range

- high/low/mid/width/volume correctness;
- no finalize before all fifteen completed slots;
- narrow/wide quality states;
- missing/invalid input exact reason.

### Breakout/momentum

- completed 5m close above OR high;
- wick-only and incomplete-bar rejection;
- overextended candle, daily resistance too close and poor RR;
- stale/spread/volume capability gates;
- first breakout never entry-ready.

### First pullback/confirmation

- first controlled pullback to OR high and supported true VWAP;
- reduced versus expanding sell volume with unavailable capability path;
- excessive depth, lower-low/structural failure;
- second pullback rejected;
- OR/VWAP reclaim, prior-bar-high break and later rejection-high break;
- incomplete confirmation and no future leakage.

### Risk/management

- structural stop with ATR buffer and maximum-width rejection;
- exact cash-risk-by-stop-distance quantity with exposure/liquidity/heat caps;
- 0.25% configured risk, max concurrent trades, two-loss and daily-loss gates;
- no averaging down/no stop widening;
- 1R partial, 2R/resistance target, stop-first ambiguity, time stop, completed-bar trail and no post-exit returns.

### Data quality/provider

- verified/unmapped Rubix symbols and no suffix guessing;
- missing/invalid bid/ask produces `SPREAD_UNAVAILABLE`;
- true VWAP unavailable without verified inputs;
- TOD RVOL sample count and `INSUFFICIENT_INTRADAY_HISTORY`;
- provisional proxy disabled/clearly labelled if later approved;
- stale/disconnected collector and timestamp/sequence regressions;
- Rubix opened read-only and no Yahoo call/fallback/comparison.

### Persistence and UI

- migrations, WAL, foreign keys, dedupe/restart, append-only triggers and old DB hash preservation;
- localized states/no raw enums, dynamic session/symbol identity, no stale cache;
- old selectors secondary only, Production Disabled and Research/Paper permanent;
- missing values not rendered as zero;
- visual distinction of continuous/auction/partial/stale data.

### Regressions

Run new ORB, Rubix, session, scalping dashboard, paper-trading and portfolio-risk groups, then the full suite and `git diff --check`. Explicitly include AI Analysis, Swing/Daily, breakout/breakdown, Pullback Health, Stable Range-Bound backend, EMA5/EMA10 backend, EODHD 241 universe and infographic regressions.

## Review gates

| Gate | Must be true before proceeding |
|---|---|
| Phase 2 | Pure engine deterministic; all synthetic tests pass; no old strategy/UI/DB change |
| Phase 3 | New DB migrations safe; Rubix read-only; restart/dedupe proven; Shadow only |
| Phase 4 | Seven-tab pipeline visually approved; old selectors secondary; current-state cache safe |
| Phase 5 | Sufficient quality-approved intraday samples and frozen evaluation protocol |

## Complexity estimate

- Phase 2 core engine: **High** — roughly 12–18 focused engineering days, driven by bar completeness, capability states, first-pullback identity and structural risk invariants.
- Phase 3 persistence/Shadow: **High** — roughly 8–12 days plus elapsed live-session observation time.
- Phase 4 dashboard: **Medium–High** — roughly 6–10 days plus Arabic/visual review.
- Phase 5 calibration: **High and data-bound** — implementation 8–15 days after sufficient history; calendar time cannot be compressed without valid sessions.

Overall: high-complexity, multi-phase work. The dominant risk is data sufficiency/semantics, not indicator coding.

## Phase 1 completion boundary

This plan creates no strategy code, no database, no migration, no UI change, no broker path and no production setting. Implementation must stop here pending review.

## Phase 2A gate-closure contract

Phase 2A now separates historical reconstruction from live operational
readiness. A verified market timestamp always controls the exchange minute;
receive time controls delivery lag. Delayed evidence may reconstruct historical
bars but receives `LIVE_FRESHNESS_FAILED` and cannot populate a current quote
capability. Unverified or negative-lag market time is excluded from bars.

The zero-second out-of-order tolerance is retained deliberately for live input:
late arrival is rejected and audited. Historical replay sorts by market time and
labels an arrival correction `LATE_CORRECTION_ONLY`; it can create a versioned
research correction but never retroactively claims real-time knowledge.

Phase 2B core may consume frozen Opening Range version identifiers and honest
capabilities only. Live decisions, paper actions, alerts and dashboard signals
remain disabled. Additional sessions and improved receive-lag evidence are
collection gates, not inferred or fabricated inputs.
