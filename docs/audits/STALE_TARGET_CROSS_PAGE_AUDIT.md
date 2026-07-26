# Final Pre-Merge Audit: Stale Targets and Cross-Page Provenance

Audit date: 2026-07-26 (Africa/Cairo)  
Repository: `D:\EGX_AI_Trader`  
Branch: `fix/stale-target-cross-page-consistency`  
Base `main`: `c86d8abebfa2b98cb6d38946a0301a8a317c3877`  
Implementation commit: recorded after the validated change is committed  
Merge/push status: not merged; not pushed

## Verdict

**PASS.** The defect was in presentation, provenance, and target lifecycle
state. No strategy, indicator, threshold, provider-routing, ranking, portfolio,
AI, or frozen decision calculation changed.

Stock Details had labelled the last completed EODHD close as `Current` and
displayed every frozen target as if it were still a future target. AI Stock
Analysis is a separate one-symbol evidence engine. Consequently, the two pages
can use the same completed close while legitimately showing different scenario
levels.

## Exact seven-file change set

| File | Status | Semantic purpose |
|---|---:|---|
| `core/scanner.py` | Modified | Attaches immutable snapshot/provenance metadata after the strategy decision is complete. |
| `core/level_status.py` | Added | Display-only target-state, comparison-price precedence, timestamp/session safety, remaining-room, and display R/R helpers. |
| `dashboard/provenance_panel.py` | Added | Shared provenance panel for the two pages. |
| `dashboard/stock_details.py` | Modified | Separates frozen/current values, renders target lifecycle states and promoted target, and handles legacy rows safely. |
| `dashboard/ai_stock_analysis.py` | Modified | Renders the typed AI evidence provenance without recalculating analysis values. |
| `tests/test_stale_target_consistency.py` | Added | Target semantics, precedence, calendar, backward compatibility, scanner golden output, provider-call, and page smoke regressions. |
| `docs/audits/STALE_TARGET_CROSS_PAGE_AUDIT.md` | Added | This evidence-backed audit. |

No file under `strategy/`, `indicators/`, provider routing, AI model code,
portfolio, risk, or settings was modified.

## Precise SAUD.CA root cause

### Stock Details / frozen Classic scanner

- Diagnostic calculation time:
  `2026-07-26T09:23:05.627718+00:00`
  (`2026-07-26 12:23:05` Cairo)
- Data domain: `CURRENT_RESEARCH_V2`
- Historical provider: `eodhd`
- Last completed session: `2026-07-22`
- Completed close previously labelled `Current`: `22.11`
- Rubix overlay observed during the diagnostic: `22.50`
- Rubix quote timestamp: `2026-07-26T09:16:36+00:00`
- Engine: `classic_levels@sha256:cbe03e1a2cfc3b89`
- Evidence hash:
  `sha256:f1a4197be02753ecdb9305ecc9b085ba97d603fabdc1e8194e2879e695fcd6fb`
- Frozen levels:
  - Buy range: `21.96–22.11`
  - Stop: `19.84`
  - Target 1: `22.10`
  - Target 2: `23.09`
  - Original frozen R/R: `0.43`
  - Support: `19.99`
  - displayed resistance field: `22.16`

The pre-fix scanner row stored `Price = round(float(last["Close"]), 2)`.
Stock Details rendered that value as `Current`, even though it was the last
completed daily close and not the Rubix live price.

Classic `strategy.entry.entry_signal` calculates its target resistance from the
20-row entry window excluding the current row (`High[start:i]`). It sets Target
1 to that resistance and Target 2 to resistance plus `2 × ATR`. The separate
Classic support/resistance display calculation includes the current row
(`High[start:i+1]`). This is why SAUD.CA can correctly have frozen Target 1
`22.10` while the displayed resistance is `22.16`.

### AI Stock Analysis / current evidence engine

- Analysis time: `2026-07-26T12:12:31.448251+03:00`
- Data domain: `CURRENT_RESEARCH_V2`
- Historical provider: `eodhd`
- Live provider: `rubix`
- Last completed session: `2026-07-22`
- Completed close: `22.11`
- Rubix quote in that evidence: `22.44`
- Rubix timestamp: `2026-07-26T09:06:48+00:00`
- Engine: `ai_analysis_evidence@1.1.0`
- Evidence hash:
  `sha256:21c30d1604c6fa3e4ed2ecc372fe39758937dbe3008aaed1a072c957a17e6022`
- Scenario:
  - Trigger / entry low: `22.16`
  - Entry high: `22.4086`
  - Target: `23.4032`
  - Stop: `21.2398`
  - R/R: `1.35`

AI `_scenarios` uses channel high `22.16` as the breakout trigger and its own
typed ATR scenario geometry. It is not the Classic entry engine. The difference
is therefore caused by different engine identities and evidence snapshots, not
by stale values leaking between pages.

