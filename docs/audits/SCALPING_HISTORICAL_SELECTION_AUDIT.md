# Scalping Historical Selection Redesign — Checkpoint Audit

**Status:** CHECKPOINT ONLY — strategy implementation has not started
**Audit date:** 2026-07-28 (Africa/Cairo)
**Branch:** `fix/scalping-historical-volatility-selection`
**Base / local main HEAD:** `e988dcc918a45b335ba8267e724c9b3628f3b717`
**Scope:** current implementation, Rubix intraday availability, metric definitions,
distribution evidence, provisional thresholds, architecture, UI and expected files

## Safety and repository state

The work was isolated before inspection. The following existing local runtime
modifications remain outside this worktree and were neither read into a commit nor
modified:

- `data/paper_trades.csv` — recorded SHA-1
  `323bc8b6c8547720d30e38ba3cab024ad55bb080`
- `docs/audits/strategies/SCALPING_MULTI_SESSION_VERDICT.md` — recorded SHA-1
  `640d9c0e98154d75e983916d6b04480e7cdc2b3d`

No strategy, provider, dashboard, paper record, production flag, or broker behavior
was changed during this checkpoint. Production and automatic execution remain
disabled.

## Executive finding

The requested separation is necessary, but the implementation must not start yet.

There are three current scalping paths:

1. The active Expected Range Scalper (ERS) uses mostly completed **daily** history,
   not historical minute sessions. Its scheduled 09:45 snapshot is historical-only,
   but the interactive dashboard rebuilds the score with a 5% live-spread component.
2. The Range Scanner calculates 83.33% of its suitability score from the current
   session or current quote state.
3. The legacy setup detector assigns higher setup scores when current momentum and
   relative volume are already high.

That explains the product-level impression that the system follows stocks that are
already active, even though the scheduled ERS rank itself has no price-change,
momentum, or breakout term.

The Rubix store contains only seven globally sound completed continuous sessions for
this research purpose. No symbol has the required 20 valid sessions:

| Coverage | Structural range-valid | Also passes inherited executable gate |
|---|---:|---:|
| At least 1 session | 226 / 265 (85.3%) | 210 / 265 (79.2%) |
| At least 4 sessions | 218 / 265 (82.3%) | 183 / 265 (69.1%) |
| All 7 audited sessions | 138 / 265 (52.1%) | 67 / 265 (25.3%) |
| At least 20 sessions | **0 / 265 (0.0%)** | **0 / 265 (0.0%)** |
| At least 30 sessions | **0 / 265 (0.0%)** | **0 / 265 (0.0%)** |

Therefore:

- the proposed formulas below are deterministic and implementation-ready;
- the distribution percentiles are diagnostic, not calibration-grade;
- the threshold values are provisional seeds, not final or production-approved;
- the new selector must return `HISTORICAL_DATA_INSUFFICIENT` and no eligible
  watchlist until the minimum 20 sessions exists;
- thresholds must not be relaxed to manufacture 15–20 candidates.

## 1. Current implementation audit

### 1.1 Active ERS historical input is daily, not historical intraday

`scalping_expected_range/historical_selector.py` reads:

```text
LocalCacheProvider.load_cached("yahoo", symbol, period, "1d",
                                allow_expired=True)
```

It then optionally appends newer `FINAL_CONTINUOUS` rows from
`data/normalized_daily_cache.db`.

Important consequences:

- The active selector is built from daily OHLCV rows.
- The local cache namespace and provenance are explicitly Yahoo-derived. It makes no
  Yahoo network call, but it is still an operational Yahoo-derived scalping input.
  That conflicts with the requested policy that Yahoo remain only for explicitly
  frozen legacy backtest reproduction.
- The pre-session manifest labels the route as current EODHD research, while the
  selector code still requests the `"yahoo"` cache namespace. That provenance
  mismatch must be removed, not relabelled.
- Rubix overlay rows use continuous-session High/Low and `continuous_close`, which is
  correct for auction separation, but they are still reduced to daily rows.
- Historical range is measured against the **previous close**, not session open.
- Minute ordering, time of high/low, first-touch ordering, timing consistency,
  intraday spread stability and missing-minute behavior cannot be recovered from
  those daily rows.

The selector drops malformed/zero rows, flat zero-volume rows and dates later than
the latest completed exchange session. During session D, D is therefore excluded.
The current incomplete session does **not** enter ERS daily historical metrics.

### 1.2 Exact active ERS selection formula

The current historical window is 20 daily sessions. The components are
cross-sectional percentile ranks:

