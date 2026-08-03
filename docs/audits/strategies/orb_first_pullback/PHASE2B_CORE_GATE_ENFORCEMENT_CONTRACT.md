# Phase 2B Core — Gate Enforcement Contract

Where each rule is actually enforced, and which configuration fields are live.

Written during the final pre-commit review of Phase 2B Core, because four of the
six defects that review found were of one kind: **a threshold that looked like a
gate but sat in a phase that could never trigger it.** Passing tests did not
reveal them — the tests exercised the phase where the gate was unreachable, and
agreed with it. This document exists so the next reader can check placement
without re-deriving the reachability argument.

Research only. The furthest state reachable anywhere below is
`ENTRY_READY_RESEARCH`.

---

## 1. The reachability rule

> A pullback resolves on the **first** completed bar whose low reaches OR High.

Everything else follows from it. Any completed close below OR High implies a low
at or below OR High, so such a close *always* resolves the pullback on the bar
that produced it. Therefore, inside a single pullback assessment:

- `closes_below_or_high` ∈ {0, 1}
- `structural_breach_bars` ∈ {0, 1}

A gate of the form `count > n` for `n >= 1` is **unreachable** in the pullback
phase. Multi-bar tolerances belong to `WAIT_RECLAIM`, which is the only phase
that observes more than one post-pullback bar.

These two counters are still computed and persisted. They are **evidence, not
gates** — a distinction the code states explicitly at the point of computation.

## 2. Where each gate lives

| Rule | Enforced in | Configuration | Live? |
|---|---|---|---|
| Close strictly above OR High, by a minimum margin | `WAIT_BREAKOUT` | `minimum_close_above_or_high_percent` | yes |
| Wick-only rejection | `WAIT_BREAKOUT` | — | yes |
| Earliest breakout time | `WAIT_BREAKOUT` | `earliest_breakout_time` | yes |
| Bar validity, update count, auction contamination | breakout assessment | `minimum_updates_for_breakout_bar` | yes |
| D-1 resistance proximity | breakout assessment | `maximum_distance_to_daily_resistance_percent` | yes, when D-1 resistance is known |
| Initial reward/risk | breakout assessment | `minimum_initial_reward_risk` | yes, when D-1 resistance is known |
| Anti-chase, percent | breakout assessment | `maximum_breakout_extension_percent` | yes |
| Anti-chase, extension in ATR | breakout assessment | `maximum_breakout_extension_atr` | yes, when intraday ATR is available |
| Anti-chase, bar range in ATR | breakout assessment | `maximum_breakout_bar_range_atr` | yes, when intraday ATR is available |
| Pullback must start | `WAIT_FIRST_PULLBACK` | `maximum_bars_until_first_pullback`, `minimum_pullback_depth_percent` | yes |
| Pullback depth, percent and ATR | pullback assessment | `maximum_pullback_depth_percent`, `maximum_pullback_depth_atr` | yes |
| Pullback duration | pullback assessment | `maximum_pullback_bars` | yes |
| Wick through the zone floor | pullback assessment | `maximum_low_below_zone_lower_percent` | yes |
| Selling-volume expansion | pullback assessment | `require_reduced_selling_volume_when_volume_valid`, `volume_requirement_mode` | yes, when volume is valid |
| Breaching closes while waiting | **`WAIT_RECLAIM`** | `maximum_structural_breach_bars` (default **0**) | yes |
| Reclaim confirmation | `WAIT_RECLAIM` | `reclaim_rule`, `require_completed_bar` | yes |
| Confirmation expiry | `WAIT_RECLAIM` | `confirmation_expiry_bars` | yes |
| Stop below pullback low, buffered | reclaim → readiness | `stop_atr_buffer`, `stop_percent_buffer` | yes |
| Stop distance ceiling | reclaim → readiness | `maximum_stop_distance_percent` | yes |
| Final reward/risk | reclaim → readiness | `minimum_reward_risk` | yes — see §4 |
| Late-session cutoff, expiry, auction | every bar, and again at `as_of` | `latest_research_entry_time`, `expiry_time`, `data.continuous_end` | yes |

`maximum_structural_breach_bars` defaults to `0`: the first decisive close under
the breach threshold fails the structure. That reproduces the pre-review
behaviour exactly. It is a tolerance knob, not a loosened default.

## 3. Fields that are validated, not switchable

These are not inert flags. Disabling one raises at construction:

- `require_completed_bar` — a touch is never confirmation.
- `require_first_pullback_only` — the engine only ever constructs ordinal 1.
- `reclaim_rule` — must be in `IMPLEMENTED_RECLAIM_RULES`.

`IMPLEMENTED_RECLAIM_RULES` contains exactly `CLOSE_RECLAIMS_OR_HIGH`. The other
two `ReclaimRule` members are the durable state-machine contract's vocabulary,
**declared but not implemented**. Selecting one raises. They previously both
reduced to `close > OR High` — the default rule — so choosing them changed the
recorded rule name and nothing else.

## 4. What `minimum_reward_risk` can and cannot do

`effective_reward_risk` measures reward to the furthest projected target still
reachable before known D-1 resistance:

```
usable_target = resistance if (resistance is known and resistance < target_2) else target_2
effective     = (usable_target - trigger) / risk_per_share
```

Measuring to `target_1` would make the ratio identically `target_1_r_multiple`
and the gate a no-op. Measuring to `target_2` avoids that — but note the honest
limit:

> **With no known D-1 resistance, `effective_reward_risk` is always exactly
> `target_2_r_multiple` (2.0).** The gate can then only bite through a configured
> minimum above 2.0 — never through market structure.

So the gate discriminates only when D-1 resistance is known and falls between
`trigger + minimum_reward_risk * R` and `target_2`. That range is real and
reachable, and is pinned by test at its exact boundary. No resistance is ever
invented to populate it.

## 5. Units

Every comparison is like-for-like; none mixes points with fractions.

- `distance_above_or_high_percent`, `bar_range_percent`, depth percents — all
  fractions of a price (OR High or the post-breakout high), compared only to
  fractional thresholds.
- `extension_atr`, `bar_range_atr`, `depth_atr`, `stop_distance_atr` — all
  price ÷ **intraday** ATR, compared only to ATR-unit thresholds.
- ATR-conditional gates are **skipped** when intraday ATR is unavailable. Daily
  ATR is D-1 context and is never substituted. `IntradayAtr` always reports its
  own status; below warm-up its value is `None`, never a fabricated number.

## 6. Anti-chase evidence

`BREAKOUT_TOO_EXTENDED` records the gate that fired *by name*, plus every
measurement taken. It previously recorded `extension_percent=…` unconditionally,
so an ATR-range rejection was filed with a percent measurement that had not
exceeded anything. `BreakoutAssessment.extension_reasons` carries the reasons and
`orb_breakouts.extension_reasons_json` persists them.

`extension_reasons` is deliberately separate from `rejection_reasons`: an
overextended breakout is **not rejected**, it is made to wait for a pullback. It
can never become research-ready on the breakout bar.

## 7. Replay performance

`event_rows_read` should stay close to a session's event count. Growing as the
**square** of the symbol count is the signature of a per-symbol full-session
reload — the defect fixed in this review, and the reason
`REPLAY_SESSION_BATCHING_REQUIRED` remains open for the next phase. A regression
test asserts the service loads one symbol at a time.
