# Scalping V2 — Event-Driven Data-Gate Report

## Executive Summary (CURRENT — supersedes all earlier numbers below)

Latest corrected results on the immutable **2026-07-21** session, using correct
EGX session phases (continuous 10:00–14:15, closing auction 14:15–14:25) and
market-wide **outage-duration** measurement:

| Metric | Value |
|---|---:|
| EVENT_DATA_VALID | **159** |
| RANGE_CONFIRMED | **0** |
| RANGE_PARTIAL | **208** |
| Max continuous-trading fatal outage | **317 s** |
| Margin to the 300 s RANGE_CONFIRMED threshold | **17 s** |
| Legacy 60% minute gate (preserved, unchanged) | 0 pass |

**What the 317 s actually was (Phase 2 reconstruction — the final word):** NOT a
collector connection failure. During 10:17–10:22 Cairo the feed was **healthy**
— 4,178 quotes received, frames every ≤3 s, 0 disconnect / 0 reconnect / 0
watchdog — but **Mubasher held the exchange `market_timestamp` frozen for 317 s**
(re-sending unchanged snapshots). It is an **upstream data-staleness**, not a
collector bug. The patched collector was provably active (26 `resubscribe_complete`
events — a patched-only metric). **Therefore no collector fix is applicable.**
Full detail: [SCALPING_EVENT_GATE_FINAL_VALIDATION.md](SCALPING_EVENT_GATE_FINAL_VALIDATION.md),
`reports/rubix_317_second_outage_timeline.csv`.

**Nothing changed:** the 300 s / 60% / event-quality thresholds, Entry/Stop/
Target/RR, Swing/Daily, Classic, Breakout, Adaptive are all untouched. The event
gate is **shadow / paper-only, disabled**, pending multi-session evidence.

---

# SUPERSEDED FINDINGS / AUDIT HISTORY

> Everything below is the chronological audit trail. **The numbers here (150
> EVENT_DATA_VALID, 13.7-minute outage, auction-inclusive and symbol-idle gap
> calculations) are superseded by the Executive Summary above** and must not be
> read as current conclusions.

## ⭐⭐ ADDENDUM 2 — EGX session-phase correction (the real number: 5 min 17 s)

