# TickerChart EGX Entitlement Validation

**Validation date:** 2026-07-13 (Africa/Cairo)  
**Observation window:** 00:16:36–00:26:52 Cairo (10 minutes 16 seconds)  
**Scope:** Read-only entitlement and timestamp validation; no EGX AI Trader configuration or code changed

## Executive verdict

**B. Safe only as delayed secondary provider**

The authenticated account is the **Free** plan and the live application displays
an explicit account-level warning that all displayed data is delayed by 15
minutes. Quotes and daily/intraday candles are available. Time & Sales, trade
summary, and both Market Depth views are locked and not entitled.

TickerChart contained the 2026-07-12 EGX session while Yahoo's tested symbols
ended at 2026-07-09, so it is newer than Yahoo for end-of-day completeness.
However, this entitlement must not be treated as real-time and should not become
the Scanner/Dashboard default for a real-time workflow.

## Authentication status

Authentication was confirmed from the application itself:

- The previous `Guest` marker was absent.
- The footer displayed `Logout` and `My Account`.
- The account tier displayed `Free`.
- The authenticated feed displayed both:
  - `Delayed data`.
  - `All this data is delayed by 15 minutes from the market`.

This is observed account state, not an inference from marketing text.

## Account entitlement observed

| Surface | Availability | Observed entitlement |
|---|---|---|
| Detailed quotes | Available | Delayed |
| Bid / Ask | Available | Delayed |
| Intraday candles | Available (1, 5, 10, 15, 20, 30 minutes, 1 hour, 4 hours) | Delayed |
| Daily candles | Available | Delayed during the session; completed session present |
| Time & Sales | Locked | Not entitled |
| Trade summary | Locked | Not entitled |
| Market Depth by price | Locked | Not entitled |
| Market Depth by order | Locked | Not entitled |

The locked surfaces were identified from their visible inactive state and lock
indicator in the authenticated application.

## Symbols tested

Three actively traded EGX symbols were inspected through the Detailed Quote
screen. Receipt time is the local Cairo time at which the displayed values were
read. Because the market was already closed, the age shown below is the age of
the last exchange trade at receipt time, not an independent measurement of
in-session dissemination latency.

| Symbol | Last | Exchange date/time shown | Cairo receipt time | Bid | Ask | Volume | Delay warning |
|---|---:|---|---|---:|---:|---:|---|
| COMI | 134.52 | 2026-07-12 14:28:47 | 2026-07-13 00:16:36 | 134.52 | 134.90 | 954,219 | Visible: 15 minutes |
| SWDY | 88.77 | 2026-07-12 14:29:38 | 2026-07-13 00:18:33 | 88.22 | 88.95 | 121,544 | Visible: 15 minutes |
| FWRY | 19.34 | 2026-07-12 14:29:25 | 2026-07-13 00:19:55 | 19.29 | 19.34 | 2,742,266 | Visible: 15 minutes |

The displayed timestamps are mutually consistent with late-session EGX trades.
The local receipt times are approximately 9 hours 48–50 minutes later because
the observation occurred after midnight, following market close.

## Side-by-side comparison

Mubasher was attempted on the same machine, but its stock page redirected to a
login screen. No unauthenticated Mubasher value was used or inferred. Yahoo was
therefore used as the available comparison source through the existing local
EGX AI Trader loader.

| Symbol | TickerChart last | TickerChart session | Yahoo last | Yahoo session | Freshness result |
|---|---:|---|---:|---|---|
| COMI | 134.52 | 2026-07-12 | 134.50 | 2026-07-09 | TickerChart newer by one EGX session |
| SWDY | 88.77 | 2026-07-12 | 89.00 | 2026-07-09 | TickerChart newer by one EGX session |
| FWRY | 19.34 | 2026-07-12 | 19.36 | 2026-07-09 | TickerChart newer by one EGX session |

The small price differences are expected because the sources represent
different completed sessions. They are not evidence of a pricing discrepancy.

## Ten-minute observation

The observation ran for **616 seconds**. FWRY remained unchanged at:

- Last: 19.34
- Bid: 19.29
- Ask: 19.34
- Volume: 2,742,266
- Exchange time: 14:29:25 on 2026-07-12

This stability is expected after market close and cannot be used to prove or
disprove a 15-minute in-session delay. The entitlement is classified as delayed
because the authenticated application itself exposes the 15-minute warning,
not because the closed-market quote was static.

## Quotes, candles, prints, and depth

### Quotes

Available for all three tested symbols with last price, last size, bid, ask,
bid/ask sizes, total bid/ask volume, session volume, date, and time. The visible
15-minute warning applies.

### Intraday candles

One-minute candles were successfully selected and rendered. Additional
intraday intervals were visible. The chart continued to display the delayed-data
warning. A live-market propagation measurement was not possible after close.

### Daily candles

Daily candles were available and the completed 2026-07-12 session was present.
For the tested symbols this was one EGX session newer than Yahoo.

### Time & Sales

Not entitled. The control was inactive and displayed a lock indicator.

### Market Depth

Neither price-aggregated depth nor order-level depth is entitled. Both controls
were inactive and displayed lock indicators.

## Feed classification and consistency

**Classification: Delayed (explicitly 15 minutes).**

The three quotes were internally consistent:

- All belonged to the same 2026-07-12 session.
- All showed late-session trade timestamps.
- Bid, ask, last, and daily range values were coherent.
- Daily data freshness was consistently ahead of Yahoo by the same session.

No evidence was found that this account receives real-time EGX quotes.

## Limitations

1. Validation occurred after EGX market close, so the stated 15-minute delay
   could not be independently timed against live exchange events.
2. Mubasher required authentication and could not be used for a live timestamp
   comparison.
3. Rubix was not available through the accessible browser session.
4. Time & Sales and Market Depth were not entitled, preventing print-by-print
   or order-book comparison.
5. This validation concerns account entitlement only. It does not establish an
   official structured API contract or authorize browser scraping.

## Recommendation

- Do **not** set TickerChart as the default Scanner/Dashboard provider for a
  real-time workflow under this account.
- It may be useful as a delayed secondary/EOD freshness source because it
  supplied a completed session that Yahoo had not yet published.
- Any integration must still use an authorized structured endpoint or official
  adapter. The observed web application must not be scraped.
- Keep `scanner_provider` and `dashboard_provider` unchanged until the user
  explicitly approves a delayed-secondary design or upgrades to a verified
  real-time EGX entitlement.

## Configuration status

No provider configuration, Scanner code, Dashboard code, strategy logic, AI,
Backtest, Forward Testing, or Experiment Tracking file was modified during this
validation.