## Scanner semantic diff

`scan_symbols(source, data_purpose="scanner")` keeps the exact same function
arguments. It still returns the same sorted list of result dictionaries. No
wrapper, tuple, class, extra return value, or error contract was introduced.

The strategy is evaluated first. Only after `result` and `breakout_result`
exist, the scanner records:

- `frozen_price` from the same completed `Close` already used by `Price`
- completed session and centralized Cairo session-close timestamp
- Classic level-engine source fingerprint
- evidence hash of the completed indicator frame and frozen result
- signal-recording timestamp

Existing `Price` remains the same rounded completed-close value. The following
keys are additive:

- `FrozenSnapshotPrice`
- `FrozenDataTimestamp`
- `SignalTimestamp`
- `LastCompletedSession`
- `CompletedSessionClose`
- `CompletedSessionTimestamp`
- `CompletedSessionProvider`
- `LevelsCalculationVersion`
- `EvidenceHash`
- `HistoricalProvider`
- `DataDomain`
- `LivePrice`
- `LivePriceTimestamp`
- `LivePriceReceivedTimestamp`
- `LivePriceStatus`
- `LiveProvider`
- `SnapshotStatus`

These fields are never passed back to the strategy, AI decision, score,
confidence, ranking key, actionability, paper portfolio, or position sizing.
`load_history` remains called once per requested symbol. No additional provider
call was added.

## Frozen-output equality

The scanner golden regression uses the immutable pre-fix projection for:

- `Signal`
- `Score`
- `Confidence`
- `BuyLow`
- `BuyHigh`
- `StopLoss`
- `Target1`
- `Target2`
- `RR`
- `Reasons`
- sorted ranking order

The representative deterministic basket is:

- `SAUD.CA`
- `COMI.CA`
- `EAST.CA`
- `SWDY.CA`
- `TMGH.CA`
- `WATCH.CA`
- `BLOCKED.CA`

It contains BUY, WATCH, AVOID, and blocked/insufficient cases. Every frozen
field and the ranking order match the pre-fix golden values exactly. The
blocked symbol remains excluded. Provider calls are `7 before / 7 after`, one
per symbol, in the same order.

The actual audited Classic outputs for the five named market symbols were also
unchanged:

| Symbol | Signal | Score | Confidence | Buy range | Stop | T1 | T2 | Frozen R/R |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SAUD.CA | WATCH | 102 | 100 | 21.96–22.11 | 19.84 | 22.10 | 23.09 | 0.43 |
| COMI.CA | WATCH | 79 | 90 | 139.21–140.00 | 125.42 | 140.40 | 145.69 | 0.39 |
| EAST.CA | AVOID | 0 | 0 | 0–0 | 0 | 0 | 0 | 0 |
| SWDY.CA | WATCH | 92 | 100 | 93.06–93.60 | 83.76 | 93.80 | 97.42 | 0.39 |
| TMGH.CA | WATCH | 65 | 80 | 99.78–100.50 | 91.38 | 103.87 | 108.64 | 0.89 |

`ZZZZ.CA` remained blocked with `DATA_UNAVAILABLE`. With no AI probabilities,
the audited ranking remained `SAUD, SWDY, COMI, TMGH, EAST`.

Sensitive source files are byte-identical to `main` by Git blob ID:

| File | Base/current blob |
|---|---|
| `strategy/decision_engine.py` | `1c3482481ddeb0cb5e298362d730fa7129879586` |
| `strategy/entry.py` | `0991a4ed365362b6248a922da9f59ef52fb86193` |
| `strategy/support.py` | `06c238ad08b3d0b27ff1242a90becb6c9fb3912c` |
| `strategy/trading_decision.py` | `58ea7f7bbe20d1cc54f2baf03a7f5fbfbb623f5a` |
| `indicators/technical.py` | `26e1204c6e543ba3a20c689ac363e2b763eb4804` |
| `strategy/config.py` | `1c401fc34525b6f2d23708ac0a80448546134245` |

## Target-state truth table

All comparisons use raw full-precision numeric values. Formatting occurs only
after state calculation.

| Direction | Current relation to target | Verified post-signal extreme | State |
|---|---|---|---|
| LONG | current below target | no qualifying prior cross | `ACTIVE` |
| LONG | current equal to/above target | not required | `REACHED` |
| LONG | current below target | verified high crossed target at/after signal | `PREVIOUSLY_REACHED` |
| SHORT | current above target | no qualifying prior cross | `ACTIVE` |
| SHORT | current equal to/below target | not required | `REACHED` |
| SHORT | current above target | verified low crossed target at/after signal | `PREVIOUSLY_REACHED` |
| Either | missing/non-positive value or incompatible provenance/time | irrelevant | `STALE_LEVEL` |

