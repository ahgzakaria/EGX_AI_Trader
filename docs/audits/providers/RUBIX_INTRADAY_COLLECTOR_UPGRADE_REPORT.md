# Rubix Intraday Collector — Upgrade Report (Diagnosis & Honest Scope)

**Verdict:** The sparse intraday capture is a **feed connection-uptime problem**,
not a collector throughput problem and not per-stock liquidity. No collector
"fix" is claimed. No strategy or threshold was changed. The Range coverage gate
stays at 60%. Automatic trading remains off.

**Two hard scope limits (both real, both unavoidable in this pass):**
1. **The collector source is not in this repository.** It is an external program
   (`rubix_feed`: `adapter.py/cli.py/protocol.py/storage.py`) launched by the RC1
   supervisor against `wss://eg-feed3.mubashertrade.com`. This repo has no
   WebSocket client/parser/writer — it reads the DB `mode=ro` only. **Phase 4
   (rewrite the capture pipeline) cannot be done here** — the code to rewrite is
   absent.
2. **The market is closed** (past midnight Cairo; next session ~09 h away).
   **Phase 9 (live acceptance test) and Phase 10 (revalidation) cannot run now**,
   and the task itself forbids using an off-session run as the acceptance test.

Everything that *is* possible now — full telemetry diagnosis, subscription/
message/density audits, failure classification — was done from real data. See
[RUBIX_INTRADAY_CAPTURE_AUDIT.md](RUBIX_INTRADAY_CAPTURE_AUDIT.md) and:
`reports/rubix_subscription_coverage.csv`, `rubix_message_type_inventory.csv`,
`rubix_intraday_density_by_symbol.csv`, `rubix_intraday_density_by_session.csv`.

---

## Market-wide coverage by session (real)

| Session | Active min / 270 | Coverage | Bars/symbol | Disconnects | Reconnects | Median update |
|---|---:|---:|---:|---:|---:|---:|
| 2026-07-14 | 21 | 8% | 6.9 | 21 | 13 | 6.7 s |
| 2026-07-15 | 2 | 1% | 1.0 | 0 | 0 | — (collector idle) |
| 2026-07-16 | 142 | **53%** | 58.1 | 95 | 61 | 3.5 s |
| 2026-07-19 | 116 | 43% | 65.3 | 43 | 29 | 3.8 s |
| 2026-07-20 | 45 | 17% | 18.5 | 8 | 6 | 4.9 s |

Best market-wide coverage ever = **53%**. Per-symbol observation coverage on the
best-updated session (07-19): **median 26.7%, max 29.3%, zero symbols ≥ 40%**.
Note 07-16 had the *most* disconnects yet the *best* coverage, and 07-20 the
*fewest* disconnects yet poor coverage — so the cap is total *connected time*
(and partial runs), not disconnect count.

---

## The 12 questions, answered from real data

**1. Was the sparse data caused by the collector or Rubix upstream?**
**Neither in the way the task assumed.** Upstream delivers densely (~1 update/
3.8 s) *when connected*, and the collector writes faithfully (1 candle per active
minute, 794 quotes/min, 0 out-of-order). The loss is **connection uptime**: only
43–53% of session minutes receive any data market-wide, with silent gaps up to
77 minutes. Whether each disconnect originates at Mubasher (server closes the
socket) or in the external client's connection handling **cannot be determined
from the DB alone**, and that client's source is not in this repo — so the root
cause of the disconnects is classified **UNKNOWN (connection-level)**, pending a
live investigation of the external collector.

**2. Were all 265 symbols actually subscribed?**
**Yes.** 265/265 ever received a quote; `subscription_rejected` = 0;
`subscription_batch_sent` = 365 (batch size 100). Not a subscription problem.

**3. Were subscriptions retained after reconnects?**
Effectively yes for symbol coverage (all 265 keep receiving across the week), but
**~62 of 186 disconnects had no matching `reconnect_success`**, so after some
drops the stream stayed down — this is the source of the long market-wide
silences, not per-symbol subscription loss.

**4. How many live messages per minute were received?**
When connected: **~794 stored quotes/min** market-wide on 07-19 (638 on 07-16),
median **~2.3 stored quotes per symbol per active minute**. Throughput is healthy.

**5. Were any events dropped by queues or database writes?**
**No evidence of drops.** Candle count equals distinct quote-minutes for even the
most liquid symbols (AMER 4,627 quotes → 78 quote-minutes → 79 candles),
`out_of_order_timestamp` = 0 in-session. The database write path is not the
bottleneck.

**6. Did batching/indexing improve capture?**
**Not applicable / not attempted.** Batching and indexing address write
throughput, which is not the bottleneck (see #4–#5). Adding indexes to the
external, read-only Rubix DB is out of scope (must not modify it) and would not
raise coverage. No index was added.

**7. What is the real live-session coverage distribution?**
Market-wide 8%–53% across sessions (best 53%). Per-symbol (07-19): median 26.7%,
p-max 29.3%. Full per-symbol distribution in
`reports/rubix_intraday_density_by_symbol.csv`.

**8. How many symbols exceed 60%, 80%, 90% coverage?**
**60%: 0. 80%: 0. 90%: 0.** In every recent session. The market-wide ceiling
(53%) is below the per-symbol 60% gate, so no symbol can pass.

**9. How many symbols now pass the Range Scanner data gate?**
**0 — unchanged.** No collector change was made (and could not be, per scope), so
the Range Scanner result is identical to before: 265/265 `DATA_INSUFFICIENT`.

**10. Are live spreads materially different from the off-session scan?**
**Cannot be measured now** (market closed). The earlier off-session scan showed
median spread ~1.03% with only 70/265 ≤ 0.6%; a true live-spread comparison
requires a live session (Phase 9) and is deferred.

**11. Is the data sufficient to proceed with Range Backtest and Forward Testing?**
**No.** Per the task's own rule ("Do not build the Range Dashboard or Range
Backtest until at least some symbols pass the real data gate"), and since 0
symbols pass, Backtest/Forward-Testing remain correctly deferred.

**12. Did any strategy or threshold change?**
**No.** Range logic, thresholds, the 60% coverage gate, spread threshold, Classic
Swing, Breakout, Adaptive, AI, Backtest, Replay, and the fixed-2% scalping
strategy are all untouched. This task added only read-only diagnostic scripts,
report CSVs, and these two markdown reports. Full regression suite: **240 passed,
0 failed.**

---

## What must happen next (outside this repo / a live session)

The fix is **connection stability of the external `rubix_feed` collector**, not a
throughput rewrite. Concretely, during a live EGX session, investigate on the
collector side:
- WebSocket **keepalive/heartbeat** cadence vs Mubasher's idle-timeout (heartbeats
  are observed but disconnects still occur) — is the client pinging often enough?
- **Reconnect policy** — 62 disconnects had no successful reconnect; is backoff
  too slow / capped / giving up? Does it re-subscribe immediately on reconnect?
- Whether **Mubasher closes duplicate/idle connections** or rate-limits the
  free/demo feed (the 25–29% duplicate-snapshot rate suggests a snapshot-push
  feed that may cull connections).
- Whether the collector **process itself stayed up** for the full session (07-20's
  partial capture with few disconnects implies it did not run the whole session —
  check the RC1 supervisor restart/uptime logs).

Only after a live session where the collector holds the connection for ≥60% of
symbol-minutes should Phase 9/10 acceptance and the Range Backtest/Forward-Test
proceed. **Until then the honest status is: capture is connection-limited, no
symbol passes the 60% gate, and the Range Strategy correctly stays gated — the
threshold must not be lowered to paper over missing data.**