| Component | Current source | Weight |
|---|---|---:|
| `AvgVolumeScore` | percentile of 20-session mean daily volume | 30 |
| `AvgTurnoverScore` | percentile of 20-session mean `Close × Volume` | 25 |
| `VolatilityScore` | percentile of 20-session arithmetic mean `(High-Low)/PrevClose` | 20 |
| `TargetFrequencyScore` | percentile of frequency where daily range is at least 2% | 15 |
| `LiquidityConsistencyScore` | `100 × volume_consistency` | 5 |
| `SpreadScore` | `100 - percentile(live_spread_percent)` | 5 |

For row \(i\), over components that are present:

```text
RawScore_i =
    Σ(weight_k × component_score_i,k)
    / Σ(weight_k for non-missing component_i,k)
```

When live spread is unavailable, the denominator is 95, not 100. When it is
available, the denominator is 100.

The liquidity gate is:

```text
LIQUIDITY_VALID   -> 1.0
LIQUIDITY_LIMITED -> 0.7
all other states  -> 0.0

EXPECTED_RANGE_SCALPING_SCORE = RawScore × LiquidityGate
```

The rank sort is:

```text
TradableCandidate descending,
EXPECTED_RANGE_SCALPING_SCORE descending,
average turnover descending,
average volume descending,
2% range frequency descending
```

Current liquidity hard gates are mean volume 50,000, median volume 30,000,
mean turnover EGP 500,000 and no more than two zero-volume sessions. Limited
liquidity applies a 30% score haircut.

### 1.3 Exact live leakage and chase paths

#### Active ERS / Scalping Dashboard

| Current-session input | Historical rank weight | Other current effect |
|---|---:|---|
| Price change | 0 | none in ERS rank |
| Live volume | 0 | quote value is available to live logic but is not ranked |
| Current High/Low | 0 | scheduled live monitor currently does not populate them |
| Momentum | 0 | none in ERS rank |
| Breakout | 0 | scenario label only; no rank weight |
| Live Rubix spread | **5%** | changes interactive combined score |
| Live Last | 0 | changes range position, remaining room, state and UI ordering |

The scheduled `run_expected_range_pre_session.py` calls
`scan(with_live=False)`, so that specific immutable snapshot does not include live
spread. The interactive pages do not simply load that artifact:

- `_ers_scan` is only a Boolean “scan requested” flag.
- `_run_scan(True)` reconstructs the universe on each Streamlit rerun.
- the score and rank then include the current Rubix spread;
- the Dashboard watchlist is filtered to current `waiting` states and sorted first by
  `abs(RemainingUpside)`, then historical `Rank`;
- `RemainingUpside` is calculated from current Last versus the historical expected
  upper range.

Thus the table labelled “Watchlist (closest to ready)” is a live opportunity list,
not a frozen historical candidate watchlist. Today’s Last can change membership,
state, and ordering even when it does not directly change the historical factors.

`ExpectedRangeScanner.live_monitor()` says that historical ranking is frozen, but it
calls `add_scores()` again. The existing `Rank` column is not regenerated, while the
displayed combined score and score-sorted views can change with live spread. That is
two different notions of “frozen.”

The single-session script `run_expected_range_paper_session.py` calls
`scan(with_live=True)` before attempting to create an immutable pre-session snapshot.
If the scheduled historical-only snapshot does not already exist, this alternate
path can freeze a score/rank containing live spread.

The durable live monitor loads the immutable CSV but rebuilds `SymbolAnalysis` from
the current cache. It reports a hash mismatch but does not refuse it. If completed
history is corrected or advanced intraday, scenario calculations can use rebuilt
ranges while stored snapshot columns retain the old values.

#### Range Scanner

`scalping/range_scanner.py` has a separate `SCALPING_SUITABILITY_SCORE`:

```text
VolatilityScore    = mean(ADR percentile, ATR percentile,
                          TODAY session-range percentile)

weighted =
    25% VolatilityScore
  + 15% TODAY SessionRangeScore
  + 20% TODAY Turnover/Volume score
  + 15% live SpreadScore
  + 10% TODAY QuoteActivityScore
  + 15% TODAY DataCoverageScore

SCALPING_SUITABILITY_SCORE =
    weighted × clip(CoverageRatio / minimum_coverage, 0, 1)
```

Because one third of the 25% volatility component is today’s session range, today’s
session range contributes 8.33% there plus the separate 15%, or **23.33% total**.
Current-session/quote factors contribute **83.33%** in total; historical ADR and ATR
contribute only **16.67%**. `CURRENT_MOVE_PERCENT` is calculated and disclosed but
has a direct score weight of 0.

This scanner is not the active Scalping Dashboard’s ERS table, but it remains a
current code path and research surface. Its score is conceptually the inverse of the
requested frozen historical selection.

#### Legacy intraday setup detector

`scalping/setup_detector.py` has no pre-session candidate universe. A setup must have
current relative volume, current momentum and target room. Its scores are:

```text
Momentum breakout =
    70 + min(15, current_5-bar_momentum_percent)
       + min(15, current_relative_volume × 3)

Opening-range breakout =
    72 + min(14, current_5-bar_momentum_percent)
       + min(14, current_relative_volume × 3)

VWAP reclaim / first pullback =
    68 + min(16, current_5-bar_momentum_percent)
       + min(16, current_relative_volume × 3)
```

This directly rewards stocks after momentum and a volume spike have appeared. It is
not routed as the active Dashboard page, but it remains reachable code and explains
legacy records and user expectations.

### 1.4 Existing no-chase behavior is incomplete

ERS currently computes:

```text
remaining_highvol_upside =
    (historical p75 expected High - live Last) / live Last × 100
```

If this is below 2%, it emits `RANGE_CONSUMED`. It also rejects continuation above
the configured upper range position. That is useful but incomplete:

- no explicit `MOVE_EXTENDED_DO_NOT_CHASE` state;
- no VWAP extension distance;
- no age of breakout;
- no current range consumed relative to the stock’s historical median
  continuous-session range;
- no target-versus-typical-zone feasibility;
- current session High/Low/Open are absent from the normal ERS snapshot accessor;
- a two-sided quote inside the middle expected range can make continuation `READY`
  without a genuine chronological momentum/retest confirmation.

### 1.5 Paper flow that must be preserved

The good contracts are:

- immutable pre-session CSV and dataset hash;
- live monitor is event-driven and cursor-based;
- duplicate READY activations are suppressed across restarts;
- all WAIT/rejection transitions are recorded, not only selected wins;
- outcome order uses chronological Rubix events;
- a minute that touches target and stop cannot be assigned a fabricated order;
- no new entry is recorded after 14:15 Cairo;
- auction outcomes remain separate;
- fixed +2% / -2% values remain configurable;
- paper is record-only; production, automatic execution and broker orders are off.

The redesign should migrate these contracts, not rewrite old paper rows or reinterpret
the existing `EXPECTED_RANGE_SCALPING_SCORE` as the new historical score.

### 1.6 Missing regression coverage

Existing tests cover liquidity gating, incomplete daily-candle exclusion, immutable
snapshots, duplicate suppression, spread/stale/range-consumed rejection, auction
cutoff and production safety. They do **not** prove:

- live Last/volume/High/Low/momentum/breakout cannot alter historical score/rank;
- a non-candidate rising today cannot enter the frozen universe;
- Streamlit reruns reuse one session-keyed watchlist artifact;
- day D uses data no later than D-1 at the minute-session level;
- auction minute bars cannot affect historical range;
- stable volatility beats equal-mean outlier-driven volatility;
- range stability and zone consistency are independent;
- split/corporate-action sessions cannot create false intraday volatility.

## 2. Historical Rubix intraday availability audit

### 2.1 Source inventory

The only applicable historical intraday store found is:

```text
data/rubix_live_market.db
```

At the audit snapshot it had:

- database file: 514,318,336 bytes, plus an active WAL;
- `candles_1m`: 145,409 rows, 265 symbols, 11 UTC dates;
- `quotes`: approximately 1.47 million rows, 265 symbols, 11 market dates;
- `feed_metrics`: approximately 3.64 million rows.

No `data/scalping_archive` directory exists. No Rubix database copy exists under
`backups/`. The report run datasets contain daily provider reproductions, not a
multi-session Rubix minute archive suitable for this selector.

`2026-07-01` is a one-row zero-price sentinel. July 14, 15 and 20 are materially
under-covered. Seven dates are globally sound enough for the structural audit:

| Session | Collector version | Continuous uptime | Structural range-valid symbols | Authoritative event-valid + range-confirmed |
|---|---|---:|---:|---:|
| 2026-07-16 | pre-patch | 100.0% | 156 | 122 |
| 2026-07-19 | pre-patch | 100.0% | 205 | 157 |
| 2026-07-21 | patched | 100.0% | 208 | 159 |
| 2026-07-22 | patched | 100.0% | 214 | 166 |
| 2026-07-26 | patched | 100.0% | 218 | 181 |
| 2026-07-27 | patched | 100.0% | 219 | 180 |
| 2026-07-28 | patched | 100.0% | 217 | 176 |

For evaluation of session 2026-07-28, the final row must be excluded and the maximum
available history is six sessions through 2026-07-27. The seven-session table is
usable only for a prospective session after 2026-07-28.

Per-session immutable validator hashes:

| Session | Hash |
|---|---|
| 2026-07-16 | `6e22975b0421b568` |
| 2026-07-19 | `a3391042ba44d131` |
| 2026-07-21 | `24c90767e5ac9f50` |
| 2026-07-22 | `c6319279dca229fc` |
| 2026-07-26 | `df0fa5897275a05e` |
| 2026-07-27 | `7954e45bef443adb` |
| 2026-07-28 | `4e8721c35b73cdd9` |

