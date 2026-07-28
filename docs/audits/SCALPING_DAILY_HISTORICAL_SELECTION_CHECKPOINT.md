# Scalping Daily Historical Selection — Revised Checkpoint

**Status:** BASELINE IMPLEMENTED AND CALIBRATED; DASHBOARD AND LIVE ENTRY NOT
IMPLEMENTED

**Audit date:** 2026-07-28 (Africa/Cairo)

**Branch:** `fix/scalping-historical-volatility-selection`

**Data cutoff:** 2026-07-27 (D-1)

**Source:** `EODHD_DAILY` only

**Metric version:** `DAILY_HISTORICAL_SELECTION_V1`

**Config version:** `DAILY_HISTORICAL_SELECTION_CONFIG_V1`

## Decision

The baseline historical selector can run now from completed EODHD daily OHLCV.
Rubix historical intraday data is an optional enrichment layer and its
20-completed-session gate does not block the daily selector.

The implemented separation is:

| Layer | Provider | Readiness | Permitted use |
|---|---|---|---|
| Historical daily selection | EODHD Daily | minimum 30, preferred 60 sessions | frozen historical score, rank and candidate membership |
| Historical intraday enrichment | Rubix completed sessions | minimum 20 sessions | first-touch, time-of-day, continuous-only, auction-adjusted and path-confidence metrics |
| Live entry monitor | Rubix current session | not implemented in this checkpoint | future readiness and no-chase states after the daily list is frozen |

Yahoo is not imported or called by the new loader. It is not a fallback and is
not a ranking input.

## Implemented scope

The new `scalping_expected_range/daily_historical_selection.py` module provides:

- a direct EODHD daily loader using the existing split-price adjustment and
  event-specific volume reconciliation;
- independent daily and intraday readiness states;
- completed-session filtering through an explicit D-1 cutoff;
- daily metrics, deterministic component scores and the weighted Historical
  Scalping Potential;
- an immutable, fingerprinted historical snapshot with no live inputs;
- honest unavailable and insufficient states with `None` scores, never an
  invented zero.

The typed configuration is in `scalping_expected_range/config.py`. It contains
no current price change, live volume, live spread, breakout, momentum, RVOL or
Rubix quote fields.

No Streamlit page, live-entry path, portfolio path, paper-trade record,
production flag, broker flag, strategy execution path or provider-routing
fallback was changed.

## Daily metric contract

For each valid completed EODHD daily session:

```text
Full-Session Daily Range % = (High - Low) / Open * 100
Upper Excursion %          = (High - Open) / Open * 100
Lower Excursion %          = (Low - Open) / Open * 100
Close Change %             = (Close - Open) / Open * 100
```

The stored aggregates include session count, mean and median range, P25, P75,
IQR, MAD, P90, maximum, 1%/1.5%/2%/2.5%/3% hit frequencies, median upper and
lower excursions, upper/lower MAD, outlier count and rate, mean/median
divergence, normal-band frequency, volume, turnover and volume consistency.
Close change is calculated and validated but does not enter any score.

The terminology is intentionally **Full-Session Daily Range**, **Daily Range
Stability** and **Daily Excursion Zone**. Every result carries this disclosure:

> Daily historical metrics may include official closing-auction effects.
> Continuous-session-only historical enrichment is not yet available.

Daily OHLC does not reveal High/Low ordering. The result also states that no
first-touch target/stop claim is made.

## Deterministic scoring

### Daily Movement Potential

Movement combines a clipped median-range component with the five range-hit
frequencies. It rewards recurring useful movement rather than a single maximum.

### Daily Range Stability

The separate 0–100 stability score combines:

- MAD / median;
- IQR / median;
- proportion inside a robust median/MAD band;
- outlier rate;
- mean/median divergence;
- median useful width and the frequency of at least 1.5% movement.

