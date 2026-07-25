# EODHD Phase 2 — Full EGX Reconciliation & Price-Scale Validation

**Date:** 2026-07-23 · Mode `EODHD_SHADOW` · **Active historical provider: Yahoo
(unchanged)** · **Rubix live (unchanged)** · production disabled. Read-only audit — no
provider switched, no strategy/score/threshold/indicator/TP-SL/paper/production change,
no `Close`↔`Adj Close` change, no symbol mapped by suffix alone, no symbol auto-added.
**529 tests pass.**

Artifacts (all in `reports/eodhd/`): `unmapped_symbol_reconciliation.csv`,
`extra_eodhd_symbols_review.csv`, `price_scale_anomalies.csv`,
`adjustment_policy_audit.csv`, `full_universe_symbol_results.csv`,
`full_universe_shadow_summary.csv/.json`, `manual_review_queue.csv`.

---

## Headline numbers
- Internal universe **265** · EODHD EGX **239** · **VERIFIED_EXACT 225 (85%)**.
- 40 unmapped: **39 coverage gaps (active in Rubix, absent from EODHD) + 1 ETF**.
- 14 extra EODHD: **12 real EGX equities missing from the internal universe + 1 NULL
  artifact + 1 alternate share class**.
- Full-universe audit (225): **CLEAN_MATCH 20 · MINOR_ROUNDING 161 · MANUAL_REVIEW 15 ·
  DATA_UNAVAILABLE (Yahoo missing) 27 · PRICE_SCALE_ANOMALY 2**.
- **181 / 225 (80%)** agree on recent close within ≤0.50%. **198 / 225 EODHD is
  fresher-or-equal** to Yahoo. **27 symbols EODHD has but Yahoo does not.**

## The 16 questions