### 2.2 Session and field semantics

All timestamps must be parsed as timezone-aware and converted to
`Africa/Cairo`; fixed UTC offsets must not be used across DST changes.

```text
Continuous session: 10:00 <= Cairo time < 14:15  (255 minute buckets)
Closing auction:    14:15 <= Cairo time < 14:25
```

The audit used `candles_1m` for chronological minute OHLC and `quotes` for event
quality, two-sided spread and cumulative session volume:

- invalid/zero/impossible OHLCV bars are rejected;
- duplicate `(ticker, minute)` rows are rejected/deduplicated deterministically;
- exactly 14:15 and later is excluded from historical continuous metrics;
- session volume is the maximum validated cumulative quote volume, not a sum of
  cumulative candle values;
- trade count is not available and must remain `UNAVAILABLE`, not approximated;
- quote-event count may be disclosed as an activity proxy but must not be labelled
  trade count.

Rubix is event-driven. The diagnostic cohort’s median missing one-minute-bar rate is
about 75%. That does not mean 75% feed loss: liquid symbols can have thousands of
events but only one stored candle per active minute. Missing-minute occupancy must be
reported and softly penalized, while structural trust is controlled by event count,
distinct prices, session span and market-wide material-value gaps.

### 2.3 Structural session validity

A session is structurally valid for a symbol only when all are true:

1. session date is strictly before target session D;
2. continuous phase only; no auction row;
3. at least 60 genuine positive events;
4. at least 5 distinct genuine prices;
5. observed span at least 180 continuous-session minutes;
6. maximum market-wide material-value gap no more than 300 seconds;
7. valid minute OHLC exists and the session open is positive;
8. no duplicate/conflicting minute bars survive;
9. no corporate-action or price-scale anomaly;
10. source-session hash and collector-version provenance are stored.

Liquidity and spread are deliberately **not** part of structural range validity.
They are a later executability filter. This prevents circular threshold selection.

### 2.4 Available structural sessions by symbol

The following is the complete 265-symbol inventory for the seven globally sound
sessions. A count is data availability, not candidate eligibility.

**7 sessions (138):** AALR, ABUK, ACAP, ACGC, ADCI, ADIB, ADPC, ADRI, AFDI,
AFMC, AIDC, AIHC, AJWA, ALCN, ALUM, AMER, AMES, AMIA, AMPI, APSW, ARAB, ARCC,
ASCM, ATQA, AXPH, BINV, BONY, CAED, CANA, CCAP, CCRS, CEFM, CICH, CIEB, CIRA,
CLHO, CNFN, COMI, COPR, CPCI, CSAG, DAPH, DOMT, DSCW, DTPP, EALR, EASB, EAST,
ECAP, EDFM, EEII, EFIC, EFID, EFIH, EGAL, EGAS, EGBE, EGCH, EGTS, EGX30ETF,
EHDR, ELKA, ELSH, EMFD, ENGC, EPCO, ETEL, ETRS, EXPA, FAIT, FCMD, FERC, FIRE,
FNAR, FWRY, GBCO, GDWA, GGCC, GIHD, GMCI, GOUR, GRCA, GSSC, GTWL, HBCO, HDBK,
HELI, HRHO, IBCT, ICFC, ICID, IDRE, IEEC, IFAP, INEG, INFI, IRON, ISMA, ISMQ,
ISPH, JUFO, KABO, KRDI, KWIN, KZPC, LUTS, MAAL, MASR, MBEG, MBSC, MCQE, MENA,
MEPA, MFPC, MFSC, MHOT, MICH, MILS, MIPH, MOED, MOSC, MPCI, MPRC, MTIE, NAPR,
NARE, NCCW, NHPS, NINH, NIPH, OBRI, OCDI, OCPH, ODIN, OFH, OLFI, ORAS, ORHD.

**6 sessions (60):** ACAMD, AIFI, AMOC, ARVA, ASPI, ATLC, BIOC, CRST, ELEC,
EPPK, LCSW, MCRO, MOIL, ORWE, PHAR, PHDC, PHTV, POUL, PRCL, PRDC, QNBE, RACC,
RAYA, RKAZ, ROTO, RREI, RUBX, SAUD, SCEM, SCFM, SCTS, SDTI, SEIG, SIPC, SKPC,
SMFR, SNFC, SPMD, SUGR, SVCE, SWDY, TALM, TANM, TAQA, TMGH, TWSA, TYCN, UEFM,
UEGC, UNIP, UNIT, UPMS, UTOP, VALU, VERT, VLMRA, WCDF, WKOL, ZEOT, ZMID.

**5 sessions (14):** AREH, BIGP, BTFH, COSG, DGTZ, ELNA, ELWA, FTNS, MPCO,
NEDA, RMDA, RTVC, SPIN, UBEE.

