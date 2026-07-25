# Rubix Feed Connection Stability Report

**Root cause found and fixed in the collector source; unit-tested against a mock
WebSocket AND live-validated over the full 2026-07-21 EGX session.**

## ⭐ Live-session verdict (Phase 9/10, 2026-07-21) — read this first

The fix **materially improved connection stability but did NOT make Rubix
intraday data sufficient for the 60% Range gate.** With live data under a stable
connection, the binding constraint is now proven to be **upstream per-symbol
update frequency**, which no further collector change can overcome.

| Metric | Before fix (best session) | After fix (2026-07-21, full session) |
|---|---|---|
| Market-wide connection uptime | 43–53% | **73%** ✅ improved |
| Disconnects that reconnected | 124 / 186 (62 failed) | **19 / 19** ✅ all recovered |
| Max market-wide silence | 77 min | 5 min in-session (14 min post-close) |
| Per-symbol coverage — median / max | ~27% / 29% | **23% / 25.6%** (≈ unchanged) |
| Symbols ≥ 60% / 80% / 90% | 0 / 0 / 0 | **0 / 0 / 0** |

Pre-declared acceptance targets (uptime ≥90%, silence ≤2min, some symbols ≥60%):
**NOT met.** The reconnect target (feed recovers after every drop) **was met.**

**Why coverage did not improve despite better uptime:** market-wide uptime rose
to 73% but per-symbol coverage stayed ~23%. The ratio (per-symbol ÷ market-wide
≈ 0.32) means the feed delivers **subsets** of symbols per burst — a given symbol
is only updated in ~23–26% of minutes even while the socket is healthy.
Extrapolating to a hypothetical 100% uptime lifts per-symbol coverage only to
~30%, still far below the 60% gate. This is **upstream feed sparsity at the
per-symbol level (SERVER_FEED_SPARSE)** — Mubasher does not push each symbol
frequently enough — and is not fixable by the collector.

**Consequence:** Rubix intraday remains insufficient for the Range Scanner (0
symbols pass, unchanged). The Range Backtest and live paper testing stay
correctly gated. The 60% threshold was **not** lowered. The realistic path to
dense/fresh EGX data remains a paid API (e.g. Mansa Pro); Rubix stays valuable
as a live *quote* overlay but cannot build dense daily/range candles.

_(Original pre-live text retained below for the record.)_

---

**Root cause found and fixed in the collector source; unit-tested against a mock
WebSocket.**

This is a **COLLECTOR ROOT-CAUSE FIX** (not merely supervisor mitigation). No
EGX AI Trader strategy, threshold, indicator, backtest, or replay was changed.

---

## Root cause (the alive-but-dead-feed defect)

In `rubix_feed/adapter.py`, three things combined to let the collector sit
**alive with a dead feed, forever, without reconnecting**:

1. **Library keepalive was disabled** — `websockets.connect(..., ping_interval=None)`.
   With no WS-level PING/PONG, a half-open TCP socket (server/proxy/NAT drops the
   stream without a FIN) is never detected by the library, so no
   `ConnectionClosed` is raised.
2. **The application heartbeat was fire-and-forget** — `_heartbeat` sent
   `{"MT":0}` every 15 s but never checked that the server's PULSE reply still
   arrived. Sending into a dead socket can succeed for a long time.
3. **The stale monitor was observational only** — it logged `stale_start`
   (1,800+ per session in the audit) but **never closed the socket or forced a
   reconnect**. Meanwhile `async for frame in socket` blocks with no timeout, so
   `_connected_session` never returns and `_reconnect_loop` never fires.

Net effect: on a silent stall there was **no disconnect event and no reconnect** —
the feed went dark market-wide until the socket eventually hard-errored (minutes
to over an hour) or the supervisor restarted the process. This exactly matches
the audit: a 77-minute market-wide outage, ~50% uptime, and sessions with *more*
hard-disconnects (07-16) paradoxically achieving *better* coverage than sessions
that stalled silently.

## The fix (minimal, config-gated, in the collector)

`rubix_feed/adapter.py` (+ `cli.py` args), original backed up to
`adapter.py.orig-backup-20260721`:

1. **Re-enabled library keepalive**: `ping_interval=20 s`, `ping_timeout=20 s`.
   A dead socket now raises `ConnectionClosed` → existing reconnect fires.
2. **Added an application liveness watchdog** (`_liveness_watchdog`): tracks the
   monotonic time of the last frame of *any* kind (market data **or** server
   PULSE). If nothing arrives for `liveness_timeout_seconds` (default 45 s;
   server pulses normally arrive ~15 s), it records `liveness_timeout` +
   `disconnect`, closes the socket, and drops into the reconnect path — catching
   the case where the socket answers pings but the market-data stream has
   stalled.
3. **Resubscription is verified on every reconnect** — `_connected_session`
   already re-sends the complete symbol universe on each connect; a new
   `resubscribe_complete` metric now records this once per connect so partial
   restoration is detectable.
4. **Accurate telemetry**: watchdog-forced drops are now counted as
   `disconnect` + `reconnect_success`, not silent clean returns.

Defaults apply automatically on the next collector restart; no supervisor change
is required, though the three controls are also exposed as CLI flags
(`--ws-ping-interval`, `--ws-ping-timeout`, `--liveness-timeout-seconds`).

