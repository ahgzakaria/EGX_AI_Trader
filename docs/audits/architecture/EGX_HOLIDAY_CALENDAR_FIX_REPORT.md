# EGX Trading-Calendar Fix — 23 July 2026 Revolution Day Holiday

**Date:** 2026-07-23 · **Scope:** calendar / session-classification / UI semantics only.
No strategy, indicator, ranking, scoring, quote-freshness threshold, TP/SL, Rubix
collector, database content, provider, or execution flag was changed. Production stays
disabled. Verified: **472 tests pass** (13 new holiday-calendar tests + the existing
suites).

---

## 1. Why was 2026-07-23 classified OPEN?

`core.egx_session.egx_session_phase()` checked only **weekday vs weekend** and the
**time of day** — it never consulted a holiday list (it did not even accept one). At
11:45 Cairo on a Thursday it therefore returned `OPEN`. Compounding this, the whole
module defaulted `holidays=()` ("caller must pass a known set; holidays are never
guessed"), and the two live-UI callers that mattered —
`dashboard/expected_range_scalper._session_phase()` and the launcher/provider status —
passed **no** holidays. So even after `2026-07-23` was added to config, the phase logic
ignored it. Result: `EGX OPEN` + `LIVE_QUOTE_STALE` on a closed-market day.

## 2. Where is the centralized holiday source?

New module **`core/egx_calendar.py`** — the single shared calendar. It merges a curated
built-in map (`date -> holiday_name`, currently `2026-07-23 → "23 July Revolution Day"`)
with operator/exchange-configured dates from `settings['rubix_daily_bridge']['holidays']`.
API: `holiday_dates()`, `holiday_name(day)`, `is_official_holiday(day)`,
`effective_holidays()`. Every session helper in `core/egx_session.py` now **defaults**
to this calendar (`holidays=None` → `egx_calendar.holiday_dates()`); the six scheduled
tasks and the launcher source their holiday list from `effective_holidays()` /
the calendar default. No page or script hardcodes holiday logic separately.

Supported closure sources (one calendar): fixed/curated official holidays, operator- or
exchange-announced dates, explicit exceptional closures, and the Friday/Saturday EGX
weekend (weekend handled by `egx_session`).

## 3. What session state is returned now?

For 2026-07-23 (verified against a fixed 11:45 Cairo datetime and live in the app):

| Field | Value |
|-------|-------|
| `egx_session_phase` / `_auction_phase` | **HOLIDAY** (at 10:30 and at 14:20 — never OPEN/auction) |
| `is_regular_trading_day` | **False** |
| continuous session / closing auction / new entries | **not active / not active / not allowed** (`live_actionability` → MARKET_CLOSED) |
| holiday name | **23 July Revolution Day** |
| next trading session | **2026-07-26** |

UI: Dashboard and Opportunities show a **🕌 EGX HOLIDAY · 23 July Revolution Day ·
Market Closed · Next Session: 26 Jul 2026** banner and an `EGX HOLIDAY` status chip;
Opportunities' first tab reframes to **آخر فرص مسجلة (Last Observed Opportunities)** with
"NOT currently actionable", **no active Live-Quote-Stale warning**; the Scalping
Dashboard shows **Value: Idle (holiday)** and history health **CURRENT**.

## 4. What is the next trading session?

**2026-07-26 (Sunday).** `next_trading_session(2026-07-23) == 2026-07-26`, skipping the
holiday and the Friday/Saturday EGX weekend. (`next_trading_session(2026-07-24)` also
returns 2026-07-26.)

## 5. Were any false paper sessions created?

No. The paper daily summary row for 2026-07-23 is
`Classification=NON_TRADING_DAY, CountsTowardForwardMinimum=False, IsPilot=False,
MonitorRan=False, ReadySignals=0, Outcomes=0` — **not** PILOT/PARTIAL/COMPLETE. No READY
activations, scenario transitions, or outcome horizons were created for the holiday. The
only artifact is the intended small `NON_TRADING_DAY` status manifest/summary row.

## 6. Were any tasks incorrectly run?

No. All holiday-gated scheduled tasks detect the holiday from the shared calendar and
exit cleanly (**exit code 0**), creating no trading artifacts:

| Task | Holiday result |
|------|----------------|
| Pre-session snapshot | `{"snapshot_status": "NON_TRADING_DAY"}` · exit 0 |
| Live paper monitor | `{"status": "NON_TRADING_DAY", "skipped": …}` · exit 0 *(was exit 2 "error"; fixed to a clean skip)* |
| Outcome finalizer | classifies session **NON_TRADING_DAY** · exit 0 |
| Rubix daily finalizer | `{"status": "NON_TRADING_DAY"}` · exit 0 |
| Session validator | resolves to the last real session (07-22) · exit 0 |

Gating uses `is_regular_trading_day(..., effective_holidays())`, so the tasks are not
reliant on Sun–Thu scheduling alone (a holiday on a normal weekday is caught).

## 7. Is 2026-07-22 history correctly CURRENT?

Yes. On the holiday `expected_latest_session_date` and `expected_latest_completed_session`
both return **2026-07-22**, `trading_session_lag(2026-07-22)` is **0**, and
`classify_history_freshness("2026-07-22")` returns **HISTORY_CURRENT / is_current=True /
lag_sessions=0 / phase=HOLIDAY**. No 2026-07-23 daily candle is expected; the next
expected completed session becomes 2026-07-26 only after that session finishes. The
launcher/dashboard show Daily Bridge FINAL **2026-07-22** and history health **CURRENT** —
no false one-session lag.

## 8. Did any strategy, threshold, provider or execution flag change?

No. Changes are limited to: the new `core/egx_calendar.py`; holiday-awareness in
`core/egx_session.py` (default `holidays=None` → shared calendar, `HOLIDAY` phase,
`next_trading_session`, holiday-skipping lag/expected-session); a reusable
`egx_holiday_banner()` + `HOLIDAY` labels in `dashboard/ui.py`,
`dashboard/expected_range_scalper.py`, `dashboard/scalping.py`,
`dashboard/opportunities.py`; the six scheduled tasks + launcher sourcing holidays from
the shared calendar; the live monitor's clean holiday exit; and `config/settings.json`
holiday entry `2026-07-23`. Quote-freshness thresholds, scoring, ranking, TP/SL, the
Rubix collector, provider selection, database contents, and the paper/production flags
are unchanged; no automatic execution was enabled; **production remains disabled**.

---

### Real-app validation (2026-07-23, ~11:45 Cairo, live)
- Opportunities: banner **EGX HOLIDAY · 23 July Revolution Day**, chip **EGX HOLIDAY**,
  first tab **آخر فرص مسجلة**, "Market closed — … NOT currently actionable", empty-state
  reasons = Low liquidity + History lag only (**no Live-Quote-Stale**), next session
  **26 Jul 2026**.
- Scalping Dashboard: banner + **EGX HOLIDAY** chip, **Value: Idle (holiday)**, Daily
  Bridge FINAL **2026-07-22**, history health **CURRENT**, snapshot **NON_TRADING_DAY**.
- Screenshot: the in-app browser pane cannot composite frames, so `computer{screenshot}`
  times out (unchanged environment limit); validated via live page-text instead.

### Tests
`tests/test_egx_holiday_calendar.py` (13): holiday classification; no OPEN at 10:30 / no
auction at 14:20; next session = 2026-07-26; latest completed = 2026-07-22; prev-session
HISTORY_CURRENT; holiday quote usable (no degraded warning); pre-session/daily-finalizer
gates NON_TRADING_DAY; banner shows only on a holiday. Full regression: **472 passed**.