**4 sessions (6):** ACTF, CPME, EBSC, KASABF, KORA, PRMH.

**3 sessions (4):** EGREF, GGRN, GPIM, RAKT.

**2 sessions (1):** CERA.

**1 session (3):** CFGH, EGSA, PHGC.

**0 sessions (39):** ACRO, ALEX, APPC, BIDI, DCRC, DEIN, DIFC, EITP, EOSB,
ESAC, ESRS, FAITA, GOCO, GPPL, GTEX, GTHE, HCFI, ICLE, IRAX, MEGM, MISR, MKIT,
MMAT, MOIN, NAHO, NBKE, NCGC, OIH, PACH, RMTV, SAIB, SIMO, SMPP, SNFI, SPHT,
SUCE, TORA, TRTO, UASG.

## 3. Data-distribution report

The percentile cohort contains 218 symbols with at least four structurally valid
sessions. Four is sufficient only to exercise robust formulas; it is far below the
20-session eligibility minimum. Results are therefore labelled **provisional
diagnostics**.

| Metric | N | P10 | P25 | P50 | P75 | P90 |
|---|---:|---:|---:|---:|---:|---:|
| Valid sessions | 218 | 6 | 6 | 7 | 7 | 7 |
| Median continuous range % | 218 | 1.304 | 1.739 | 2.440 | 3.479 | 4.848 |
| Sessions with total range >=2% | 218 | 14.3% | 33.3% | 71.4% | 85.7% | 100.0% |
| Proposed Range Stability | 218 | 48.9 | 56.7 | 63.4 | 72.3 | 77.7 |
| Proposed Zone Consistency | 218 | 51.0 | 56.6 | 62.6 | 68.8 | 73.1 |
| Median cumulative volume | 218 | 52,599 | 264,571 | 975,568 | 3,809,219 | 25,974,334 |
| Median turnover EGP | 218 | 2.45m | 6.41m | 20.10m | 43.93m | 96.78m |
| Median two-sided event ratio | 218 | 99.69% | 99.83% | 99.95% | 100.0% | 100.0% |
| Typical spread % | 218 | 0.127 | 0.204 | 0.317 | 0.499 | 0.750 |
| Spread IQR, percentage points | 218 | 0.004 | 0.023 | 0.068 | 0.143 | 0.247 |
| Median missing-minute rate | 218 | 74.1% | 74.5% | 75.3% | 76.7% | 80.1% |

The spread and liquidity percentiles above use the structurally valid cohort before
applying the inherited EGP 500,000 / 0.6% executability gate. They can therefore
inform, rather than merely reproduce, a new gate.

No eligible-candidate count is reported under the proposed full rules because the
20-session rule correctly yields **zero**.

## 4. Proposed historical metrics

All percentages below are percentage points. All quantiles use deterministic linear
interpolation. All score weights and normalizers belong in one frozen, typed config.
Missing required inputs produce `INSUFFICIENT_DATA`; they are not replaced with a
neutral score.

For each valid completed continuous session \(s\):

```text
R_s = 100 × (High_s - Low_s) / Open_s
U_s = 100 × (High_s - Open_s) / Open_s
L_s = 100 × (Low_s - Open_s) / Open_s        # normally negative
MFE_up_s = U_s
MAE_down_s = -L_s
```

Primary lookback is the most recent 30 valid sessions before D. A secondary 60-session
window is calculated for robustness disclosure. At least 20 valid sessions are
required. The ranking for D must enforce `session_date < D`.

### 4.1 Normal range band

The displayed normal range band is:

```text
[Q25(R), Q75(R)]
```

The report also includes median, mean (disclosure only), IQR, raw MAD, P90, maximum,
sessions inside the inclusive Q25–Q75 band and Tukey outliers outside
`[Q25 - 1.5×IQR, Q75 + 1.5×IQR]`.

Maximum is never called “normal.”

### 4.2 Historical Intraday Movement Potential

Cross-sectional percentile rank uses the complete in-scope equity universe at the
same cutoff, with average ranks for ties.

```text
RangeLevel =
    percentile_rank(median(R))

RangeHit2 =
    100 × P(R >= 2%)

DirectionalOpportunity =
    100 × max(P(U >= 2%), P(-L >= 2%))

TimeReliability =
    100 × P(|excursion from open| reaches 1%)
        × clip(1 - 1.4826×MAD(time_to_first_abs_1pct) / 90 minutes, 0, 1)

HistoricalIntradayMovementPotential =
    0.35×RangeLevel
  + 0.35×RangeHit2
  + 0.20×DirectionalOpportunity
  + 0.10×TimeReliability
```

`DirectionalOpportunity` is suitability disclosure, not permission to short.
Production/broker execution remains disabled and the existing live system is long-only.

