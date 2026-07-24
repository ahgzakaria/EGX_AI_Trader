# Rubix Intraday Capture Audit (Phase 1–3, 7)

**Method:** read-only analysis of the live `rubix_live_market.db` (`feed_metrics`
1,316,852 rows; `quotes` 535,714; `candles_1m` 51,485), filtered to actual EGX
session hours (10:00–14:30 Cairo = 07:00–11:30 UTC in July). No production
behavior was changed. No minute bar was fabricated.

**Headline finding:** The sparse minute-candle coverage is **not** a collector
throughput bottleneck, **not** a candle-builder bug, and **not** per-stock low
liquidity. It is a **feed connection-uptime problem** — the upstream stream
(`wss://eg-feed3.mubashertrade.com`) delivers updates densely (~1 every 3.8 s)
**when connected**, but the connection is only alive for roughly **43% of the
session**, with large market-wide silent gaps (up to **77 minutes**). No data
arrives for *any* symbol during those gaps, so no candles can honestly be built.

---

## Critical architectural fact

**The Rubix collector is an external program that is NOT in this repository.**
`EGX_AI_Trader/scripts/*` (RC1) only *supervises/launches* it via
`python -m rubix_feed.cli --url wss://eg-feed3.mubashertrade.com/...` pointing at
an external adapter directory (`adapter.py, cli.py, protocol.py, storage.py`).
This repo contains **no WebSocket client, no message parser, and no
`candles_1m`/`quotes` writer** — it opens the DB `mode=ro` only. Consequently the
Phase 4 "rewrite the collector pipeline" work **cannot be performed here** — the
source that would be rewritten is not present. This audit therefore focuses on
*diagnosis from telemetry*, which is fully possible and is what actually gates
the decision.

---

## The evidence (real, session-filtered)

### 1. The feed is responsive when connected — NOT sparse

`update_interval_ms` during session hours (time between consecutive updates):

| Session | median | p75 | p90 | ≤60 s | >5 min |
|---|---:|---:|---:|---:|---:|
| 2026-07-19 | **3.8 s** | 15.3 s | 56.3 s | 91% | 1% |
| 2026-07-16 | 3.5 s | 14.7 s | 56.9 s | 91% | 1% |
| 2026-07-20 | 4.9 s | 15.6 s | 56.4 s | 91% | 1% |

When the stream is live, updates arrive every few seconds. Upstream is capable
of dense delivery. **This rules out SERVER_FEED_SPARSE as the primary cause.**

### 2. All 265 symbols are subscribed and receive data

- Distinct symbols that ever received a quote: **265 / 265**.
- `subscription_rejected`: **0** (all history).
- `subscription_batch_sent`: 365. Batch size 100 (config).
**Rules out SUBSCRIPTION_LIMIT and SYMBOL_MAPPING_FAILURE.**

### 3. The candle builder is faithful — NOT a bug

For the most liquid symbols on 07-19, candle count exactly equals the number of
distinct minutes that had a quote:

| Symbol | Quotes | Distinct quote-minutes | Candles |
|---|---:|---:|---:|
| AMER | 4,627 | 78 | 79 |
| AMES | 4,188 | 78 | 79 |
| CAED | 3,936 | 76 | 77 |
| ABUK | 3,258 | 78 | 79 |
| ADIB | 2,724 | 78 | 79 |

One candle per active minute, no dropped minutes. **Rules out
DATABASE_WRITE_BOTTLENECK / PARSER_REJECTION / COLLECTOR_BOTTLENECK as the cause
of low coverage** (throughput is fine: ~794 stored quotes/minute market-wide).

### 4. The silence is MARKET-WIDE and SHARED — NOT per-stock liquidity

This is the decisive test. On 07-19, three independent liquid stocks are active
in the **same** minutes, not different ones:

- AMER active minutes: 78 · CAED: 76 · ADIB: 78
- Overlap AMER∩ADIB: **78** (identical) · AMER∩CAED: 76
- **Union of all three: only 78 minutes** (would be 150+ if liquidity-driven)
- **Market-wide: only 116 of 270 session-minutes had ANY quote from ANY of the
  265 symbols** — i.e. for **154 minutes (57%)** the entire feed delivered
  nothing for the entire market.
- Largest market-wide silent gap: **77 minutes**.