The focused test proves that the stable sequence
`2.6, 2.8, 3.0, 3.1, 3.3` scores above an equal-mean erratic sequence. Current
session data is not accepted by this calculation.

### Daily Volatility Zone Consistency

The separate 0–100 zone score evaluates upper and lower excursions
independently using median-relative MAD, IQR, robust-envelope frequency and
outlier rate. It measures repeatability of the High/Low zones around the Open;
it is not a duplicate of range stability.

### Daily Liquidity

Liquidity uses median turnover, median volume, volume consistency and non-zero
frequency. If a corporate action inside the 60-session window has an
`EVENT_SPECIFIC` or `UNRESOLVED` volume policy, the liquidity component and
composite score are unavailable rather than guessed.

### Historical Scalping Potential

The weights are stored together in `DailyHistoricalScoreWeights`:

| Component | Weight |
|---|---:|
| Daily Movement Potential | 40% |
| Daily Range Stability | 25% |
| Daily Volatility Zone Consistency | 20% |
| Daily Liquidity | 15% |

No live or current-session term is present.

## Verified 225-symbol depth

The checkpoint used the 225-symbol verified mapping in
`reports/eodhd/full_universe_symbol_results.csv`, the existing complete EODHD
cache, and `reports/eodhd/corporate_action_reconciliation.csv`. The calculation
read 450 cached EOD and split documents and made zero network requests.

| Result | Symbols |
|---|---:|
| EODHD daily history loaded | 225 / 225 |
| At least 30 valid sessions | **225 / 225** |
| At least 60 valid sessions | **221 / 225** |
| Composite score available | **216 / 225** |
| Recent volume policy blocks liquidity/composite | **9 / 225** |

The four histories below the preferred 60-session depth are:

| Symbol | Valid sessions |
|---|---:|
| KORA | 30 |
| UTOP | 54 |
| TWSA | 57 |
| TYCN | 57 |

The nine score-blocked symbols and latest blocking events are:

| Symbol | Blocking event date |
|---|---|
| AMES | 2026-04-28 |
| ASPI | 2026-06-01 |
| DEIN | 2026-07-08 |
| EGBE | 2026-06-02 |
| HDBK | 2026-07-08 |
| KZPC | 2026-06-25 |
| MHOT | 2026-07-08 |
| NINH | 2026-05-14 |
| SVCE | 2026-04-23 |

This is a liquidity-provenance block, not a zero historical score.

## Missing-40 correction

The earlier 225/265 result measured EODHD **exchange-list catalogue matching**,
not actual direct daily-endpoint availability. Each of the 40 catalogue-missing
project symbols received a bounded read-only probe against:

```text
/eod/{symbol}.EGX?filter=last_date
/search/{symbol}?limit=10
```

No Yahoo request was made. All 40 searches returned no EGX alias/ISIN candidate,
so there is no evidence for an alternate mapped code or exchange suffix.

The exact reason categories are:

| Category | Count | Symbols |
|---|---:|---|
| Current daily endpoint works under the existing `.EGX` symbol despite catalogue omission | 16 | ACRO, ALEX, APPC, DCRC, EITP, GOCO, GTHE, IRAX, NBKE, NCGC, PACH, RMTV, SMPP, SUCE, TORA, UASG |
| Inactive/historical endpoint; latest bar is no longer current | 13 | ADRI, AMPI, BIDI, BIGP, ESRS, FIRE, FNAR, IBCT, INEG, MKIT, RKAZ, UPMS, VERT |
| Non-equity exclusion | 1 | EGX30ETF |
| True absent or empty EODHD daily endpoint | 10 | AIFI, AIHC, DIFC, ELWA, ESAC, FCMD, HCFI, MISR, MMAT, SNFI |
| Alternate EODHD symbol/suffix supported by search/ISIN evidence | 0 | none |

The inactive endpoint dates were:

| Latest date | Symbols |
|---|---|
| 2025-01-06 | ADRI, AMPI, BIDI, BIGP, FIRE, FNAR, IBCT, INEG, MKIT, RKAZ, UPMS, VERT |
| 2025-03-12 | ESRS |

Consequently, current EODHD daily data-bearing coverage is **241/265**, not
225/265: the verified 225 plus 16 directly recoverable current symbols. All
241 have at least 30 sessions and 237 have at least 60. The 16 recoveries are
the same provider and suffix, but they are not silently inserted into the
existing verified mapping: an explicit mapping/universe update is required
before an operational artifact includes them.

The 13 inactive symbols, one non-equity instrument and ten truly absent/empty
symbols are not eligible current equities. A genuinely unavailable input is
represented as `HISTORICAL_DAILY_DATA_UNAVAILABLE`, with no rank and no score.

## Requested distributions on the verified 225

The percentile table uses the latest 60 valid sessions through 2026-07-27
(or all available sessions when 30–59). Liquidity and composite distributions
contain 216 symbols because the nine unsafe volume histories are excluded.

| Metric | N | Min | P10 | P25 | Median | P75 | P90 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Median daily range % | 225 | 0.0000 | 1.8663 | 2.3907 | 3.0289 | 3.7394 | 4.4811 | 6.4767 |
| Daily Range Stability | 225 | 0.0000 | 51.6664 | 61.9173 | 69.4482 | 75.2545 | 79.0774 | 86.7112 |
| Daily Zone Consistency | 225 | 18.1369 | 30.5198 | 35.0706 | 38.5775 | 44.2698 | 51.4417 | 100.0000 |
| Daily Liquidity | 216 | 0.0000 | 27.8158 | 53.1957 | 75.0427 | 88.3862 | 96.5352 | 99.3333 |
| Median turnover EGP | 225 | 0 | 535,328 | 4,959,720 | 11,590,913 | 29,682,826 | 81,804,216 | 709,100,785 |
| 2% range-hit frequency | 225 | 0.0000 | 0.4400 | 0.6333 | 0.8000 | 0.9000 | 0.9500 | 1.0000 |
| Historical Scalping Potential | 216 | 20.0000 | 47.0160 | 59.6870 | 67.3206 | 73.7452 | 77.9849 | 83.2774 |

## Proposed baseline eligibility

The versioned initial gates are:

| Gate | Threshold |
|---|---:|
| Valid completed sessions | at least 30 |
| Historical Scalping Potential | at least 65 |
| Median daily range | at least 1.5% |
| 2% range-hit frequency | at least 30% |
| Daily Range Stability | at least 50 |
| Daily Zone Consistency | at least 45 |
| Daily Liquidity | at least 40 |
| Median turnover | at least EGP 1,000,000 |

These gates produce **16 baseline historical candidates** on the verified
225-symbol checkpoint. A zone threshold of 45 is deliberately selective: with
the other gates fixed, score/zone combinations over the expanded data-bearing
scope produced 27 candidates at 55/45, 24 at 60/45, 16 at 65/45 and 13 at
70/45. At score 65, lowering the zone gate to 40 produced 39, while raising it
to 50 produced 6.

This is a transparent calibration checkpoint, not a claim that the thresholds
are statistically optimal.

## Frozen candidate evidence

The offline checkpoint snapshot ID is:

```text
7cac64ec24a87b589202d7832e59a681aecbd7cf20fe14c22d4e1ea40c56e26c
```

The rank is the rank among all 216 scoreable histories; eligibility gates are
then applied without re-ranking:

| Historical rank | Symbol | Score | Median range % | 2% frequency | Stability | Zone | Liquidity |
|---:|---|---:|---:|---:|---:|---:|---:|
| 4 | ARAB | 80.9488 | 4.7619 | 0.7500 | 69.2109 | 62.7306 | 97.3333 |
| 5 | ZMID | 80.3976 | 3.2058 | 0.9000 | 84.9132 | 46.2187 | 99.3333 |
| 11 | VALU | 79.5668 | 3.3237 | 0.9167 | 82.8283 | 47.7976 | 87.5782 |
| 16 | MPRC | 78.8507 | 3.3321 | 0.9167 | 83.0069 | 47.8597 | 81.2759 |
| 20 | GBCO | 78.3625 | 3.2116 | 0.9000 | 79.1904 | 46.3265 | 96.9776 |
| 31 | COSG | 77.2167 | 3.1749 | 0.8667 | 77.5209 | 46.1743 | 94.3248 |
| 48 | CERA | 74.6455 | 2.9852 | 0.8667 | 81.6375 | 47.5763 | 81.9624 |
| 68 | RTVC | 72.6003 | 3.0651 | 0.8833 | 77.9886 | 51.1828 | 64.4088 |
| 75 | UNIP | 70.8209 | 3.0777 | 0.7167 | 64.4524 | 57.0253 | 77.5723 |
| 77 | KRDI | 70.5766 | 2.8571 | 0.7333 | 62.1166 | 69.8462 | 83.7965 |
| 78 | MENA | 70.4892 | 3.2522 | 0.8667 | 63.4350 | 51.3086 | 62.1631 |
| 83 | SIPC | 70.3024 | 2.7616 | 0.7833 | 79.0680 | 48.0963 | 77.0725 |
| 86 | DSCW | 70.0703 | 2.6181 | 0.7500 | 74.8876 | 46.8586 | 95.6764 |
| 112 | IRON | 67.0084 | 2.7428 | 0.7333 | 77.3582 | 46.7267 | 64.8596 |
| 125 | SPMD | 65.4170 | 2.5641 | 0.9667 | 57.0013 | 52.8472 | 83.8158 |
| 129 | SDTI | 65.1472 | 2.5648 | 0.8000 | 77.2149 | 45.7903 | 63.6072 |

The checkpoint calculation does not write or activate this candidate list as a
runtime artifact. An explicit future rebuild action is still required.

Every per-symbol result records:

- source provider and interval;
- raw/adjusted mode;
- source-data SHA-256 fingerprint;
- D-1 data cutoff;
- Rubix overlap-validation status;
- metric and configuration versions.

The snapshot hash excludes generation time but includes the source
fingerprints, scores, ranks, eligibility flags, cutoff and versions. Rebuilding
with identical completed daily data is stable. Today’s bar and arbitrary live
Rubix metadata are excluded and cannot change the snapshot ID, score, rank or
membership.

## Independent intraday readiness

At approximately seven valid Rubix completed sessions:

```text
DAILY_HISTORICAL_SELECTION_READY
INTRADAY_HISTORICAL_ENRICHMENT_NOT_READY
required_intraday_sessions = 20
```

Only these enrichment metrics remain unavailable:

- first-touch target/stop statistics;
- time-of-day consistency;
- continuous-session-only historical range;
- auction-adjusted historical metrics;
- intraday path confidence.

The baseline daily ranking is not blocked.

## Validation

Focused daily selector tests:

```text
19 passed
```

Focused plus Expected Range, paper, orchestration, freshness, Range Scalper,
session-validation and EODHD adjustment regressions:

```text
128 passed
```

Complete existing EODHD provider regression group:

```text
71 passed
```

The tests prove all requested isolation properties, including D-1 filtering,
30-session readiness, insufficient/unavailable handling, stable-versus-spiky
scoring, separate zone consistency, immutable live-independent ranking, no
Yahoo fallback, no first-touch claim, auction disclosure and disabled
production/broker execution.

## Stop condition

This branch stops at the revised daily-data checkpoint. It does not implement:

- Historical Daily Selection dashboard panels;
- Intraday Historical Enrichment panels;
- Rubix Live Entry Monitor changes;
- live entry rules, no-chase rules or current-session reordering;
- trading, portfolio, production, automatic execution or broker behavior.

Those remain outside the approved checkpoint.