### 4.3 Range Stability Score / ثبات نطاق الحركة

Let \(m = median(R)\), `MAD = median(|R-m|)`, `IQR = Q75-Q25`.

```text
MADComponent =
    100 × clip(1 - ((1.4826×MAD / m) / 0.75), 0, 1)

IQRComponent =
    100 × clip(1 - ((IQR / m) / 1.50), 0, 1)

BandCapture =
    100 × P(Q25 <= R <= Q75)

OutlierControl =
    100 × (1 - Tukey_outlier_rate)

MeanMedianControl =
    100 × clip(1 - ((abs(mean(R)-m) / m) / 1.00), 0, 1)

UsefulRangeFrequency =
    100 × P(R >= configured_minimum_useful_range)

RangeStability =
    0.25×MADComponent
  + 0.20×IQRComponent
  + 0.15×BandCapture
  + 0.15×OutlierControl
  + 0.10×MeanMedianControl
  + 0.15×UsefulRangeFrequency
```

The 0.75, 1.50 and 1.00 normalizers are versioned config values. This construction
causes `[2.6, 2.8, 3.0, 3.1, 3.3]` to beat
`[0.4, 0.6, 0.8, 0.7, 10.5]`: the second vector is penalized by robust dispersion,
mean/median separation, a Tukey outlier and low useful-range frequency.

Interpretation remains:

- 80–100: very stable;
- 65–79: good;
- 45–64: moderate/variable;
- below 45: unstable/event-driven.

### 4.4 Volatility Zone Consistency / ثبات منطقة التذبذب

For axis \(X\), where \(X=U\) or \(X=L\):

```text
robust_scale(X) =
    max(1.4826×MAD(X), IQR(X)/1.349, 0.05 percentage points)

Typical zone(X) =
    median(X) ± 1.5×robust_scale(X)
```

The upper zone lower bound is clipped at zero; the lower zone upper bound is clipped
at zero.

```text
AxisDispersion(X) =
    100 × clip(
        1 - (
            robust_scale(X) / max(abs(median(X)), 0.25)
        ) / 1.50,
        0, 1
    )

AxisCapture(X) =
    100 × P(X lies in Typical zone(X))

EnvelopeCapture =
    100 × P(U in upper zone AND L in lower zone)

EnvelopeOutlierControl =
    100 × (1 - P(
        |U-median(U)| > 3×scale(U)
        OR |L-median(L)| > 3×scale(L)
    ))

ZoneConsistency =
    0.20×AxisDispersion(U)
  + 0.20×AxisDispersion(L)
  + 0.15×AxisCapture(U)
  + 0.15×AxisCapture(L)
  + 0.20×EnvelopeCapture
  + 0.10×EnvelopeOutlierControl
```

The 0.05, 0.25, 1.50 and 3.00 values are typed config, not scattered constants.
Range Stability and Zone Consistency are separate: the first scores repeatability of
range width; the second scores where the high and low occur relative to the open.

### 4.5 Target frequency, path and timing disclosure

For each stock disclose:

- `P(R >= 1%, 1.5%, 2%, 2.5%, 3%)`;
- `P(U >= 2%)`, `P(-L >= 2%)`;
- median MFE Up and median MAE Down;
- first +2% versus first -2% using chronological minute High/Low;
- if one minute touches both, classify `AMBIGUOUS_SAME_BAR`; do not infer order and
  exclude it from directional first-touch denominators while reporting the count;
- median time of High and Low;
- median time to first +1%, +2%, -1%, -2%;
- fraction of full session range formed by minute 15, 30, 60, midday end and final
  continuous minute;
- no bar at or after 14:15 participates.

### 4.6 Liquidity and Executability Score

The historical liquidity score is:

```text
TurnoverRank =
    percentile_rank(log1p(median_session_turnover))

VolumeRank =
    percentile_rank(log1p(median_cumulative_volume))

LiquidityPassRate =
    100 × P(
        session_turnover >= configured minimum
        AND session_median_spread <= configured historical maximum
        AND two_sided_event_ratio >= configured minimum
    )

SpreadLevel =
    100 × clip(1 - median_historical_spread / max_historical_spread, 0, 1)

SpreadStability =
    100 × clip(1 - spread_IQR / 0.25 percentage points, 0, 1)

DataQuality =
    0.70 × 100×(structurally_valid_sessions / observed_completed_sessions)
  + 0.30 × percentile_rank(active_minute_ratio)

LiquidityExecutability =
    0.30×TurnoverRank
  + 0.15×VolumeRank
  + 0.15×LiquidityPassRate
  + 0.20×SpreadLevel
  + 0.10×SpreadStability
  + 0.10×DataQuality
```