EGX does not halt all 265 stocks for 77 minutes mid-session. A whole-market
simultaneous silence is a **connection/collection outage**, not liquidity.
**Rules out LOW_LIQUIDITY_SYMBOL as the dominant cause** (it is at most a minor
contributor for genuinely thin names).

### 5. Connection churn is high

Per session (intra-session window): `disconnect` 43–95, `reconnect_success`
29–61, `stale_start` **1,809–1,837**. Across all history: 186 disconnects vs
124 reconnect_success — so **~62 disconnects had no matching successful
reconnect**, consistent with the long silent windows. `out_of_order_timestamp`
during session: 0 (ordering is fine). `duplicate_message`: ~25–29% of messages
(the server re-sends unchanged snapshots — deduped correctly, not a problem).

### 6. The latest session (07-20) is a partial capture

07-20 stored only 53k quotes (vs 214k on 07-19) with *fewer* disconnects (8 vs
43) and only 18 bars/symbol. Fewer disconnects but far less data ⇒ the collector
was likely **not running the full 07-20 session** (started late / stopped
early), which is why the earlier scalping audit saw ~8% coverage that day.

---

## Message-type inventory (feed_metrics)

Real event types observed (full counts in `reports/rubix_message_type_inventory.csv`):
`latency_ms` (534,869), `update_interval_ms` (525,965), `duplicate_message`
(219,733), `stale_start` (11,622), `stale_cleared` (8,026), `heartbeat_received`
(6,895), `heartbeat_sent` (5,822), `subscription_batch_sent` (365),
`out_of_order_timestamp` (256), `disconnect` (186), `authentication_acknowledged`
(160), `connected` (159), `reconnect_success` (124), `subscription_sent` (83),
`collector_started`/`collector_stopped` (39/20). Payload fields: `quotes` carries
`last_price, bid, ask, volume (cumulative), market_timestamp, received_at,
sequence`; `candles_1m` carries `open, high, low, close, volume, updates`.

---

## Coverage vocabulary (Phase 7)

- **Observation coverage** = minutes with ≥1 received event ÷ 270. On 07-19 the
  market-wide ceiling is 116/270 = **43%**; even the best single symbol reaches
  only ~78/270 = **29%** because it is silent whenever the *market-wide* feed is
  silent.
- **Active-market coverage** (minutes the stock genuinely traded) cannot exceed
  observation coverage and is unknowable during the 57% of minutes where the feed
  delivered nothing — you cannot tell "quiet stock" from "feed down" in a window
  where nothing arrived for anyone.

Because the gaps are market-wide and simultaneous, the limiting factor is
**observation availability (connection uptime)**, not market activity.

---

## Failure classification

| Class | Verdict |
|---|---|
| COLLECTOR_BOTTLENECK (throughput/queue/DB writes) | ❌ Not the cause — 794 quotes/min stored, 1 candle per active minute, 0 out-of-order |
| DATABASE_WRITE_BOTTLENECK | ❌ Not observed |
| PARSER_REJECTION | ❌ Not observed (candles match quote-minutes) |
| SUBSCRIPTION_LIMIT | ❌ All 265 subscribed, 0 rejections |
| SYMBOL_MAPPING_FAILURE | ❌ All symbols map and receive |
| SERVER_FEED_SPARSE | ❌ Feed delivers every ~3.8 s **when connected** |
| LOW_LIQUIDITY_SYMBOL | ⚠️ Minor contributor for thin names only |
| **RECONNECT_SUBSCRIPTION_LOSS / connection-uptime** | ✅ **PRIMARY** — market-wide silent gaps up to 77 min, 43–95 disc/session, 62 unmatched disconnects |
| **Partial collector run (07-20)** | ✅ Secondary — latest session under-captured |
| **UNKNOWN: root cause of disconnects (upstream vs client)** | ⚠️ Cannot be attributed from the DB alone; the collector source is external/not in this repo, so whether Mubasher drops the connection or the client fails to hold it is undetermined here |

**Do not claim the collector was "fixed."** The bottleneck is connection uptime,
not throughput, so a high-throughput queue/batch rewrite would not raise
coverage. The next step must be a **live-session connection-stability
investigation of the external `rubix_feed` collector** (heartbeat/keepalive,
reconnect backoff, whether Mubasher closes idle/duplicate connections), which
requires the external collector source and a live EGX session — neither
available in this pass (repo lacks the source; market is closed).
