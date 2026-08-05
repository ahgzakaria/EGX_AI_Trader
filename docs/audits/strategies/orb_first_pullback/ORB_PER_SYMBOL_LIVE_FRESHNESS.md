# ORB — Per-Symbol Live Decision Freshness

**Research Only. Production execution disabled.** `ENTRY_READY_RESEARCH` is a
research candidate, never an order. No order, size, routing or execution path
exists in this package, and nothing in this change adds one.

## The finding

2026-08-05 ran a clean session — 435,851 normalized events, no stall, 255/255
continuous minutes — and produced **zero** live research candidates. The audit
attributed this to `LIVE_DECISION_DISABLED_FRESHNESS` on 6,847 Lane A rows
across 86 symbols, and the obvious next question was whether Rubix was
delivering stale prices or the system was mis-measuring quote age.

**Neither.** The gate never read a quote age at all.

`engine.py:_freshness_reasons` appended the rejection unconditionally once it
had established that the mode was live:

```python
# SHADOW_LIVE and LIVE_DISABLED are both disabled today.
reasons.append(RejectionReason.LIVE_DECISION_DISABLED_FRESHNESS)
if context.live_freshness_status is LiveFreshnessStatus.LIVE_FRESHNESS_FAILED:
    reasons.append(RejectionReason.STALE_LIVE_DATA)
```

`LiveDecisionCapability` had four members and every one was a
`LIVE_DECISION_DISABLED_*` variant, so there was no value the condition could
have tested for. This was deliberate: Phase 2A made live research readiness
unreachable *by construction* rather than by configuration.

The data confirms the diagnosis rather than the hypothesis:

| Measure | 2026-08-05 |
|---|---|
| `LIVE_DECISION_DISABLED_FRESHNESS` | 6,847 |
| `STALE_LIVE_DATA` | **0** |
| `UNRELIABLE_MARKET_TIMESTAMP` | **0** |
| `stale_live_bars` | **0** |
| median receive lag | 0.818 s (budget 60 s) |
| p95 receive lag | 1.571 s |
| above budget | 0.0016 % |

If the feed had been stale, `STALE_LIVE_DATA` would have appeared alongside. It
appeared zero times in 18,818 rows. Freshness was computed, it passed, and the
block happened anyway.

Corroborating evidence: `live_evidence_fresh` already gates bar finality via
`ShadowSnapshotBuilder.classify()`. Had it been false, no bar would have become
`OPERATIONALLY_FINAL` and no breakout would have formed. 48,689 bars completed
and 6,847 breakouts formed, so the watermark was fresh throughout.

**The strategy worked.** It detected valid live breakouts on 86 symbols. Every
one was discarded at a gate that never looked at the data.

## Why the session-wide watermark could not be the authority

The obvious fix — gate on `snapshot.watermark.live_evidence_fresh` — is wrong,
and the Rubix data shows why. Inside the continuous window, market timestamps
reach back to 08:20 and the maximum observed lag is **5,992 s**. Some symbols
genuinely carry very old prints.

The watermark is derived from the *latest* market and receive timestamps across
all 224 symbols. One actively-trading symbol keeps it fresh for every other
symbol on the exchange, including one that last printed hours earlier. A
watermark is a **source health** signal. It is not per-symbol authority.

There is a second trap. `live_freshness_status` and `collector_age_seconds` are
both settled when an event is *normalized*. A symbol that printed once at 10:05
with a 0.4 s receive lag keeps `LIVE_FRESHNESS_PASSED` for the rest of the day.
Reading that stored status at 14:00 says "fresh" about a four-hour-old price.

## The contract

```
source watermark healthy
AND this symbol has its own event
AND its market timestamp is verified and reliable
AND its receive lag <= 60 s
AND its arrival freshness passed
AND (evaluation instant − its receive time) <= 60 s      ← re-measured now
→ LIVE_DECISION_ENABLED_RESEARCH_ONLY

anything missing, unverifiable or out of budget
→ LIVE_DECISION_DISABLED_*
```

The last clause is the one that does the real work, and the one a watermark
cannot express. It is re-measured against the evaluation instant rather than
read from the event, which is what makes a symbol that stopped printing go
stale on its own.

Implemented in `capabilities.assess_live_decision_capability()`, called
per-symbol from `OrbShadowService.evaluate_live()` using
`_latest_event(snapshot, ticker)` — that symbol's most recent event by receive
time, **never** another symbol's.

### What did not change

- the 60 s freshness budget, and the out-of-order tolerance
- every ORB strategy rule: opening range, breakout, pullback, reclaim, risk,
  targets, cutoffs
