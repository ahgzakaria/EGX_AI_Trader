# ORB + First Pullback — Dashboard Wireframe

Status: Phase 1 visual/information architecture only. No Streamlit code changed.

## Page role

The main Scalping Dashboard becomes a single chronological pipeline for:

**زخم النطاق الافتتاحي + أول إعادة اختبار**

**Opening Range Momentum + First Pullback**

Permanent page badges:

- `Production Disabled / الإنتاج معطل`
- `Shadow / Paper / Research Only`
- `No Broker Execution / لا يوجد تنفيذ عبر وسيط`

Stable Range-Bound and Daily EMA5/EMA10 Uptrend Pullback remain accessible only as **Secondary Research Filters / فلاتر بحثية ثانوية** and never show primary entry-readiness actions.

## Top-level layout

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ زخم النطاق الافتتاحي + أول إعادة اختبار                                     │
│ Opening Range Momentum + First Pullback                                     │
│ [Production Disabled] [Shadow/Paper] [Research Only]                        │
├──────────────────────────────────────────────────────────────────────────────┤
│ EODHD D-1      Rubix health       Session phase       OR clock               │
│ 2026-..        Healthy/Stale      Continuous/Auction  12/15 completed        │
│ Data freshness Mapping coverage   Last completed bar  Validation status      │
│ ...            225/241 verified   10:12 / partial?    INSUFFICIENT HISTORY   │
├──────────────────────────────────────────────────────────────────────────────┤
│ Localized blocker banner with exact missing requirement                     │
│ e.g. لا يمكن حساب RVOL الزمني: 7 جلسات سليمة فقط من الحد الأدنى 20          │
├──────────────────────────────────────────────────────────────────────────────┤
│ [1 Candidates] [2 Opening Range] [3 Momentum] [4 First Pullback]             │
│ [5 Entry Ready] [6 Active Paper Trades] [7 Session Review]                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

The status row refreshes from the current session/read model, never from a prior symbol/session cache. Auction phase changes the page banner and disables all new-entry affordances while preserving trade-management visibility.

## Tab 1 — Pre-Session Candidates / مرشحو ما قبل الجلسة

```text
Filters: [selected D-1/session] [quality status] [mapping] [search ticker]

Ticker | Company | Daily trend* | Median daily turnover | ATR | Resistance
       | Available upside | Rubix mapping | Selection reason
```

`*` Daily trend is context, not a mandatory truth. The row explains rejected and selected states. Candidate count is market-dependent (roughly 10–25 is a goal, not a forced quota).

Side metrics: active universe 241, eligible count, verified mapping count, stale/invalid histories, insufficient liquidity, insufficient movement/upside.

## Tab 2 — Opening Range / النطاق الافتتاحي

```text
Ticker | OR high | OR low | OR width % / ATR | OR volume state
       | Turnover state | True VWAP state | Current price | Phase

Selected row card:
Expected slots: 15 | Completed valid: 12 | Missing: 3
Range status: جارٍ بناء النطاق الافتتاحي
Exact note: cannot finalize before 10:15 and before all required completed slots
```

Unavailable values show `غير متاح` plus a reason; never `0`. Before completion, provisional high/low may be drawn as forming context but are visibly unfrozen and cannot feed later states.

## Tab 3 — Momentum Qualified / الأسهم المؤهلة للزخم

```text
Ticker | Breakout time | Completed 5m close | Distance from OR high
       | Distance from true VWAP (or unavailable) | RVOL status
       | Spread status | Next daily resistance | Localized state
```

Rows explicitly say `مؤهل للزخم — لا دخول بعد`. An overextended row stays visible with `الاختراق ممتد — ممنوع المطاردة` and the measured reason. The first breakout never exposes an “enter” action.

## Tab 4 — First Pullback / انتظار أول إعادة اختبار

```text
Ticker | Pullback low | Depth % / intraday ATR | Completed bars
       | Selling-volume behavior | OR high hold | VWAP hold/reclaim
       | Confirmation needed | Exact rejection/block reason
```

The view distinguishes:

- waiting for the first pullback;
- first pullback in progress;
- waiting for a completed reclaim;
- failed structure;
- a later/second pullback that is not eligible.

No row becomes ready from a touch alone.

## Tab 5 — Entry Ready / فرص جاهزة للدخول البحثي

```text
Ticker | Research trigger | Structural stop | Target 1 | Target 2 | R/R
       | Risk amount | Paper position size | Confirmation time
       | Quote freshness | Spread state | Volume state | Research-only status
```

Each card shows:

- confirmation rule and completed bar timestamp;
- defended structure and ATR buffer;
- frozen levels and source/config hashes in an audit detail;
- exact execution-quality gates;
- permanent `Research/Paper Only — ليست توصية أو أمر تنفيذ` warning.

If spread is absent, show `SPREAD_UNAVAILABLE / السبريد غير متاح`, never a calculated zero. If true VWAP/RVOL are unavailable, show the capability and sample-count reasons.

## Tab 6 — Active Paper Trades / الصفقات الورقية النشطة

