# CURRENT_RESEARCH_V2 — Pre-Approval Reconciliation

Verification pass only. No strategy, provider, routing decision, data file or flag was
changed. The only code touched is the **report generator**
`scripts/build_current_research_migration_manifest.py`, which now emits provenance columns.
Regression: **573 passed**.

**Bottom line: two of the four checks fail. I do not recommend approving CURRENT_RESEARCH_V2
as it stands.** The counts reconcile (§1, §2) but the volume series (§3) and the Yahoo
isolation claim (§4) do not hold. My earlier migration report overstated both.

---

## 1. The 13-symbol difference — reconciled

Tier A 108 + Tier B 28 + Tier C 73 = **209** EODHD-routed symbols; **196** are active.
All 73 Tier C symbols did pass the clean-window gate — Tier C is not the source of the gap.
The 13 are **1 Tier A + 12 Tier B**, blocked *before* tier logic by the 250-bar minimum
lookback. Every one is a recent listing whose entire EODHD history is shorter than 250
sessions. All 13 are **current to 2026-07-22** — none is stale, none is a data fault.

| Symbol | Previous tier | Current route | State | Reason | Rows | First | Latest | Data quality |
|---|---|---|---|---|---|---|---|---|
| FERC | TIER_A_FORWARD_SAFE | EODHD → blocked | Blocked | 210 < 250 bars | 210 | 2025-09-10 | 2026-07-22 | DATA_INSUFFICIENT |
| BONY | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 244 < 250 bars | 244 | 2025-07-22 | 2026-07-22 | DATA_INSUFFICIENT |
| CPME | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 243 < 250 bars | 243 | 2025-07-23 | 2026-07-22 | DATA_INSUFFICIENT |
| NARE | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 241 < 250 bars | 241 | 2025-07-28 | 2026-07-22 | DATA_INSUFFICIENT |
| NAPR | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 235 < 250 bars | 235 | 2025-08-05 | 2026-07-22 | DATA_INSUFFICIENT |
| AIDC | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 231 < 250 bars | 231 | 2025-08-11 | 2026-07-22 | DATA_INSUFFICIENT |
| VLMRA | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 153 < 250 bars | 153 | 2025-12-01 | 2026-07-22 | DATA_INSUFFICIENT |
| GPIM | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 106 < 250 bars | 106 | 2026-02-09 | 2026-07-22 | DATA_INSUFFICIENT |
| GOUR | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 105 < 250 bars | 105 | 2026-02-10 | 2026-07-22 | DATA_INSUFFICIENT |
| TWSA | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 55 < 250 bars | 55 | 2026-04-28 | 2026-07-22 | DATA_INSUFFICIENT |
| TYCN | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 55 < 250 bars | 55 | 2026-04-28 | 2026-07-22 | DATA_INSUFFICIENT |
| UTOP | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 52 < 250 bars | 52 | 2026-05-03 | 2026-07-22 | DATA_INSUFFICIENT |
| KORA | TIER_B_NO_FALLBACK | EODHD → blocked | Blocked | 28 < 250 bars | 28 | 2026-06-11 | 2026-07-22 | DATA_INSUFFICIENT |

Four of them (BONY 244, CPME 243, NARE 241, NAPR 235) clear 250 bars within weeks and will
self-activate — no intervention needed.

**Manifest defect fixed:** the previous manifest wrote `rows=0` for every blocked symbol
while its own `blocked_reason` said "231 bars". Real counts are now recorded.

## 2. The 69-vs-56 discrepancy — my report's error, not a data error

The two populations were different and I presented them as one. `31 ready of 56` is
**Tier D only**; `17/20/1 = 38` is the **all-tier** blocked total. Adding them
double-counts. The correct split:

| Blocked category | Count | Meaning |
|---|---|---|
| EODHD_SUPPORTED_BUT_BLOCKED | **13** | §1 above — EODHD serves them, history < 250 bars |
| UNSUPPORTED_NO_HISTORY | **20** | not on EODHD, no local history at all (DATA_UNAVAILABLE) |
| UNSUPPORTED_INSUFFICIENT_LOCAL_HISTORY | **4** | not on EODHD, local history < 250 bars |
| MANUAL_REVIEW | **0** | queue was cleared in Phase 3 (`manual_queue_resolution.csv`) |
| EXCLUDED_NON_EQUITY | **1** | not an equity |
| **Total blocked** | **38** | |

