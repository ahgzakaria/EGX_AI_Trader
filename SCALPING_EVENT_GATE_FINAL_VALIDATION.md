# Scalping Event-Gate — Final Validation & 317-Second Outage Audit

**Headline:** The residual 317-second blocker on 2026-07-21 was **not** a
collector fault. The connection was healthy throughout; the patched collector was
provably active and correct. The 317 s was an **upstream Mubasher exchange-timestamp
staleness**. Consequently **no collector fix was applied** (none is timeline-supported),
**no threshold or strategy was changed**, and the event gate stays **shadow /
paper-only** pending multi-session evidence (only one patched session exists so far).

---

## Phase 2 — Where the 317 seconds were spent

Reconstructed from real `feed_metrics` at millisecond resolution
(`reports/rubix_317_second_outage_timeline.csv`). The "outage" is the exchange
`market_timestamp` not advancing from **10:17:02 → 10:22:19 Cairo**.

| Component | Seconds | Evidence |
|---|---:|---|
| Detection latency | 0 | connection healthy — nothing to detect |
| Socket cleanup | 0 | no socket close |
| Reconnect backoff | 0 | **0 disconnect** in the window |
| WebSocket connect | 0 | no reconnect |
| Authentication | 0 | no re-auth |
| Resubscription | 0 | no resubscribe in the window |
| Waiting for first market event | 0 | frames were flowing the whole time |
| **Upstream Mubasher `market_timestamp` staleness** | **317** | **4,178 quotes received**, 1,626 unchanged/duplicate snapshots, **max frame gap 3 s** |

The feed delivered **4,178 frames in 317 s** (~13/s) with a maximum inter-frame
gap of **3 s**, but every frame carried the **same frozen exchange timestamp**.
The collector correctly de-duplicated them (so no new `quotes`/`candles` rows),
which is why the `market_timestamp` timeline shows a gap while the connection was
never down.

## Phase 3 — The patched collector was definitely active

- **26 `resubscribe_complete` events on 07-21** — a metric that exists **only in
  the patched `adapter.py`**. The old code could not emit it. ⇒ patched code ran.
- `adapter.py` on disk contains the keepalive (`ping_interval=ws_ping_interval`),
  the `_liveness_watchdog`, and `resubscribe_complete` (verified).
- The watchdog (45 s no-frame) **correctly did not fire** — frames arrived every
  ≤3 s, so the connection was genuinely alive; firing would have been wrong.
- One logical collector (supervisor → collector → worker process tree); a single
  clean reconnect at 10:15:18 (one `connected`, one `reconnect_success`, three
  `subscription_batch_sent`, one `resubscribe_complete`) — **no overlapping
  reconnect loops, no stale old-adapter process**.

## Phase 4 — Minimal collector fix: NONE (not timeline-supported)

Every candidate fix (immediate reconnect, bounded close, backoff reset, supervisor
guard, faster resubscription) addresses **reconnect/recovery** latency. The
timeline shows **zero** reconnect/recovery time — the connection never dropped.
Forcing a reconnect during a healthy connection because the *upstream* stopped
advancing its timestamp would add instability without recovering data (the data
simply did not exist upstream yet). **No collector change was made.** The 317 s is
a **Mubasher feed characteristic**, not a collector defect.

## Phase 5 — Multi-session status (honest)

`reports/scalping_event_gate_multisession.csv`. **Only ONE patched-collector
session exists (2026-07-21).** Sessions 07-14…07-20 predate the connection patch
(`adapter.py` patched 2026-07-21 00:51) and are marked `PRE-FIX` — they are not
valid evidence for the patched collector. Per the task's own rule, **validation
cannot be claimed from one session.**

## Phase 6 — Paper activation gate (built, records 0 today)

`scalping/event_paper_gate.py`: a signal is permitted for **paper-only** recording
only when session-phase is valid, the max continuous fatal outage ≤ 300 s,
EVENT_DATA_VALID, RANGE_CONFIRMED, fresh quote, acceptable spread/liquidity, and a
valid entry/stop/targets/net-RR. An append-only store records signals **before**
outcomes and never rewrites them (outcomes at 1/3/5/10/20 min are appended
separately). On 07-21 the gate permits **0** recordings (0 RANGE_CONFIRMED — the
317 s outage exceeds 300 s by 17 s). 14 tests pass.

---

## The 10 questions

1. **Where were the 317 seconds spent?** Entirely upstream — Mubasher held the
   exchange `market_timestamp` frozen for 317 s while the socket stayed healthy
   (4,178 frames received, ≤3 s apart). 0 s in detection/reconnect/auth/resub.
2. **Was the patched collector definitely active?** **Yes** — 26 patched-only
   `resubscribe_complete` events; watchdog/keepalive present; single clean collector.
3. **Was any reconnect delay avoidable?** **No reconnect occurred** — the
   connection never dropped, so there was no reconnect delay to avoid.
4. **What minimal collector fix was applied?** **None.** The timeline supports no
   collector-side fix; the gap is an upstream feed characteristic.
5. **Max outage in each new session?** Only one patched session: **07-21 = 317 s**
   (upstream staleness). No other patched sessions yet.
6. **RANGE_CONFIRMED per session?** 07-21: **0** (17 s short). No other patched sessions.
7. **Paper opportunities recorded?** **0** — the gate correctly permits none (0
   RANGE_CONFIRMED).
8. **Persisted across multiple sessions?** **Unknown** — only one patched session
   exists; cannot assess persistence.
9. **Is paper forward testing recommended now?** **No.** Acceptance needs ≥3 live
   sessions with ≥2 producing RANGE_CONFIRMED symbols and recovery consistently
   <300 s; today = 1 session, 0 RANGE_CONFIRMED. **Not yet.**
10. **Did any strategy or threshold change?** **No.** 300 s gate, 60% legacy
    diagnostic, event-quality thresholds, Entry/Stop/Target/RR, Swing/Daily,
    Classic, Breakout, Adaptive all unchanged.

## Recommendation

Keep the legacy gate as production and the event gate **shadow / paper-only,
disabled**. Collect **at least 3 patched-collector sessions**. The key open
question is **not the collector** (it works) but whether Mubasher's periodic
`market_timestamp` freezes stay ≤ 300 s often enough that some liquid symbols
reach RANGE_CONFIRMED across multiple sessions. If they do (≥2 of ≥3 sessions),
paper forward testing becomes justified — with **no threshold or strategy change**.