The active-minute term is cross-sectional because absolute minute occupancy is not a
valid standalone quality gate for this event-driven source.

### 4.7 Historical Scalping Potential Score

```text
HistoricalScalpingPotential =
    0.40×HistoricalIntradayMovementPotential
  + 0.25×RangeStability
  + 0.20×ZoneConsistency
  + 0.15×LiquidityExecutability
```

Metric versions:

```text
historical_scalping_score@1
range_stability@1
volatility_zone_consistency@1
liquidity_executability@1
live_entry_readiness@1
```

The typed config must validate each sub-score and top-level weight set sums to exactly
1.0 within a fixed decimal tolerance.

## 5. Provisional eligibility thresholds

These are evidence-informed starting values for later walk-forward validation. They
are **not final** because every symbol is below the 20-session minimum.

| Rule | Provisional value | Evidence / reason |
|---|---:|---|
| Valid sessions | >=20 | explicit minimum; do not relax |
| Primary / robustness lookback | 30 / 60 | requested architecture |
| Median continuous range | >=1.75% | approximately current P25 1.739% |
| 2% range-hit frequency | >=35% | approximately current P25 33.3% |
| Range Stability | >=60 | upper-moderate; below current median 63.4 |
| Zone Consistency | >=60 | near current median 62.6 |
| Median turnover | >=EGP 5m | between P10 2.45m and P25 6.41m |
| Median cumulative volume | >=100,000 | secondary only; turnover is primary |
| Historical median spread | <=0.50% | approximately current P75 0.499% |
| Sessions with spread <=0.60% | >=80% | matches current live cap |
| Historical two-sided events | >=80% | inherited event-quality minimum |
| Genuine events per session | >=60 | inherited structural minimum |
| Distinct prices per session | >=5 | rejects one-price heartbeat streams |
| Observed continuous span | >=180 min | inherited structural minimum |
| Material-value gap | <=300 sec | inherited range-confidence threshold |
| Median missing-minute rate | <=85% | safety ceiling; never the sole gate |
| Instrument | verified common equity | ETFs/funds/non-equities separately excluded/configured |

The 85% missing-minute ceiling is intentionally above the current P90 of 80.1% and
must be combined with event/range validity. It is not a replacement for the
event-driven quality model.

Corporate-action sessions are excluded until one deterministic adjustment factor is
applied consistently to every OHLC minute in the affected history. Raw and adjusted
bars must never be mixed within a score window.

Under all rules above, current eligible candidates are **0**, solely because
`valid_sessions < 20`.

## 6. Proposed two-engine architecture

### 6.1 Historical selection engine

Inputs:

- Rubix `candles_1m`, `quotes`, `feed_metrics`;
- target session D;
- verified equity universe;
- completed-session calendar;
- metric/config versions;
- source identity and per-session hashes.

Output is immutable, pre-session and contains no live-day fields:

```text
symbol, frozen_rank, historical_score,
movement_score, range_stability, zone_consistency, liquidity_score,
median_range, q25_range, q75_range, 2pct_hit_frequency,
typical_upper_zone, typical_lower_zone,
valid_session_count, data_cutoff_session,
generated_at, source_identity, metric_versions, config_hash
```

### 6.2 Live readiness engine

It accepts only symbols in the frozen artifact. It may use current spread/freshness,
current cumulative volume versus historical time-of-day norms, VWAP, opening range,
chronological breakout/retest evidence, current continuous range, distance to
historical zones, remaining movement capacity and invalidation.

Suggested transparent readiness components:

```text
25% trigger structure / confirmation
20% time-of-day relative volume confirmation
15% VWAP / retest quality
15% spread and quote freshness
15% remaining historical movement capacity
10% session timing suitability
```

Hard states take precedence over the number:

```text
HISTORICAL_CANDIDATE_WAITING
ENTRY_TRIGGER_FORMING
ENTRY_READY
BREAKOUT_UNCONFIRMED
MOVE_EXTENDED_DO_NOT_CHASE
SPREAD_TOO_WIDE
LIQUIDITY_INSUFFICIENT
DATA_STALE
OPPORTUNITY_INVALIDATED
SESSION_PHASE_BLOCKED
CLOSING_AUCTION_NO_NEW_ENTRY
```

`MOVE_EXTENDED_DO_NOT_CHASE` should trigger when any configured hard condition is
met, including consumed normal range, excessive VWAP extension, stale breakout,
insufficient net target room, widened spread or proximity to the historical upper
zone. Historical quality never overrides a live block.

### 6.3 Frozen-watchlist lifecycle

1. Resolve target session D and Cairo calendar.
2. Set immutable cutoff to the latest completed session strictly before D.
3. Build/cache historical features by
   `(symbol, cutoff, lookback, metric_version, config_hash, source_identity)`.