Tier D: 31 ready + 25 blocked = 56 ✓. The 17 DATA_INSUFFICIENT = 13 EODHD-tier + 4 Tier D.

## 3. Volume series — **FAILS**

`providers/eodhd_adjustment.py:137` applies a **universal** transform:

```python
out["Volume"] = out["Raw Volume"] * out["Split Factor"]
```

`core/history_frame_adapter.py:37` then takes that adjusted `Volume` into the operational
frame. So the metadata label `adjustment_policy: "SPLIT_ONLY_EVENT_SPECIFIC"` **describes a
policy the code does not implement.** Your Phase 3.1 audit
(`reports/eodhd/volume_adjustment_event_validation.csv`) established three distinct policies
— `MULTIPLY_BY_FACTOR`, `KEEP_RAW`, `EVENT_SPECIFIC` — and bonus/capital-increase events are
explicitly *not* multiply-by-factor. The engine applies multiply-by-factor to all of them.

| Route | Symbols | Volume actually delivered |
|---|---|---|
| Tier A/B/C → EODHD | 196 | **EODHD raw × universal cumulative split factor** (not event-specific) |
| Tier D → local + bridge | 31 | **Yahoo-derived local seed volume** (Yahoo's own adjustment); 0 Rubix bars appended |
| Blocked | 38 | none served |

**Current blast radius — measured, not assumed.** The cumulative factor is 1.0 for all bars
after the last split, so short windows are only contaminated when a split ex-date falls
inside them. Liquidity ranking uses `tail(5/10/20/30)`; volume indicators use `rolling(20)`.

- **0 of 196** activated EODHD symbols have a split ex-date within the last 30 sessions.
  → `liq_avg_volume_20`, `liq_avg_turnover_egp_20`, `volume_trend_ratio`, minimum-volume
  filters and `Volume / Volume.rolling(20).mean()` are **presently uncontaminated**.
- **36 of 196** have a split ex-date inside the last 250 sessions, so any longer-window
  volume statistic is already distorted: COMI +3.05%, UNIP +62.0% (250-bar mean vs raw).

Test symbols you named:

| Symbol | Route | Splits | Last split | Factor in last 20 | avg_vol_20 error | 250-bar mean error |
|---|---|---|---|---|---|---|
| COMI | EODHD (Tier C) | 10 | 2025-12-17 | 1.0 | 0.000% | **+3.051%** |
| EAST | EODHD (Tier C) | 6 | 2024-06-06 | 1.0 | 0.000% | 0.000% |
| SWDY | EODHD (Tier C) | 4 | 2018-09-27 | 1.0 | 0.000% | 0.000% |
| ORAS | EODHD (Tier B) | 0 | — | 1.0 | 0.000% | 0.000% |
| UNIP | local + bridge | 8 | 2025-09-25 | 1.0 | 0.000% | **+62.03%** |
| KZPC | local + bridge | 6 | 2026-06-25 | **1.0–1.2** | **+0.655%** | **+19.15%** |

KZPC is the worked example of the failure mode: a 20-bar window straddling the 2026-06-25
ex-date gets ×1.2 on the older bars and ×1.0 on the newer ones — an artificial volume step
inside the ranking window. It is currently harmless only because KZPC routes to local, not
EODHD. **The first time a split ex-date lands inside 30 sessions for any of the 196, the
liquidity ranking silently shifts.** That is a latent, not a theoretical, defect.

So: I cannot confirm what you asked me to confirm. A universal incorrect volume
transformation **is** being fed into the volume series; it is not currently reaching the
20-day ranking windows, and that is luck of the calendar, not design.

## 4. Yahoo isolation — level 1 passes, level 2 **FAILS**

**Level 1 — zero Yahoo network calls: confirmed.** No operational path calls `yfinance`.
The legacy reader is cache-only. Regression test asserts an exploding Yahoo provider is
never invoked for scanner/dashboard/forward_testing.

**Level 2 — provenance labelling and freshness: fails on both counts.**

For all 31 local-route symbols:

- `bridge_sessions_appended = 0`. **Total Rubix Daily Bridge bars appended across all 31: 0.**
  They are running on **100% Yahoo-derived bars** with zero Rubix extension.
- The frame is labelled `provider="local_validated+rubix_bridge"`,
  `price_series="PROJECT_LOCAL"`, **`yahoo_used=False`**. The name advertises a Rubix
  contribution that does not exist, and `yahoo_used=False` is true only in the narrow sense
  of "no network call" — as a *provenance* claim about the bars it is wrong.
- `latest_completed_session` is read off `frame.index[-1]` regardless of origin, so for all
  31, **freshness is determined by Yahoo-derived bars.** Visible in the data: EODHD symbols
  report 2026-07-22, all 31 local symbols report 2026-07-21 — the 07-21 bar is the Yahoo
  seed's last bar.

Three of the 31 are badly stale yet marked `LOCAL_PLUS_RUBIX_READY` and activated:

| Symbol | Rows | Latest session | Age |
|---|---|---|---|
| SUCE | 1331 | 2023-03-08 | ~3.4 years |
| UASG | 2042 | 2024-10-23 | ~1.7 years |
| ESRS | 2144 | 2025-03-13 | ~1.4 years |

Root cause of the mislabel — `core/research_router.py`:

```python
state = LOCAL_PLUS_RUBIX_READY if appended >= 0 else LOCAL_PLUS_RUBIX_BUILDING_HISTORY
```

An append count can never be negative, so this is unconditionally `READY`;
`LOCAL_PLUS_RUBIX_BUILDING_HISTORY` is dead code and no staleness check is applied.

Your stated condition — "an old immutable local history seed is acceptable, but it must be
clearly labelled and only extended using Rubix Daily Bridge" — is **not met**: the seed is
not labelled as Yahoo-derived, and it is not being extended at all.

Separately, `core/history_frame_adapter.py` still stamps `session_phase: "SHADOW"` and
`freshness_status: "SHADOW"` into every operational EODHD frame — leftover shadow-mode
labelling now flowing through current research.

## 5. Reconciliation — every total is 265

| Route | Active | Blocked | Total |
|---|---|---|---|
| Tier A — EODHD forward-safe | 107 | 1 | 108 |
| Tier B — EODHD no-fallback | 16 | 12 | 28 |
| Tier C — EODHD clean-window | 73 | 0 | 73 |
| Tier D — local seed + Rubix bridge | 31 | 25 | 56 |
| **Total** | **227** | **38** | **265** |

EODHD-routed tiers (A+B+C): 209 total → 196 active + 13 blocked.
Regenerated with machine-checked assertions (`reconciles_to_universe: true`,
`tier_totals_reconcile: true`):

- `reports/eodhd/current_research_migration_manifest.csv` — 265 rows, now carrying
  `block_category`, `volume_series`, `volume_detail`, `bridge_sessions_appended`,
  `freshness_source`, and true row counts for blocked symbols
- `reports/eodhd/current_research_migration_summary.json` — adds `by_tier`,
  `blocked_categories`, `volume_series_counts`, `freshness_source_counts`,
  `rubix_bridge_bars_appended_total: 0`,
  `activated_symbols_whose_freshness_comes_from_yahoo_derived_seed: 31`

## 6. Corrections to the migration report

These answers in `CURRENT_RESEARCH_V2_EODHD_MIGRATION_REPORT.md` were wrong:

- **Q4** — "Yahoo code survives only in the legacy reader and audit tooling." Wrong: 31
  activated current-research symbols are served Yahoo-derived bars.
- **Q7** — "31 ready … using validated local history plus idempotent Rubix Daily Bridge
  appends." Wrong: zero bridge appends have occurred.
- **Q8** — presented the all-tier blocked breakdown as the Tier D breakdown (§2).
- **Q10** — "freshness is independent from Yahoo." True for the 196 EODHD symbols, false for
  the 31 local ones.
- **Q12** — metadata claims `adjustment_policy: SPLIT_ONLY_EVENT_SPECIFIC`; the code applies
  a universal factor (§3).

I should have measured the bridge contribution and the volume path before making those
claims rather than inferring them from the routing design.

## 7. What blocks approval

1. Volume: replace the universal `× Split Factor` with the event-specific policy already
   validated in `volume_adjustment_event_validation.csv`, or relabel the series honestly.
2. The 31 local symbols: label the seed `YAHOO_DERIVED_FROZEN_SEED`, stop reporting
   `yahoo_used=False`, derive freshness from the bridge rather than the seed, and fix the
   `appended >= 0` predicate so stale seeds surface instead of reading `READY`.
3. Decide whether SUCE / UASG / ESRS (1.4–3.4 years stale) should stay activated.
4. Investigate why the Rubix Daily Bridge has produced 0 bars for all 31.
5. Clear the leftover `SHADOW` session/freshness labels from operational frames.

None of this is fixed here — this was a verification pass. Production, automatic execution
and broker orders all remain disabled.
