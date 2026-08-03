# Phase 2A — Session Phase Correctness Review

Every boundary below was probed with timezone-aware `Africa/Cairo` timestamps at
**one microsecond before, exactly at, and one microsecond after**. Results are from a live
probe against `scalping_orb.session.OrbSessionClassifier`, not from reading the code.

---

## 1. Boundary sweep — all correct

| Boundary | `-1 µs` | exactly at | `+1 µs` |
|---|---|---|---|
| 10:00:00 `continuous_start` | `PRE_SESSION` | `OPENING_RANGE_BUILDING` | `OPENING_RANGE_BUILDING` |
| 10:15:00 `opening_range_end` | `OPENING_RANGE_BUILDING` | `CONTINUOUS_AFTER_OPENING_RANGE` | `CONTINUOUS_AFTER_OPENING_RANGE` |
| 13:30:00 `late_continuous_start` | `CONTINUOUS_AFTER_OPENING_RANGE` | `LATE_CONTINUOUS` | `LATE_CONTINUOUS` |
| 14:15:00 `continuous_end` | `LATE_CONTINUOUS` | `CLOSING_AUCTION` | `CLOSING_AUCTION` |
| 14:25:00 `auction_end` | `CLOSING_AUCTION` | `POST_MARKET` | `POST_MARKET` |

Every interval is half-open `[start, end)`. `[10:00, 10:15)` is therefore exactly the opening
range: 10:14:59.999999 is still inside it and 10:15:00.000000 is not.

**Now covered by test:** `test_every_boundary_is_half_open_to_the_microsecond` (parametrised
over all five boundaries). The pre-existing suite probed at whole-second resolution only.

---

## 2. Opening-range completion timing

| Requirement | Result |
|---|---|
| The 10:14 minute bar completes at 10:15 | Confirmed — `bar_end_utc == 10:15:00`, and the bar is emitted only when `bar_end <= as_of` |
| No `READY` result before that completion | Confirmed — at `10:15:00 − 1 µs` the status is `BUILDING` with 14 of 15 slots; at `10:15:00` it is `READY` with 15 |
| 10:15 enters continuous-after-opening-range | Confirmed |

`build_opening_range` guards this twice: an explicit `evaluated < window.opening_range_end_utc →
BUILDING` return, *and* a per-slot `by_slot[slot].bar_end_utc <= evaluated` filter. Either alone
would be sufficient; both together mean a partial final minute cannot leak in even if a caller
passes a hand-built bar list.

---

## 3. Late-continuous boundary is configuration-driven

`late_continuous_start` is read from `OrbDataConfig`, not from a literal. Verified by
constructing a config with `late_continuous_start=12:00` and confirming the phase flips at
12:00 rather than 13:30 — `test_late_continuous_boundary_follows_configuration_not_a_literal`.

`config.__post_init__` enforces
`continuous_start < opening_range_end <= late_continuous_start < continuous_end < auction_end`,
so a misconfiguration fails at construction rather than producing a silently reordered session.

---

## 4. Non-trading dates

Calendar validity is evaluated **before** any time-of-day logic:

```
if not is_regular_trading_day(local.date(), self.holidays):
    return OrbSessionPhase.NON_TRADING_DAY
```

A Friday, a Saturday or a configured EGX holiday therefore returns `NON_TRADING_DAY` even at
11:00 Cairo, and can never be mistaken for a normal session. Holidays default to the shared
`core.egx_calendar`, so this module cannot drift from the rest of the system.

Confirmed against real data: **all 13 observed Rubix dates are regular EGX trading days.** No
weekend or holiday contamination exists in the current evidence base.

> Latent gap, in the audit script rather than the classifier:
> `inspect_orb_rubix_readiness.py::_candidate_dates` takes every distinct date from `candles_1m`
> without calling `is_regular_trading_day`. Today this is harmless because all 13 dates are valid,
> but a stray weekend row would silently become a "session". Recorded in the gate list.

---

## 5. Timezone and DST determinism

Egypt observes DST (last Friday in April → last Thursday in October). Verified across both 2026
transitions:

| Session date | UTC offset | 10:00 Cairo → UTC | 14:25 Cairo → UTC | Span |
|---|---|---|---|---|
| 2026-04-23 (Thu) | +02:00 | 08:00Z | 12:25Z | 265 min |
| 2026-04-26 (Sun) | +03:00 | 07:00Z | 11:25Z | 265 min |
| 2026-10-28 (Wed) | +03:00 | 07:00Z | 11:25Z | 265 min |
| 2026-11-01 (Sun) | +02:00 | 08:00Z | 12:25Z | 265 min |