```text
Ticker | Entry | Current bid | Frozen stop | Target 1 | Target 2
       | Initial/current risk | Quantity remaining | State | Time in trade
```

Actions are paper-model controls only and should be minimal. The panel emphasizes:

- no averaging down;
- stop cannot be widened;
- partial 1R status;
- completed-5m trailing rule;
- time-stop countdown;
- management cutoff before auction.

No broker/order language is used.

## Tab 7 — Session Review / مراجعة الجلسة

```text
Session identity and source/config fingerprints
Pipeline counts: candidate → OR ready → momentum → pullback → ready → paper trade
Failures by localized reason
Feed quality: quote age, gaps, coverage, volume reliability, spread availability
Paper outcomes: chronological fills, fees/slippage, conservative ambiguity flags
Validation: INSUFFICIENT_INTRADAY_HISTORY until evidence gate passes
```

Performance-like summaries carry sample size and validation status. The page does not extrapolate profitability from a small sample.

## Live details panel

Selecting one ticker opens a stable side/below panel without changing pipeline state:

```text
┌──────────────────────────── Ticker details ────────────────────────────────┐
│ D-1 context: completed date, ATR, support/resistance, upside, mapping      │
├────────────────────────────────────────────────────────────────────────────┤
│ 1m chart: continuous background | separate auction shading | stale gaps    │
│ 5m chart: completed bars solid | current partial bar dashed/not confirmable │
│ overlays: OR band, supported true VWAP (else unavailable), EMA9/EMA20       │
│ markers: breakout, pullback start/low, reclaim, trigger, stop, targets      │
│ lower pane: volume/turnover capability and quality                          │
├────────────────────────────────────────────────────────────────────────────┤
│ Transition timeline                                                        │
│ 10:15 OR ready → 10:20 Momentum qualified → 10:28 Pullback ...             │
│ Each event: localized rule, completed timestamp, evidence, data quality     │
├────────────────────────────────────────────────────────────────────────────┤
│ Exact current blocker/readiness evidence + Research/Paper Only warning      │
└────────────────────────────────────────────────────────────────────────────┘
```

Auction data may be displayed in separate shading for context but is excluded from continuous indicators/levels/transitions. Stale gaps are visual gaps, not forward-filled candles.

## Secondary Research Filters / فلاتر بحثية ثانوية

Placed below the primary pipeline, visually separated:

```text
Secondary Research Filters / فلاتر بحثية ثانوية
[Stable Range-Bound / تداول داخل رينج ثابت]
[Daily EMA5/EMA10 Uptrend Pullback / اتجاه يومي قرب الدعم]

Permanent label: context only; cannot generate ORB entry readiness
```

Existing backend results remain viewable for daily trend, levels, volatility, liquidity and range/trend context. Remove/relabel any old live `ENTRY_READY_RESEARCH_ONLY` presentation from this section in Phase 4; do not change or delete the underlying selectors.

## Localized state presentation

The service returns enums; the UI maps them through one exhaustive localization dictionary. Unknown enum values render as a safe `حالة غير معروفة — غير قابل للدخول` diagnostic, never raw text or readiness.

Visual tones:

- neutral/blue: building/waiting/context;
- amber: qualified but no entry, extended, data capability warnings;
- red: failed/stale/auction/no-entry;
- research accent (not production green): `ENTRY_READY` paper-only;
- muted: exited/expired/session review.

No green BUY badge is used. The primary noun is always “research opportunity” or “paper signal,” not recommendation/order.

## Empty, missing-data, and error behavior

- Keep every pipeline tab visible even with zero rows.
- Show the exact reason and count, such as no candidates, OR still building, no valid breakout, insufficient history, or collector stale.
- Never display missing numeric inputs as zero.
- A per-symbol calculation error creates a local failed row and diagnostic event; it does not crash the full dashboard.
- If the ORB service/store is unavailable, the page remains visible in Production Disabled mode and old filters remain secondary context only.
- Current session identity, selected ticker and configuration hash key any UI cache. Cross-session or cross-symbol reuse is prohibited.

## Research alerts panel

Alerts are timeline notifications for `MOMENTUM_QUALIFIED`, `WAIT_FIRST_PULLBACK`, `ENTRY_READY`, `STOP_HIT`, `TARGET_1_HIT`, `TARGET_2_HIT`, `TIME_STOP`, and `LATE_SESSION_EXIT`. Every alert includes ticker, localized state, timestamp, trigger/stop/target where applicable, evidence, and `Research/Paper Only`. It contains no broker instruction.

## Responsive/visual acceptance criteria

1. The seven primary tabs remain in chronological order and are keyboard reachable.
2. Production Disabled and Research/Paper remain visible without scrolling.
3. Tables use localized labels; no raw enum is visible.
4. Missing VWAP/RVOL/spread/turnover values show capability reasons, not zeros.
5. Current/partial 5m bar cannot look like completed confirmation.
6. Continuous and auction periods are visually distinct.
7. Old selectors are visibly secondary and cannot show a primary entry-ready control.
8. Selected ticker details and timeline update together and cannot reuse another ticker's state.
9. UI snapshots cover Arabic/English wrapping, empty states, stale state, auction state and entry-ready research state.