**1. Final classifications of the 40 unmapped.**
39 → **NOT_SUPPORTED_BY_EODHD** (each is actively quoted by Rubix today, yet absent from
both the EODHD EGX list and EODHD search — a coverage gap, not a delisting). 1 →
**FUND_OR_ETF** (`EGX30ETF`; EODHD's EGX list is common-stock only). None are delisted;
none could be confirmed as a renamed EGX ticker (EODHD search returned zero EGX
candidates for all 40 — the only hits were unrelated foreign securities sharing the
letters).

**2. Any active equities missing only because of ticker changes?**
No confirmable rename via EODHD. The 40 are covered-by-Rubix but not-in-EODHD; EODHD
simply does not carry them, so a ticker change cannot be evidenced from EODHD. Each is
marked NOT_SUPPORTED with the honest note "keep on current provider."

**3. Should any of the 14 extra EODHD symbols be added later?**
12 are valid active EGX common stocks with ISINs that the internal universe omits
(e.g. `AUTO`=GB Corp, `QNBA`=QNB Al Ahli-class, `AIND`=Arabia Investments, `MATD`=Marsa
Alam, `MEDP`=Medical Packaging …) — **candidates for later review, not auto-added**.
`NULL` is a placeholder artifact (MAPPING_ERROR, ignore). `SEIGA` is a probable
alternate share class of internal `SEIG` (needs share-class confirmation). No addition
was made.

**4. What caused the ORAS 10× mismatch?**
**Yahoo's `ORAS.CA` series is stale/mis-scaled.** On 2026-07-22 EODHD `ORAS.EGX` = **713.5**
and the **live Rubix close = 713.5** (ratio 1.00), while Yahoo = **71.05 with volume 0**.
Rubix adjudicates: **EODHD is correct; Yahoo is unreliable for ORAS** → status
`RESOLVED_EODHD_CORRECT`.

**5. Are there other scale mismatches?**
One: **MEGM** (EODHD 43.92 vs Yahoo 12.54, ≈3.5×). Rubix carries no valid MEGM price
(0.0, illiquid), so it cannot adjudicate → `NEEDS_MANUAL_REVIEW` (hold). No other symbol
in the 225 shows a gross scale offset (the rest cluster at ratio ≈ 1.0).

**6. What adjusted-price convention does the project use?**
**Raw `Close` throughout.** Indicators (`indicators/technical.py`: RSI/MACD/ADX/… all
`df["Close"]`) and the expected-range/volatility models (`daily["Close"]`) consume the
unadjusted Close and raw OHLC; **Volume is unadjusted**. `Adj Close` is carried as an
optional column but is **not consumed** by the scalping/expected-range strategy. Yahoo
is fetched with `auto_adjust=False`, so its `Close` is **split-adjusted** (dividends go
to `Adj Close`). Rubix/TradingView bridges set `Adj Close = Close`. This is the frozen
behavior; nothing was changed.

**7. Would moving to EODHD change existing backtest results?**
**Potentially yes for windows that span a stock split.** EODHD `close` is *fully
unadjusted*; Yahoo `Close` is *split-adjusted*. Recent bars are identical (ratio 1.00),
but deep history diverges by the cumulative split factor (COMI 9.5×, SWDY 17×, EAST 46×,
FWRY 4.4×; TMGH none). A backtest whose lookback crosses a split would see a raw-close
discontinuity under EODHD that Yahoo smooths → **ADR/range/indicator values, and thus
results, would differ**. Recent forward paper (post most-recent split) is unaffected.
Mitigation (future, deliberate): use EODHD `adjusted_close` or apply EODHD split factors
— not done in this phase.

**8. How many symbols pass the recent OHLC comparison?**
**181 / 225 (80%)** within ≤0.50% mean recent close difference (20 CLEAN ≤0.10% + 161
MINOR ≤0.50%). 15 are >0.50% (MANUAL_REVIEW: ARAB, ASPI, CFGH, COPR, DEIN, EASB, HDBK,
IEEC, KZPC, MEGM→scale, MHOT, NAHO, PRDC, SPMD, TRTO, UNIP). 27 could not be compared
(Yahoo had no data). 2 scale anomalies.

**9. How many symbols have material volume differences?**
Recent volumes agree for the liquid names (often identical). Material volume gaps track
the price outliers and the 27 Yahoo-missing symbols; ORAS is the clearest (Yahoo volume
0 vs EODHD real volume). Per-symbol figures are in `full_universe_symbol_results.csv`
(`mean_volume_pct_diff`).

**10. Which symbols require per-symbol fallback?**
- The **40 unmapped** → must stay on Yahoo/Rubix (no EODHD coverage).
- **MEGM** and the **15 MANUAL_REVIEW** symbols → hold on Yahoo pending investigation.
- **ORAS** → the reverse: prefer **EODHD** (Yahoo is the unreliable one).
`reports/eodhd/manual_review_queue.csv` holds the review set.

**11. Is EODHD fresher than Yahoo?**
Yes or equal: EODHD latest = **2026-07-22** (the correct last completed session per the
EGX calendar) for the mapped set; **EODHD ≥ Yahoo freshness for 198/225**, and EODHD
**covers 27 symbols Yahoo lacks entirely**. No stale EODHD symbol was found.

**12. API-call cost for the full universe.**
First full run ≈ **212 live EODHD calls** (40 searches + exchange list + user + ~225
EOD, minus cache); reruns **0 live / 225 cache hits** (persistent disk cache). Daily
limit is 100,000 — usage is negligible.

**13. Is EODHD safe to make primary for verified symbols?**
For the **~181 clean/minor-match symbols** (and ORAS, where EODHD beats Yahoo) EODHD is a
strong, fresh, well-agreeing source — promising. But **not yet blanket-safe**: the
split-adjustment convention (Q7), the 15 MANUAL_REVIEW symbols, MEGM, and the 27
coverage-vs-Yahoo cases need resolution first, and 40 symbols have no EODHD coverage at
all. Promotion should be **per-symbol and gated**, not universe-wide.

**14. Should Yahoo remain as a temporary fallback?**
Yes. Yahoo must remain active now, and after any promotion remain the fallback for the
40 EODHD-uncovered symbols and the review queue — while noting Yahoo's own defects
(ORAS 10×/zero-volume, 27 missing series).

**15. Was Rubix left unchanged?**
Yes — Rubix is untouched and served here only as the read-only adjudicator for the price
scale (it confirmed EODHD for ORAS).

**16. Is production still disabled?**
Yes — `production_enabled=false`, `automatic_execution=false`, `broker_orders_enabled=
false`.

---

## Part 8 — Provider routing recommendation

**Immediate recommendation: A. REMAIN_EODHD_SHADOW.** There is unresolved work (split-
adjustment integration decision, 15 MANUAL_REVIEW symbols, MEGM, and confirming the 27
EODHD-only / Yahoo-missing symbols) that must be cleared before any switch.

**Target architecture (after the queue clears): C. EODHD_PRIMARY_WITH_PER_SYMBOL
EXCEPTIONS** — a per-symbol policy router:
- EODHD primary for the ~181 clean/minor symbols **and** ORAS **and** the 27 Yahoo-
  missing symbols (EODHD is the only/better source);
- Yahoo/Rubix retained for the **40 EODHD-uncovered** symbols and any unresolved review
  items;
- a split-adjustment rule decided up-front (EODHD `adjusted_close` or split-factor
  reconstruction) so historical backtests remain consistent.
`EODHD_ONLY` is rejected (40 symbols uncovered). Coverage (85% mappable), price agreement
(80% ≤0.5%), superior freshness/coverage vs Yahoo, and negligible API cost all support C
as the destination — but **only per-symbol and after the gates above**.

## Safety
Token never printed/logged (masked fingerprint only); `.env` gitignored; Yahoo remains
the active historical provider; Rubix unchanged; no strategy/threshold/indicator/TP-SL/
paper/production/execution change. **Stopping — no provider will be switched until you
review this report.**