4. Apply hard eligibility rules.
5. Rank and persist one session-keyed watchlist artifact.
6. If an artifact with the same identity exists, Streamlit reruns load it; they do
   not recalculate history.
7. Live monitor receives membership/rank/scores by value from the artifact and cannot
   add a symbol.
8. An explicit “Rebuild historical watchlist” action may create a new version only
   from completed sessions before D. It must show the old/new cutoff and reason.
9. A newly completed session can generate the next session’s artifact; it never
   silently mutates the current session’s artifact.
10. Paper signals copy metric versions, config hash, cutoff and watchlist hash.

## 7. Leakage controls and required tests

The implementation gate requires:

- mutate current price change, volume, High/Low, momentum, breakout and live quote;
  historical score/rank/membership remain byte-identical;
- a non-candidate rising 5% today remains outside the watchlist;
- D uses only dates `< D`;
- 14:15–14:25 changes do not alter any historical metric;
- current incomplete D is excluded even after a Streamlit rerun;
- the exact stable-versus-erratic vectors rank in the required order;
- mean-inflating outliers are penalized;
- range and zone stability vary independently in synthetic fixtures;
- split/corporate-action fixtures do not create false range;
- low-liquidity jumps fail executability;
- missing/invalid sessions produce honest insufficient status;
- one-minute both-level touches are ambiguous;
- frozen artifact identity survives reruns/restarts;
- explicit rebuild is required;
- old paper rows remain unchanged;
- new rows carry metric versions;
- no Yahoo operational call or fallback;
- no broker order and all production flags remain false.

Walk-forward validation for session D must build through D-1, freeze, and only then
replay D. In-sample results cannot be used for acceptance.

## 8. Expected Dashboard changes

The combined `Score` column must be removed from the decision surface and replaced by
two explicitly labelled values.

### Historical Candidate Watchlist

Default sort is frozen rank. Columns:

```text
rank, symbol, Historical Scalping Potential,
median range, Q25–Q75 normal range,
2% range frequency, Range Stability, Zone Consistency,
historical liquidity score, lower/upper excursion zones,
valid sessions, data cutoff, historical status
```

Allowed alternate historical sorts are Range Stability, Zone Consistency and 2% hit
frequency. No live Last, price change or remaining-room value changes this table’s
membership or default order.

### Live Entry Monitor

Only frozen members appear. Columns:

```text
symbol, frozen rank, Live Entry Readiness, live state,
Last, current range, remaining movement capacity,
VWAP state, volume confirmation, spread,
entry, target, stop, risk/reward, invalidation, no-chase warning
```

Live readiness is a separate sort option. Every row gets a deterministic “Why
selected historically” and “Live status” panel populated from numeric fields, not
free-form AI prose.

## 9. Proposed file architecture

Following the existing package conventions, implementation is expected to add:

- `scalping_expected_range/models.py`
- `scalping_expected_range/historical_intraday_repository.py`
- `scalping_expected_range/historical_metrics.py`
- `scalping_expected_range/historical_scoring.py`
- `scalping_expected_range/live_readiness.py`
- `services/scalping_watchlist_service.py`

Expected focused edits:

- `scalping_expected_range/config.py`
- `scalping_expected_range/settings.json`
- `scalping_expected_range/scanner.py` (compatibility adapter; no mixed score)
- `scalping_expected_range/scenario_engine.py`
- `scalping_expected_range/live_monitor.py`
- `scalping_expected_range/paper_recorder.py`
- `scalping_expected_range/paper_state.py`
- `scripts/run_expected_range_pre_session.py`
- `scripts/run_expected_range_live_monitor.py`
- `scripts/run_expected_range_paper_session.py`
- `dashboard/scalping.py`
- `dashboard/expected_range_scalper.py`
- `dashboard/opportunities.py`

Expected new/updated tests:

- `tests/test_scalping_historical_metrics.py`
- `tests/test_scalping_historical_selector.py`
- `tests/test_scalping_live_readiness.py`
- `tests/test_scalping_watchlist_service.py`
- existing ERS orchestration, paper, UI, Rubix source and strategy-regression suites.

The following remain out of scope: Swing/Daily, existing Market Scan selection, AI
Stock Analysis calculations, Rubix collector protocol, archive format, broker/order
execution and Production Disabled safeguards.

## 10. Checkpoint decision

The design is ready for review, but implementation and activation are blocked by the
explicit checkpoint and by data sufficiency:

- strategy logic changed: **no**;
- thresholds finalized: **no**;
- minimum 20 Rubix sessions available: **no (0/265)**;
- eligible historical watchlist today: **none**;
- next safe step after approval: implement the versioned engines and tests so they
  return `HISTORICAL_DATA_INSUFFICIENT` until enough completed sessions accumulate,
  then recalibrate thresholds using at least 20 sessions and walk-forward validation.