- deduplication and the liveness watchdogs
- `LIVE_DISABLED` mode, which stays closed whatever the capability says —
  it means "do not decide", and fresh data is not grounds to reinterpret it
- production execution, which remains disabled and unreachable

The single enabled member is named `LIVE_DECISION_ENABLED_RESEARCH_ONLY`, and a
test asserts it is the only non-disabled member and that no member's name
contains order, execution, trade, buy or sell vocabulary.

## Point-in-time replay — 2026-08-05

**POINT-IN-TIME GATE REPLAY — NOT ORIGINAL LIVE ARTIFACT.**

For every one of the 18,818 Lane A observation instants, each symbol's most
recent quote was resolved from Rubix using **only rows with
`received_at <= that instant`**, and the new gate applied. No Lane B evidence,
no later corrections, no hindsight. 1,082,034 quotes across 224 symbols indexed.

### The 6,847 rejected live breakouts

| Verdict | Rows | Share |
|---|--:|--:|
| `ENABLED_RESEARCH_ONLY` | 6,744 | 98.5 % |
| `DISABLED_STALE_QUOTE` | 97 | 1.4 % |
| `DISABLED_MARKET_TIME_UNRELIABLE` | 6 | 0.1 % |

All 86 symbols had at least one eligible instant. Quote age when enabled:
median 3.6 s, p95 28.6 s, max 59.4 s — inside the unchanged 60 s budget.

### Exchange-wide, by Cairo hour

| Hour | Observations | Enabled | % | Stale | Unreliable |
|---|--:|--:|--:|--:|--:|
| 10 | 1,931 | 1,899 | 98.3 % | 32 | 0 |
| 11 | 2,145 | 2,067 | 96.4 % | 78 | 0 |
| 12 | 6,267 | 5,824 | 92.9 % | 440 | 3 |
| 13 | 6,732 | 6,261 | 93.0 % | 465 | 6 |
| 14 | 1,743 | 1,670 | 95.8 % | 67 | 6 |
| **All** | **18,818** | **17,721** | **94.2 %** | **1,082** | **15** |

**The gate is not a rubber stamp.** It refuses 5.7 % of observations — 1,082
instants where that specific symbol's own last print was over 60 s old while the
session watermark was perfectly fresh. Those are exactly the decisions a
watermark-based gate would have wrongly authorised.

### The 14 symbols Lane B found ENTRY_READY

Every one was eligible from its first Lane A evaluation at 10:06:35, and several
had genuine stale stretches during the day:

| Symbol | Lane A rows | Enabled | Stale |
|---|--:|--:|--:|
| ADIB, BTFH, CCAP, EFID, EMFD, HELI, PHDC | 100–101 | all | 0 |
| ISMQ | 99 | 99 | 0 |
| OLFI | 98 | 96 | 2 |
| CNFN | 87 | 84 | 3 |
| CANA | 87 | 78 | 9 |
| EASB | 86 | 77 | 9 |
| EPCO | 83 | 71 | 12 |
| DGTZ | 84 | 67 | 17 |

## What this does and does not predict

**Gate passage is not a signal count.** Passing the gate only allows a symbol to
continue into pullback, reclaim, risk and target evaluation. Whether it reaches
`ENTRY_READY_RESEARCH` is decided by the unchanged strategy rules.

So this replay does **not** promise 14 live candidates, or any number. What it
establishes is narrower and firmer: on 2026-08-05, 98.5 % of the live breakouts
that were discarded would have been permitted to continue being evaluated, and
the remaining 1.5 % would still have been refused — correctly, on their own
symbol's evidence.

The honest expectation for the next session is not a signal count. It is that
each symbol will be judged on its own quote age, fresh symbols will progress,
and stale symbols will not.

## Tests

`tests/test_orb_per_symbol_live_freshness.py` — the contract clause by clause,
the fail-closed paths, the budget boundary at exactly 60 s, a quote from the
future, and the two that matter most:

- a symbol that arrived fresh at 10:05 and stopped printing is refused at 14:00,
  despite still carrying `LIVE_FRESHNESS_PASSED`
- one symbol ticking a second ago does not make another symbol's four-hour-old
  print eligible, even with the source watermark healthy

`tests/test_orb_phase2b_core.py` — the two Phase 2A guard tests were rewritten
rather than removed. They previously asserted live could never reach readiness
and that the enum had no enabled member. They now assert that the default is
still closed, that stale evidence still cannot reach readiness under any
disabled capability, that `LIVE_DISABLED` stays closed even with fresh data, and
that exactly one enabled member exists and it is research-only.
