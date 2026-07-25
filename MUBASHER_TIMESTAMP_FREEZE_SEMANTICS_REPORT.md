# Mubasher Frozen-Timestamp — Value-Progression Semantics (Audit)

**Verdict: Hypothesis A confirmed.** During all 18 freeze periods on 2026-07-21,
**only the exchange `market_timestamp` froze — Last / Bid / Ask / Volume kept
genuinely progressing and the market was actively trading.** The 300-second
fatal-gap gate is therefore applied to the **wrong field**: it treats a Mubasher
timestamp artifact as a data outage. A value-progression-based connection-health
model (diagnostic only) would recognise the feed as healthy. **No threshold,
strategy, gate, or collector was changed** — this is audit only.

Reports: `reports/rubix_timestamp_freeze_value_progression.csv` (per-period),
`reports/rubix_freeze_per_symbol.csv` (per-symbol for the 8 key names).

## The evidence (real 07-21, 162,142 continuous quotes)

Every freeze ≥120 s classified as **TIMESTAMP_ONLY_STALE (18 / 18)** — zero
FULL_MARKET_STATE_STALE, zero PARTIAL, zero DUPLICATE-only:

| | Value |
|---|---:|
| Periods where Last price genuinely changed | **18 / 18** |
| Periods with any material value change | **18 / 18** |
| Average duplicate frames per period | 76.2% (⇒ ~24% carried NEW values) |

The **three fatal (>300 s) freezes** were the most actively-trading of all:

| Period | Start | Dur | Frames | Last changes | Bid changes | Vol changes | Cum-Vol Δ |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | 10:17:02 | 317 s | 3,570 | **243** | 182 | 460 | **11.7 M** |
| 4 | 11:01:02 | 311 s | 3,437 | **182** | 118 | 362 | **17.5 M** |
| 12 | 12:10:06 | 303 s | 3,244 | **199** | 166 | 365 | **11.8 M** |

Key liquid symbols kept trading throughout the fatal freezes (period 1): COMI 13
Last-changes / +8,141 vol, ADIB 12 / +107,775, TMGH / +42,920 — **all two-sided
executable**. Volume advancing by millions proves genuine market activity, not a
frozen snapshot.

## Feed health vs symbol activity (the key distinction)

- **Market-wide genuine value-progression gap: max 36 s.** Some symbol's Last
  price changed at least every 36 s across the whole continuous session — the
  feed never stalled. The 300 s "connection outage" that blocked every symbol was
  **entirely** the Mubasher `market_timestamp` freeze, not missing data.
- **Per-symbol value gap: median ~22 min.** Individual EGX stocks simply don't
  tick every 5 minutes — that is normal inactivity (`SYMBOL_INACTIVE`), not a
  failure, and must not gate the range (the same lesson as the minute-coverage
  gate).

## Shadow counterfactual (diagnostic only — NOT activated)

| Model | Connection-outage signal | Fatal >300 s freezes | RANGE_CONFIRMED-eligible |
|---|---|---:|---:|
| A. Strict `market_timestamp` (current) | timestamp gap | 3 | **0** |
| B. Value-progression, **market-wide feed health** | genuine value gap (max 36 s) | **0** | **up to 159** (the EVENT_DATA_VALID set) |
| B-mis. Value-progression applied **per-symbol** (wrong) | per-symbol value gap | — | 1 (re-introduces the inactivity bug) |

Model B removes the three false outages, so the 159 well-observed liquid symbols
(which already pass span / distinct-price / spread / liquidity) would become
RANGE_CONFIRMED-eligible. **The 300 s threshold itself is unchanged** — only the
*signal it is measured against* would move from a Mubasher timestamp artifact to
genuine value progression.

## The 11 questions

1. **Did only the timestamp freeze?** **Yes — 18/18 TIMESTAMP_ONLY_STALE.**
2. **Did Last prices change?** **Yes**, in every period (243/182/199 changes in the fatal three).
3. **Did Bid/Ask change?** Yes (182/118/166 Bid changes in the fatal three).
4. **Did cumulative Volume advance?** **Yes — by 11–17 million** in each fatal freeze.
5. **% identical duplicate frames?** ~**76%** on average (so ~24% carried new values).
6. **How many FULL_MARKET_STATE_STALE?** **0.**
7. **How many TIMESTAMP_ONLY_STALE?** **18 (all).**
8. **Does `market_timestamp` alone falsely reject valid data?** **Yes — definitively.**
   Real prices/volume progressed during all three "fatal" freezes.
9. **How many symbols RANGE_CONFIRMED in the diagnostic model?** Up to **159**
   (market-wide value-progression model B) — vs 0 under the strict-timestamp model.
   (A per-symbol value gate would give only 1 and is the wrong design.)
10. **Is a gate redesign justified?** **Yes, pending multi-session validation.**
    Replace the connection-outage detector's input from `market_timestamp` gaps to
    **genuine market-wide value progression**, keep the 300 s threshold, keep it a
    feed-health (not per-symbol) measure. Validate across ≥3 patched sessions first.
11. **Did any production threshold or strategy change?** **No.** Audit only.

## Recommendation

The user's second branch is the correct one: **prices and volume were moving while
only Mubasher's timestamp stood still — the 300 s gate depends on the wrong field.**
Build the value-progression connection-health signal as a **shadow** model
(diagnostic, disabled), validate it across the next ≥3 patched sessions, and only
then consider adopting it — **without lowering the 300 s threshold** and **without
turning it into a per-symbol activity requirement**. Do not activate the diagnostic
model; production behavior stays unchanged.
