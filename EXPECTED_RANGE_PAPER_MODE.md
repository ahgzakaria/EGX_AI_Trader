# Expected Range Scalper — Safe Paper Forward Recording (ENABLED)

**Paper forward recording is now ON to collect unbiased forward evidence. It
records scenarios and outcomes only — it never places an order, never
auto-executes, and never enables production.**

## Safety state (final, required)

| Flag | Value |
|---|---|
| `paper_enabled` | **true** |
| `production_enabled` | **false** |
| `decision_support_only` | **true** |
| `automatic_execution` | **false** |
| `broker_orders_enabled` | **false** |
| Fixed target / stop | **+2% / -2%** (unchanged) |
| Range percentiles, score weights | **unchanged** |
| Strategy version | `ERS-1.1-paper` |

No frozen strategy (Swing/Daily, Classic, Breakout, Adaptive, AI, the fixed-2%
Scalping strategy, the Intraday Range Scalper, the Event-Driven Data Gate, the
Rubix collector) was changed.

## Range semantics (statistical references, not support/resistance)

Public band names now map onto the existing percentiles without changing any
percentile:

| Semantic | Band | Meaning | Historical full-session containment |
|---|---|---|--:|
| **CORE / MEDIAN EXCURSION** | base (p50) | central expected excursion | ~15% |
| **EXPANSION** | high-volatility (p75) | wider movement band | ~48% |
| **EXTREME** | max excursion (new additive reference) | high-volatility extension | — |

Because full-session containment is low, **a move above the Core/Expansion High is
NOT automatically a breakout** — scenario confirmation uses live price, Bid/Ask,
spread, activity and observable behaviour. (The `extreme` band is an additive
max-excursion reference; the configured p25/p50/p75 percentiles are untouched.)

## What gets recorded (unbiased — all states, not only READY)

- **Immutable pre-session snapshot** (`pre_session_snapshot.csv`): frozen ranks,
  liquidity/volatility metrics, Core/Expansion/Extreme ranges, hard-gate status,
  dataset hash. Written once; never recomputed from current-session data.
- **Every scenario state transition** (`scenario_states.csv` /
  `scenario_transitions.csv`): WAIT / READY / INVALID / NO_TRADE / DATA_STALE /
  DATA_INSUFFICIENT / SPREAD_TOO_WIDE / LIQUIDITY_TOO_LOW / RANGE_CONSUMED — for all
  7 scenarios, so rejected and waiting states are captured (prevents selection bias).
- **Immutable decision-time snapshot** when a scenario first turns READY
  (`paper_signals.csv` / `ready_signals.csv`): Signal UUID, Cairo/receipt/exchange
  timestamps, Last/Bid/Ask/spread/quote-age/cumulative volume, entry, fixed
  +2%/-2%, costs, net RR, Core/Expansion/Extreme ranges, range position/consumed,
  room to each High, ranks, scores, evidence, dataset hash, strategy version.
  **One record per activation cycle** — duplicate READY on subsequent quotes is
  suppressed; a scenario must reset (leave READY) and genuinely re-activate to
  create a new record.
- **Outcomes** (`paper_outcomes.csv` / `outcomes.csv`): computed from **chronological
  Rubix quotes only** after the decision — executable-entry availability, entry
  delay, actual spread, slippage, MFE/MAE, +2% target / -2% stop hits and **which
  occurred first in time**, time-to-target/stop, 1/3/5/10/20-minute returns and
  session-close result. **Never inferred from daily High/Low.** Auction (14:15–14:25
  Cairo) activity is stored **separately** and never treated as breakout confirmation.

## EGX session rules honored

- No **new** continuous-session entry after **14:15 Cairo** (a post-14:15 READY is
  logged with `NO_NEW_ENTRY_AFTER_1415` but produces no signal).
- Auction prices/volume stored separately from continuous-session range formation.

## Reports & artifacts

Rolling: `reports/expected_range_paper_signals.csv`,
`reports/expected_range_paper_outcomes.csv`,
`reports/expected_range_scenario_states.csv`,
`reports/expected_range_daily_paper_summary.csv`.
Per session: `reports/expected_range_paper/YYYY-MM-DD/` with
`pre_session_snapshot.csv`, `scenario_transitions.csv`, `ready_signals.csv`,
`rejected_scenarios.csv`, `outcomes.csv`, `session_summary.json`,
`validation_log.txt`.

Run each session (a scheduler can call this live during the session):

```
python scripts/run_expected_range_paper_session.py
```

## Minimum evidence before ANY strategy judgment (not yet met)

`scalping_expected_range/paper_evidence.py` reports per-scenario signal/executable
counts, target-first / stop-first / neither rates, MFE/MAE, time-to-target/stop,
expectancy after costs, Profit Factor, max drawdown and max consecutive losses,
plus an overall evidence status. Judgment is **not** begun until:

- ≥ 20 completed paper sessions,
- ≥ 30 executable signals overall,
- ≥ 20 signals for any scenario judged independently.

The current state is `COLLECTING_EVIDENCE` (1 session so far). **Production is never
recommended by this tooling**; results are reported per scenario, never merged into
one number. Execution-quality judgment remains forward-only.

## Dashboard

The Expected Range Scalper page shows **🟢 PAPER FORWARD TEST ACTIVE · 🔴 PRODUCTION
DISABLED**, the paper session count, total/executable READY signals, target-first /
stop-first / neither counts, per-scenario evidence, data-quality failures and the
current evidence status. It never shows a production BUY command and uses
scenario-specific labels only.

## Verification

329+ tests pass, including 15 new paper-recording tests (immutable snapshot,
WAIT/rejection recording, READY transition, duplicate suppression, reset +
reactivation, fixed +2%/-2%, chronological target/stop ordering, no daily-bar
inference, no entry after 14:15, auction separation, stale/wide-spread/
range-consumed rejection, paper-on/production-off) and the app/dashboard smoke test.