Applying the correct EGX microstructure — **continuous trading 10:00–14:15**,
**closing auction 14:15–14:25** (randomized close ~14:23–14:25) — plus a fix to
measure the **connection-outage duration** (not a liquid symbol's own idle time)
collapses the "14-minute gap" almost entirely.

**Decomposition of every symbol's raw ~14-min max gap (07-21):**
| Component | Duration | Fatal to range? |
|---|---|---|
| Opening auction edge (10:01–10:15 Cairo) | ~14 min | ❌ `SESSION_START_EDGE` |
| Symbol idle while connection healthy | variable | ❌ `SYMBOL_INACTIVE` |
| Closing auction (14:15–14:25) | ≤10 min | ❌ auction, excluded from continuous range |
| **Genuine continuous-trading connection outage** | **317 s (5 min 17 s)** | ✅ the only fatal part |

**The true fatal outage was one 5-min-17-s market-wide connection drop at
10:17–10:22 Cairo** (median = max FatalGap = **317 s** across the universe). My
earlier model mistakenly counted a symbol's full 777 s idle gap that merely
*overlapped* that 317 s outage. Corrected: fatal gap = the market-wide outage
duration within the symbol's active continuous window.

**Recomputed 2026-07-21 (session-phase-aware, corrected):**

| | Value |
|---|---:|
| EVENT_DATA_VALID | **159** |
| RANGE_CONFIRMED | **0** (17 s short) |
| RANGE_PARTIAL | 208 |
| True max continuous-trading connection outage | **317 s (5 min 17 s)** |
| Symbols within 60 s of RANGE_CONFIRMED | **159** |
| Auction close/volume preserved separately | `AuctionLast`, `AuctionVolume` cols |

**Meaning:** on 07-21 Rubix's continuous-trading data was **17 seconds away**
from confirming 159 well-observed, liquid, two-sided symbols (COMI range-confidence
score 78.1). The single blocker is **one sub-6-minute connection outage** — exactly
what the collector connection fix targets, and what a slightly faster reconnect or
a marginally denser session eliminates. **The 300 s / 5-min RANGE_CONFIRMED
threshold was NOT lowered** — 5 min 17 s legitimately misses it by 17 s, and it
stays PARTIAL. This is a far more hopeful and accurate picture than "0 confirmed,
14-minute gap, major collector work needed."

**Answers (updated):** (1) opening auction + symbol-idle + closing auction +
**one 5 m 17 s continuous-trading outage at 10:17–10:22 Cairo**. (2) The single
fatal part is inside continuous trading. (3) No after-close/report-runtime
inclusion. (4) `RANGE_CONFIRMED=0` was **mostly** a classification/session-phase
bug (now fixed); the residual is a genuine but tiny 5 m 17 s outage. (5) **317 s
(5 m 17 s)**. (6) **159** EVENT_DATA_VALID. (7) **0** RANGE_CONFIRMED (by 17 s).
(8) 0 shadow opportunities. (9) **Yes but minimal** — shave one ~5-min outage
(faster reconnect); not major work. (10) **No** threshold or strategy changed —
only EGX session-phase classification + outage-duration measurement. Suite:
**252 passed** (16 event-quality tests incl. session-phase / outage-duration).

---

## ⭐ ADDENDUM — 14-minute gap audit (resolves the discrepancy)

**Finding:** The `RANGE_CONFIRMED = 0` result was **partly a gap-classification
bug and partly a genuine in-session outage** — and it was **not** caused by
after-close or report-runtime time. Traced on the immutable 2026-07-21 data
(`reports/scalping_range_gap_trace.csv`).

**Where the ~14-min gaps actually are:**
- For **147 of 245** symbols (incl. COMI, ADIB, CCAP) the dominant max gap is the
  **opening auction: 10:01→10:15 Cairo** — a `SESSION_START_EDGE`. During it the
  connection was healthy (219 other symbols produced 270 events). This must
  **not** invalidate a session range, but the original model counted it. **Bug —
  now fixed** (COMI's 840 s max gap is now labelled `SESSION_START_EDGE`).
- There is **also** a genuine **market-wide connection outage at 14:09→14:23
  Cairo (~13.7 min)** near close — every symbol silent → `IN_SESSION_CONNECTION_OUTAGE`.

**Why the two earlier reports disagreed:** the live monitor's "≤5 min in-session"
was the max silence *up to its 14:05 checkpoint*; the ~14-min market-wide outage
occurred at **14:09–14:23**, after that checkpoint, so it only appeared at the
after-close read. Both were correct about different windows. Verified: **0 gaps
end after close**; the observation window (07:00–11:30 UTC) is correct; a report
run hours later gives the identical verdict (report-runtime invariance test).

**The fix (gap classification only — no threshold changed):** range confidence
now uses a **fatal gap** = the max *genuine in-session connection-outage* gap,
excluding the opening-auction edge, any after-close interval, and gaps that
occur while the market-wide connection is healthy (symbol-inactivity). The raw
`max_gap` and a `max_gap_classification` are still reported for transparency.
The 300 s/900 s thresholds, the 60% legacy gate, and all strategy code are
unchanged.

**Recomputed 2026-07-21 (corrected):**

| | Raw (old) | Corrected |
|---|---:|---:|
| EVENT_DATA_VALID | 150 | **150** |
| RANGE_CONFIRMED | 0 | **0** |
| RANGE_PARTIAL | 189 | 194 |
| Max-gap class = SESSION_START_EDGE (non-fatal) | — | **147** |
| Max-gap class = IN_SESSION_CONNECTION_OUTAGE | — | 93 |
| True max in-session market-wide outage | — | **~13.7 min (14:09–14:23 Cairo)** |
| Max market-wide silence 10:15–14:09 (core trading) | — | ≤ 5 min |

**COMI worked example:** 3,477 events, first 10:00:01 / last 14:27:21 Cairo; raw
max gap 840 s → **SESSION_START_EDGE (non-fatal)**; fatal gap 822 s = the near-close
outage → **RANGE_PARTIAL**.

**Audit answers:** (1) opening auction 10:01–10:15 **and** near-close 14:09–14:23,
both Cairo. (2) Yes, both inside the session. (3) **No** after-close/report-runtime
inclusion. (4) Partly a window/classification bug (opening edge) — now fixed —
but 0 confirmed persists due to the genuine near-close outage. (5) **~13.7 min**;
core-trading max ≤5 min. (6) **150** EVENT_DATA_VALID. (7) **0** RANGE_CONFIRMED.
(8) **0** shadow opportunities. (9) **Yes** — further collector work is still
needed, but now precisely targeted: eliminate the **mid/late-session connection
outages** (e.g. the 14:09–14:23 dropout — one of the day's 19 disconnects that
reconnected slowly), **not** the opening edge and **not** by lowering any gate.
(10) **No** threshold or strategy changed — only gap classification was corrected.
Full suite: **252 passed**.

---

_(Original report below.)_



**Verdict:** The user's premise is **confirmed with real data** — minute-occupancy
is the wrong quality measure for Rubix's event-driven feed. The new event-driven
gate correctly recognises **150 well-observed, liquid, executable symbols** that
the legacy 60% minute gate wrongly rejects. **But** it also honestly shows that
**0 symbols reach a fully trustworthy range today**, because a universal ~14-minute
connection-blackout gap sits in every symbol's session. The event gate is
therefore **more informative** and is delivered **paper-only, disabled by
default**. No production strategy, threshold, or gate was changed.

Built (isolated, disabled): `scalping/event_quality.py`,
`scalping/event_range_scanner.py`, config block in
`scalping/range_scalper_settings.json` (`event_gate_enabled: false`),
`tests/test_event_quality.py` (8 passing), report
`reports/scalping_legacy_vs_event_gate.csv`.

---

## What the real 2026-07-21 session shows

**Phase 3 — range source:** Rubix `quotes` has **no session High/Low field**
(columns: `last_price, bid, ask, volume, …`). Range must be **event-derived from
genuine Last extremes**. Only **0.5%** of events had zero/null Last (never treated
as prices).

**The event evidence is excellent for liquid names, despite low minute coverage:**

| Symbol | Legacy min-cov | Events | Two-sided | Spread | Turnover (EGP) | Max gap | Quality |
|---|---:|---:|---:|---:|---:|---:|---:|
| COMI | 23.7% ❌ | 3,477 | 100% | 0.06% | 1.08 B | 840 s | 84.2 |
| ADIB | 24.1% ❌ | 3,086 | 100% | 0.04% | 503 M | 840 s | 84.7 |
| CCAP | 23.7% ❌ | 1,361 | 100% | 0.18% | 512 M | 840 s | 81.7 |
| PHDC | 24.1% ❌ | 1,957 | 100% | 0.13% | 286 M | 837 s | 82.8 |

COMI — one of EGX's most liquid stocks — has 3,477 genuine two-sided events
across 267/270 minutes with a 0.06% spread, yet scores only **23.7% minute
coverage**. The legacy gate rejects it; the event gate admits it. **Correlation
EventCount ↔ Turnover = 0.765** (events track real liquidity); **LegacyCoverage ↔
EventCount = 0.52** (minute occupancy is a weak proxy).

**But the ~14-minute blackout caps range confidence:** every symbol's maximum
event gap is **≥ 820 s (~14 min)** — the shared market-wide connection outage, not
symbol inactivity. A move during that blackout could be missed, so no
event-derived range is *fully* trustworthy today → **RANGE_CONFIRMED = 0,
RANGE_PARTIAL = 189.**

## Three coverage types (Phase 1), kept separate

- **CONNECTION_COVERAGE** — market-wide healthy uptime (73% on 07-21).
- **OBSERVED_MINUTE_COVERAGE** — the legacy field, **preserved unchanged** as a
  diagnostic (median ~23%).
- **ACTIVE_EVENT_COVERAGE** — genuine-event density in active minutes (high for
  liquid names). No minute is ever fabricated or forward-filled.

## Legacy vs shadow event gate (real 07-21)

| | Count |
|---|---:|
| Legacy 60% minute gate — PASS | **0** |
| Event gate — EVENT_DATA_VALID | **150** |
| …of which legacy wrongly rejects (well-observed) | **150** |
| RANGE_CONFIRMED (blackout-free enough to trade) | **0** |
| RANGE_PARTIAL | 189 |
| SHADOW_PASS (EVENT_DATA_VALID **and** RANGE_CONFIRMED) | **0** |

Full per-symbol divergence with reasons: `reports/scalping_legacy_vs_event_gate.csv`.

---

## The 10 questions

1. **Is 60% observed-minute coverage appropriate for the Rubix event-driven feed?**
   **No.** It rejects 150 genuinely well-observed, liquid, two-sided symbols
   (COMI, ADIB, CCAP…). Minute occupancy under-measures an event feed.
2. **How many symbols have healthy subscriptions and fresh genuine events?**
   **150** are EVENT_DATA_VALID (enough two-sided events, executable, liquid).
3. **How many have reliable range High/Low?** **0 fully (RANGE_CONFIRMED); 189
   partial.** Limited by the ~14-min connection blackout, not by liquidity.
4. **How many are liquid and executable?** ~150 (event gate) carry real turnover
   and tight spreads; the wide-spread/low-turnover tail is correctly rejected.
5. **How many qualify under the legacy gate?** **0.**
6. **How many qualify under the shadow event gate?** **0 produce an opportunity**
   (SHADOW_PASS needs RANGE_CONFIRMED), but **150 are recognised as well-observed**
   — the key informational gain.
7. **Which symbols diverge and why?** The 150 EVENT_ADMITS/LEGACY_REJECTS symbols
   (well-observed but <60% minutes). None diverge the other way. See the CSV.
8. **Did shadow opportunities survive paper forward validation?** **N/A —** 0
   RANGE_CONFIRMED means 0 shadow opportunities to forward-test today. The
   forward-validation recorder is specified but not exercised (needs a session
   that yields RANGE_CONFIRMED, i.e. after the connection blackout shrinks).
9. **Is replacing the legacy coverage gate recommended?** **Not yet — but adopt
   the event gate as the research quality lens.** The acceptance criteria require
   *multiple* live sessions and forward-validated opportunities; today gives one
   session with 0 confirmed ranges. **Recommendation:** keep the legacy gate as
   production, run the event gate in shadow, and the single remaining blocker is
   **connection max-gap reduction** (shrink the ~14-min blackout below the 5-min
   RANGE_CONFIRMED threshold via further collector/connection work) — **not**
   lowering any threshold.
10. **Did any production strategy or threshold change?** **No.** The 60% gate is
    preserved; Entry/Stop/Target/RR, Swing/Daily, Classic, Breakout, Adaptive,
    and the fixed-2% scalper are untouched. The event gate is disabled/shadow.

---

## Honest scope / deferrals

- **Phase 8 (manual MubasherTrade cross-check):** requires user-supplied
  screenshots/observed values during a live session — not performed here (no
  browser automation, no credential extraction). Recommended as a spot-check of
  event-derived Last/Bid/Ask/High/Low against the MubasherTrade screen.
- **Phase 10 (forward-outcome validation):** the recorder schema is defined, but
  with 0 RANGE_CONFIRMED there are no shadow opportunities to score yet.
- **Multi-session (Phase 7):** only 07-21 had the patched-collector session;
  prior sessions predate the fix. Do not change production behavior on one
  session — gather several patched-collector sessions first.

## Bottom line

The event-driven model proves Rubix **is** a viable *quality* source for
event-driven scalping research on liquid EGX names — the data is there (150
symbols, thousands of two-sided events, tight spreads). The only thing standing
between "well-observed" and "range-confirmed, tradable" is the residual
connection-blackout gap. Close that gap (collector/connection work already begun)
and these 150 symbols flip to RANGE_CONFIRMED — **without lowering the 60%
threshold or touching any strategy.**