## Validation performed (mock, deterministic)

`test_rubix_connection_stability.py` — **7 passed** (no network):
- library keepalive is enabled (regression guard for the exact defect),
- **silent stall → watchdog fires → reconnect** (`liveness_timeout` ≥ 1,
  connects ≥ 2, `reconnect_success` ≥ 1),
- **full resubscription each connect** (`resubscribe_complete` == connect count),
- **server close → reconnect** with resubscription,
- quotes stored when data flows,
- **never more than one socket open at once** (no duplicate live connections),
- clean shutdown records stop.

EGX AI Trader regression suite: unchanged (collector is a separate project) —
see final line.

---

## The 14 questions

1. **Where is the external collector?** `C:\Users\ahgza\OneDrive\Documents\Scrapping\rubix_feed`
   (`python -m rubix_feed.cli`), writing `D:\EGX_AI_Trader\data\rubix_live_market.db`.
   See [RUBIX_COLLECTOR_LOCATION_AUDIT.md](RUBIX_COLLECTOR_LOCATION_AUDIT.md).
2. **Was source available?** **Yes** — root-cause fix was possible.
3. **What caused the 77-minute outage?** A silent feed stall with no liveness
   watchdog and disabled keepalive: the socket stayed "open" while no frames
   arrived, and nothing forced a reconnect until a hard TCP error/supervisor
   restart.
4. **Why did 62 disconnects fail to reconnect?** Many "outages" were **not clean
   disconnects at all** — they were silent stalls that never raised an exception,
   so the reconnect loop never ran. The fix converts these into detected
   disconnects that reconnect.
5. **Auth/session expiry involved?** Not identified as the primary cause in the
   telemetry (no auth-rejection storm). Auth expiry is a *possible* secondary
   trigger; the collector already raises on auth rejection/timeout. A cleaner
   `AUTH_EXPIRED_REQUIRES_USER` signal is a recommended follow-up (see below).
6. **Did reconnect restore all 265 subscriptions?** Yes by design and by test —
   the full universe is re-sent on every connect (`resubscribe_complete` == connects,
   `subscription_rejected` = 0 historically). Live per-symbol confirmation is a
   Phase-9 item.
7. **Was an alive-but-dead state possible?** **Yes — that was the defect.** The
   watchdog + keepalive eliminate it (proven in the silent-stall test).
8. **What collector changes were made?** Re-enabled WS keepalive; added a
   liveness watchdog that forces reconnect on total-silence; added
   `resubscribe_complete`/`liveness_timeout` telemetry and accurate
   disconnect/reconnect accounting. Minimal, config-gated, original backed up.
9. **What supervisor mitigation was added?** None was needed for the root cause;
   the existing supervisor restart remains as a backstop. **Recommended** (not
   yet implemented): have the supervisor also restart when market-wide quote
   timestamps stop advancing during an open session (dead-feed detection),
   independent of process liveness — this is *mitigation*, not a root-cause fix.
10. **Full-session uptime after the fix?** **Unknown — not yet measured.**
    Requires a live EGX session (market closed now). Cannot be claimed.
11. **Symbols above 60/80/90% after the fix?** **Unknown — pending live session.**
12. **Symbols passing the unchanged Range Scanner?** **Still 0 today** — no live
    session has run under the fixed collector yet; the scanner is unchanged.
13. **Data sufficient for Range Backtest / live paper testing?** **Not yet** —
    conditional on a live session proving ≥60% coverage. Do not proceed until then.
14. **Did any strategy/indicator/threshold change?** **No.** Only the external
    collector was changed. The 60% gate, spread/liquidity thresholds, Swing,
    Breakout, Adaptive, AI, indicators, backtest, and replay are untouched.

---

## Honest status & next step (must run during a live EGX session)

- **COLLECTOR ROOT-CAUSE FIX**: implemented + unit-tested. ✅
- **SUPERVISOR MITIGATION**: existing restart retained; dead-feed-aware restart
  recommended. ⚠️ (mitigation, not proof of fix)
- **LIVE VALIDATION (Phase 9) + RANGE REVALIDATION (Phase 10)**: **pending the
  next EGX session (Sun–Thu 10:00–14:30 Cairo).** Acceptance targets to declare
  *before* that run: connection uptime ≥ 90%, market-wide max silence ≤ ~2 min,
  and a real coverage distribution showing some symbols ≥ 60%.

**Recommended before the next session:** restart the collector so it loads the
patched `adapter.py` (defaults enable the fix), confirm only **one** collector
instance is running, then capture the live checkpoints. Until that live run
exists, the fix is *believed correct and unit-proven* but **not confirmed** —
and this report does not claim the problem is solved.

## Follow-ups requiring the external collector owner / more work
- `AUTH_EXPIRED_REQUIRES_USER` explicit terminal signal (Phase 5) instead of a
  generic reconnect on auth rejection.
- Full structured event taxonomy (Phase 7) — a subset is added; the rest
  (CONNECT_ATTEMPT, SUBSCRIBE_ACK, FIRST_MARKET_EVENT, …) can be layered on.
- Supervisor dead-feed restart trigger (Phase 6).