The session is defined in Cairo wall time and converted per session date, so the trading day is
always exactly 265 minutes regardless of offset. The transitions themselves occur around
midnight, outside trading hours, so no session ever contains a fold or a gap — there is no
ambiguous-time path to get wrong. Cairo offsets are also whole hours, which is why the
minute-bucketing in `bars.py` is safe to compute on local wall time.

`ZoneInfo(self.timezone)` is constructed in `config.__post_init__`, so an unknown timezone key
fails at configuration time rather than at the first classification.

**Now covered by test:** `test_session_window_is_deterministic_across_the_egypt_dst_change`.

---

## 6. Naive datetimes are never accepted silently

`require_aware` raises `ValueError("… must be timezone-aware")` rather than assuming UTC.
Verified live on every entry point:

| Entry point | Naive input |
|---|---|
| `OrbSessionClassifier.classify` | rejected |
| `OrbSessionClassifier.session_date` / `to_cairo` / `is_continuous` | rejected |
| `RubixEventNormalizer.normalize` — `market_timestamp` | rejected |
| `RubixEventNormalizer.normalize` — `receive_timestamp` | rejected |
| `RubixEventNormalizer.normalize` — `evaluated_at` | rejected |
| `aggregate_completed_one_minute_bars` — `as_of` | rejected |
| `aggregate_completed_five_minute_bars` — `as_of` | rejected |
| `build_opening_range` — `as_of` | rejected |
| `OrbShadowIngestionService.ingest` — `evaluated_at` | rejected |

The error names the offending field, so a caller cannot be left guessing which timestamp was
naive.

---

## 7. Auction containment — verified end to end

Requirement: auction ticks must not enter one-minute continuous bars, five-minute continuous
bars, the opening range, or quote-freshness metrics for continuous trading.

| Sink | Result | Mechanism |
|---|---|---|
| One-minute continuous bars | **Contained** | `event.session_phase not in CONTINUOUS_PHASES → continue`, with an explicit `AUCTION_EVENT_EXCLUDED` quality event so the exclusion is auditable rather than silent |
| Five-minute continuous bars | **Contained** | Auction events never form 1-minute bars, so no component can exist; a second guard rejects any non-continuous component with `NON_CONTINUOUS_COMPONENT_EXCLUDED` |
| Opening range | **Contained** | Candidates are filtered to `[continuous_start, opening_range_end)`, and any bar phased `CLOSING_AUCTION` forces `AUCTION_CONTAMINATION_REJECTED` — a rejection, not a downgrade |
| Capability snapshot (`current_bid`/`current_ask`/spread) | **Was leaking — now fixed** | See below |

Empirical probe with a 14:10–14:19 quote stream: 1-minute bars are produced for 14:10–14:14
only, and the single 5-minute bucket is 14:10–14:15. Nothing at or after `continuous_end`
14:15 forms a bar, and no 5-minute bar bridges continuous trading into the auction.

### The leak that was found

`shadow.py` selected the capability snapshot's `latest_event` as the maximum by market timestamp
across **all** persisted events. Reproduced directly: a 14:20 auction quote at bid 98.00 /
ask 100.00 was reported as `current_bid=98.0`, `current_ask=100.0`,
`spread_status=SPREAD_AVAILABLE` — presented as the current continuous market, and persisted
into `orb_capabilities`. A Phase 2B renderer reading that row would have shown an auction print
as the live quote.

Fixed: the snapshot now selects from `CONTINUOUS_PHASES` events only, and reports `None` /
`SPREAD_UNAVAILABLE` / `VOLUME_UNAVAILABLE` when no continuous quote exists — rather than
falling back to the nearest available price. Proven by
`test_auction_quotes_never_become_the_current_continuous_market`, which also asserts against
the persisted row, not just the in-memory result.

### Remaining caveat, not a defect

`inspect_orb_rubix_readiness.py::_volume_audit` computes its freshness distribution over the
window `[continuous_start, auction_end)`, so auction rows are included. The generated report
labels this honestly ("Quote freshness from audited continuous/auction rows"), so nothing is
misrepresented — but the per-session `quote_freshness_*` columns in `orb_volume_quality.csv`
are not phase-separated and should not be read as a continuous-trading latency figure. A
phase split is recommended before those numbers inform any Phase 2B freshness budget.