`PREVIOUSLY_REACHED` cannot be inferred from target ordering, Target 2
existence, rounded display values, or a target that was already behind the
frozen snapshot. It requires both:

1. an explicitly verified market observation crossing the target (high for
   LONG, low for SHORT); and
2. a compatible aware timestamp at or after signal creation.

Naive and timezone-aware timestamps are rejected as incompatible; they are not
silently localized.

Rounding regressions explicitly prove:

- raw `22.104 < 22.105` is `ACTIVE`
- values that both display as `22.10` can remain `ACTIVE`
- raw `22.105 == 22.105` is `REACHED`
- raw `22.106 > 22.105` is `REACHED`
- floating-point boundary handling uses only a `1e-9` equality guard, far below
  an EGX tick
- missing, NaN, zero, and negative prices/targets are rejected safely

## Price-source precedence and session compatibility

Comparison-price precedence is explicit:

1. valid Rubix live price
2. latest completed-session close
3. frozen snapshot price only when no fresher comparable price exists

A Rubix value is accepted as live only when it is positive, its provider is
Rubix, status is not stale/unavailable/error, both timestamps are comparable
and timezone-aware, quote time is at or after signal creation, and the
centralized EGX calendar reports `CONTINUOUS` or `CLOSING_AUCTION`.

The tests cover:

- same continuous session
- closing auction
- after 14:25 `CLOSED`
- weekend
- holiday
- next trading session
- stale Rubix quote
- Rubix quote older than the frozen calculation
- completed-session fallback
- frozen fallback
- signal produced before a later live overlay

The UI shows value, provider, exact timestamp, and whether the selected basis is
live, completed-session, or frozen. Its warning names the Rubix timestamp,
frozen signal timestamp, and last completed session rather than silently
mixing them.

## Active-target and display R/R behavior

When Target 1 is reached:

- Target 1 remains visible as `REACHED`
- Target 2 becomes `Next Active Target`
- Remaining Room is calculated from the selected full-precision comparison
  price to Target 2
- `Remaining R/R to Next Target` uses Target 2 and the original frozen stop
- `Original Frozen R/R` remains unchanged and is labelled as frozen

For a LONG scenario below/equal to its original stop, remaining R/R is
suppressed. For a SHORT scenario above/equal to its original stop, it is also
suppressed. If all targets are reached or stale, there is no next target,
remaining room, remaining R/R, or active-entry language; the page requires a
fresh analysis.

This is display interpretation only. It never rewrites the original decision
trace or target values.

## Backward compatibility

Old serialized scanner rows may lack timestamps, evidence hash, engine version,
provider provenance, and live-overlay metadata. Stock Details:

- loads them without error
- labels them `Legacy Snapshot`
- does not invent an evidence hash
- does not invent timestamps
- does not invent an engine or provider
- marks target comparison `STALE_LEVEL`

The regression includes an old-payload unit test and a Streamlit AppTest smoke
of Stock Details. Existing result dictionary keys and page navigation remain
compatible.

## Cross-page presentation

Stock Details now shows:

- frozen Classic engine/version
- frozen levels timestamp
- frozen snapshot price
- Rubix overlay value/source/timestamp
- selected comparison basis
- original target states
- next active target
- original frozen R/R and separate display remaining R/R

AI Stock Analysis shows:

- AI evidence engine/version
- analysis timestamp
- evidence hash
- current typed scenario levels
- historical and live provider provenance

Both pages display:

> قد تنتج المحركات ولقطات الأدلة المختلفة مستويات صحيحة مختلفة.  
> Different engines and evidence snapshots may produce different valid levels.

The AI page continues to render typed Core fields; no scenario value is
recalculated in the UI.

## Validation

Final validated results before commit:

| Validation | Result |
|---|---:|
| Target status / scanner golden / Stock Details | 39 passed |
| AI Stock Analysis | 112 passed |
| Launcher and Rubix | 122 passed |
| AI Narrative | 235 passed |
| Combined focused audit | 508 passed |
| Full repository suite | 1071 passed |
| Streamlit application smoke | `STREAMLIT_SMOKE_OK` |
| Stock Details page smoke | passed within target-status suite |
| AI Stock Analysis page smoke | passed within AI Stock UI suite |
| `git diff --check` | passed |
| Diff under `strategy/`, `indicators/`, and settings | empty |

## Final confirmation

- No trading calculation changed.
- No strategy or indicator source changed.
- No threshold changed.
- No provider routing or provider request changed.
- No ranking, AI, portfolio, sizing, or risk behavior changed.
- No additional market-data call was introduced.
- The original frozen decision trace is retained verbatim.
- Only target lifecycle presentation, next-target display metrics, explicit
  frozen/live labels, provenance, and staleness warnings changed.
- This branch remains unmerged and unpushed pending approval.
