# Scalping Daily Selector — Phase 2A Validation

**Status:** VALIDATED FOR HISTORICAL SELECTION ONLY; WATCHLIST PERSISTENCE AND
LIVE ENTRY NOT IMPLEMENTED

**Audit date:** 2026-07-28 (Africa/Cairo)

**Branch:** `fix/scalping-historical-volatility-selection`

**Validated through:** commit `79281e1` plus this documentation commit

**Data cutoff:** 2026-07-27 (D-1)

**Provider:** `EODHD_DAILY`, interval `1d`, split-adjusted price series

**Metric/config versions:** `DAILY_HISTORICAL_SELECTION_V1` /
`DAILY_HISTORICAL_SELECTION_CONFIG_V1`

## Decision

The daily historical selector is deterministic, provider-locked, cutoff-safe
and sufficiently defended to remain the approved historical-selection
checkpoint. It is not approved as a live signal or trading implementation.

The current 16-name result is **highly sensitive to the Daily Volatility Zone
Consistency gate** and insensitive over the tested neighborhoods of the other
gates. It is therefore not evidence that the thresholds are statistically
optimal. Preserve the disclosed baseline thresholds for the next research
phase; do not tune them merely to retain 15–20 names.

Use **60 completed sessions as the primary history and 30 sessions as a recent
confirmation**, not a blended score. The 60-session result is materially more
stable in the walk-forward dry run.

## Validation boundary and data safety

This phase performed no Market Scan, provider write, paper record, broker
order, production execution, watchlist persistence, live-entry implementation
or Yahoo request. All provider credentials were blanked for test and UI smoke
commands. Detailed selector evidence was reproduced through a cache-only client
that raises on a cache miss and cannot make a live call.

The Streamlit smoke used a no-op Rubix path under the operating-system temporary
directory. It listened on port 8765, returned HTTP 200 from both
`/_stcore/health` and `/`, and its exact process was terminated. The existing
port 8501 owner remained PID 26988 before and after the probe. Ignored launcher
logs and dataset-finalizer lock fixtures created by repository tests inside the
isolated worktree were identified and removed; no runtime database or report
artifact remains there.

The two pre-existing, uncommitted main-worktree files were not committed,
reverted or overwritten:

| File | Git blob hash | Working-file SHA-256 |
|---|---|---|
| `data/paper_trades.csv` | `323bc8b6c8547720d30e38ba3cab024ad55bb080` | `09d3b7f66f6e32a8bc7dd9fc0e21007899388cec2ab57976b3de38c90ddfa68d` |
| `docs/audits/strategies/SCALPING_MULTI_SESSION_VERDICT.md` | `640d9c0e98154d75e983916d6b04480e7cdc2b3d` | `37b4b58231937ce69da3360c1f8e1d03b784389f41fbd3630fc6b677b80a7eed` |

## Full and scoped validation

| Validation | Result |
|---|---:|
| Complete repository suite | **1436 passed, 7 skipped in 58.69s** |
| Daily historical selector | **48 passed** |
| Selector plus Expected Range Scalper | **122 passed** |
| Existing scalping plus Range Scanner | **40 passed** |
| Dashboard, UI and launcher | **344 passed** |
| Paper and production safety | **99 passed** |
| EODHD and provider policy | **118 passed, 7 skipped** |
| Import smoke | `IMPORT_SMOKE_OK` |
| Streamlit temporary-port smoke | health 200, root 200 |
| `git diff --check` | passed |

The seven skips are explained and are not selector failures:

- `tests/test_current_research_correctness.py::test_real_corrected_frames_have_required_provenance`
  skips six parameters (`COMI`, `EAST`, `SWDY`, `KZPC`, `UNIP`, `ORAS`)
  because the isolated worktree has no matching real EODHD cache documents.
- `tests/test_current_research_correctness.py::test_kzpc_event_window_preserves_real_raw_volume`
  skips because the KZPC real cache document is not present in the isolated
  worktree.

The full suite initially exposed one test-isolation defect:
`test_dashboard_route_falls_back_and_backtest_stays_on_yahoo` wrote its frozen
legacy fixture to a temporary cache but the code under test instantiated a
different default cache. The test now injects that same temporary cache. No
provider-routing or Yahoo operational behavior changed.

## Coverage reconciliation

Coverage is reported in three explicit populations:

| Population | Count | Meaning |
|---|---:|---|
| Original universe | 265 | Project symbol universe |
| Original catalogue-unresolved set | 40 | Absent from the EODHD exchange listing |
| Direct daily endpoint recoveries | 16 | Historical data returned under the existing `.EGX` code |
| Remaining outside normal coverage | 24 | 13 inactive, 1 non-equity, 10 unavailable |
| Endpoint data-bearing coverage | **241 / 265** | 225 verified plus 16 direct recoveries |
| Strict distribution population | **225** | Original verified mapping, analyzed separately |

The remaining 24 are:

| Category | Count | Symbols |
|---|---:|---|
| Inactive | 13 | ADRI, AMPI, BIDI, BIGP, ESRS, FIRE, FNAR, IBCT, INEG, MKIT, RKAZ, UPMS, VERT |
| Non-equity | 1 | EGX30ETF |
| Unavailable/empty endpoint | 10 | AIFI, AIHC, DIFC, ELWA, ESAC, FCMD, HCFI, MISR, MMAT, SNFI |

### Strict 225-symbol readiness

With positive volume required, duplicate local dates rejected, explicit D-1
filtering and a ten-calendar-day staleness gate:

| Result | Count |
|---|---:|
| Current-ready | **219 / 225** |
| Preferred 60-session depth among current-ready | **215 / 219** |
| Current-ready and scoreable | **211 / 225** |
| Stale | 4: GPPL, SAIB, SIMO, SPHT |
| Insufficient | 1: DEIN (24 usable positive-volume sessions in the bounded raw audit) |
| Unavailable positive-volume history | 1: MEGM |
| Volume-policy unresolved | 9, with DEIN overlapping the insufficient set |

The current snapshot ID is:

```text
deddfda21bcc6518114d329e207984a10d187eb7b7dd33f74ec2d8d062a38790
```

### Separate expanded 16-symbol analysis

The 16 direct endpoints can pass through the same price-adjustment and metric
pipeline, but none is current-ready at the 2026-07-27 cutoff. Their latest
positive-volume sessions are:

| Symbol | Latest positive-volume session | Symbol | Latest positive-volume session |
|---|---|---|---|
| ACRO | 2026-02-11 | ALEX | 2020-08-06 |
| APPC | 2021-08-25 | DCRC | 2024-06-03 |
| EITP | 2020-07-27 | GOCO | 2025-10-16 |
| GTHE | 2019-08-27 | IRAX | 2023-11-15 |
| NBKE | 2023-11-15 | NCGC | 2011-12-15 |
| PACH | 2023-11-16 | RMTV | 2021-12-16 (one positive-volume session) |
| SMPP | 2021-11-28 | SUCE | 2021-02-07 |
| TORA | 2021-02-03 | UASG | 2024-10-28 |

Without a freshness gate, NCGC's 2011 observations falsely add a seventeenth
candidate. The strict stale status prevents that error. Thus `241/265` remains
the correct endpoint coverage, while the 16 recoveries contribute zero
current-ready symbols. These populations are not mixed.

## Sixteen-candidate evidence

All fields below are historical. There is no current quote, current change,
current high/low, live volume, RVOL, spread, VWAP, breakout, momentum or Rubix
freshness field.

All 16 are EODHD common stocks with 60 valid completed sessions through
2026-07-27. All eight configured gates pass. `Normal band` is the disclosed
interquartile range `[P25, P75]`, not a prediction interval.

| Rank | Symbol | Instrument | Score | Movement | Stability | Zone | Liquidity | Fingerprint prefix |
|---:|---|---|---:|---:|---:|---:|---:|---|
| 5 | ARAB | Common stock | 80.9488 | 91.2500 | 69.2109 | 62.7306 | 97.3333 | `817a13b2e780` |
| 6 | ZMID | Common stock | 80.3976 | 87.5638 | 84.9132 | 46.2187 | 99.3333 | `a5e76cc02f2a` |
| 12 | VALU | Common stock | 79.5668 | 90.4088 | 82.8283 | 47.7976 | 87.5782 | `c1f60c2c2ec6` |
| 18 | MPRC | Common stock | 78.8507 | 90.8392 | 83.0069 | 47.8597 | 81.2759 | `b06d4ddc4b92` |
| 22 | GBCO | Common stock | 78.3625 | 86.8825 | 79.1904 | 46.3265 | 96.9776 | `44b12e959ae8` |
| 32 | COSG | Common stock | 77.2167 | 86.1323 | 77.5209 | 46.1743 | 94.3248 | `408aa159b780` |
| 49 | CERA | Common stock | 74.6455 | 81.0663 | 81.6375 | 47.5763 | 81.9624 | `a412aac26674` |
| 70 | RTVC | Common stock | 72.6003 | 83.0131 | 77.9886 | 51.1828 | 64.4088 | `a07823649699` |
| 78 | UNIP | Common stock | 70.8209 | 79.1672 | 64.4524 | 57.0253 | 77.5723 | `539048544d08` |
| 81 | KRDI | Common stock | 70.5766 | 71.2719 | 62.1166 | 69.8462 | 83.7965 | `bc1d45862afd` |
| 82 | MENA | Common stock | 70.4892 | 87.6107 | 63.4350 | 51.3086 | 62.1631 | `873704afec12` |
| 86 | SIPC | Common stock | 70.3024 | 73.3882 | 79.0680 | 48.0963 | 77.0725 | `7066858636c7` |
| 89 | DSCW | Common stock | 70.0703 | 69.0631 | 74.8876 | 46.8586 | 95.6764 | `5c13c7ce32f7` |
| 115 | IRON | Common stock | 67.0084 | 71.4865 | 77.3582 | 46.7267 | 64.8596 | `43505f14d63b` |
| 128 | SPMD | Common stock | 65.4170 | 70.0621 | 57.0013 | 52.8472 | 83.8158 | `5cebad490431` |
| 132 | SDTI | Common stock | 65.1472 | 67.8608 | 77.2149 | 45.7903 | 63.6072 | `282842bca2f4` |

Each fingerprint is stored in full as `sha256:<64 hex characters>` in the
result. Common provenance for every row is
`EODHD_DAILY/1d/SPLIT_ADJUSTED`, cutoff `2026-07-27`, raw/adjusted mode
`SPLIT_ADJUSTED_PRICE_EVENT_SPECIFIC_VOLUME`, metric version
`DAILY_HISTORICAL_SELECTION_V1`, and Rubix overlap status recorded separately.

| Symbol | Median | P25 | P75 | Normal band | 2% hit | Median upper | Median lower | Outlier rate | Mean/median divergence | Median volume | Median traded value EGP |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| ARAB | 4.7619 | 3.0000 | 5.0000 | [3.0000, 5.0000] | .7500 | 4.2572 | 0.0000 | .316667 | .9718 | 335,367,583 | 70,427,192.43 |
| ZMID | 3.2058 | 2.5411 | 3.8023 | [2.5411, 3.8023] | .9000 | 2.1370 | -.6806 | .000000 | .1022 | 27,149,495 | 167,270,819.06 |
| VALU | 3.3237 | 2.4872 | 4.3327 | [2.4872, 4.3327] | .9167 | 1.5510 | -1.6789 | .000000 | .1761 | 2,224,186 | 26,045,631.74 |
| MPRC | 3.3321 | 2.6667 | 4.0887 | [2.6667, 4.0887] | .9167 | 1.5803 | -1.2920 | .033333 | .2489 | 534,283 | 20,077,388.63 |
| GBCO | 3.2116 | 2.4158 | 4.3456 | [2.4158, 4.3456] | .9000 | 1.3003 | -1.7182 | .033333 | .3578 | 2,984,576.5 | 87,777,813.38 |
| COSG | 3.1749 | 2.5897 | 4.4095 | [2.5897, 4.4095] | .8667 | 2.3881 | -.6712 | .050000 | .4412 | 23,187,840 | 37,796,179.20 |
| CERA | 2.9852 | 2.4743 | 3.5440 | [2.4743, 3.5440] | .8667 | 1.6878 | -.8772 | .050000 | .3126 | 10,731,677 | 13,055,199.20 |
| RTVC | 3.0651 | 2.3246 | 4.4369 | [2.3246, 4.4369] | .8833 | 1.8205 | -1.2469 | .000000 | .4020 | 1,094,613.5 | 4,212,810.05 |
| UNIP | 3.0777 | 0.0000 | 3.8690 | [0.0000, 3.8690] | .7167 | 0.0000 | 0.0000 | .000000 | .4824 | 33,757,759 | 10,989,919.09 |
| KRDI | 2.8571 | 0.0000 | 2.9412 | [0.0000, 2.9412] | .7333 | 2.6014 | 0.0000 | .416667 | .2376 | 45,010,658.5 | 15,653,147.30 |
| MENA | 3.2522 | 2.5596 | 5.5388 | [2.5596, 5.5388] | .8667 | 2.0251 | -1.0230 | .133333 | 1.1526 | 831,304 | 4,959,720.19 |
| SIPC | 2.7616 | 2.2874 | 3.4229 | [2.2874, 3.4229] | .7833 | 1.7046 | -.8370 | .033333 | .2907 | 2,845,810 | 10,160,344.70 |
| DSCW | 2.6181 | 1.9361 | 3.3804 | [1.9361, 3.3804] | .7500 | 1.1495 | -1.0531 | .050000 | .2735 | 22,526,262 | 42,324,981.14 |
| IRON | 2.7428 | 1.9709 | 3.2711 | [1.9709, 3.2711] | .7333 | 1.2796 | -1.1590 | .016667 | .2967 | 214,202 | 7,281,427.55 |
| SPMD | 2.5641 | 2.3810 | 4.8780 | [2.3810, 4.8780] | .9667 | 2.3256 | 0.0000 | .416667 | 1.2331 | 38,974,878 | 16,578,439.64 |
| SDTI | 2.5648 | 2.1583 | 3.3735 | [2.1583, 3.3735] | .8000 | 1.5358 | -.8160 | .033333 | .2871 | 220,989.5 | 10,131,812.78 |

Warnings are disclosure-only: daily bars can include closing-auction effects for
all symbols; ARAB, KRDI and SPMD have high robust outlier rates; UNIP, KRDI and
SPMD have a zero-sided median excursion. No warning field is a live input.

## Score definition and five reproducibility traces

The final score is:

```text
0.40 × Movement + 0.25 × Stability + 0.20 × Zone + 0.15 × Liquidity
```

Normalization rules:

- Movement median clip:
  `clip((median range - 0.75) / (3.50 - 0.75), 0, 1)`.
  Hit aggregate weights the 1%, 1.5%, 2%, 2.5%, 3% frequencies
  `0.10/0.20/0.30/0.20/0.20`. Component =
  `100 × (0.65 median clip + 0.35 hit aggregate)`.
- Stability consistency weights clipped MAD/median, IQR/median, robust-band
  frequency, outlier rate and mean/median divergence
  `0.25/0.20/0.20/0.20/0.15`. Component =
  `100 × (0.60 consistency + 0.40 useful width)`.
- Zone component uses upper consistency, lower consistency, the geometric mean
  of robust-envelope frequencies, and the clipped worse-side outlier score
  with weights `0.30/0.30/0.25/0.15`.
- Liquidity log-clips median turnover between EGP 0.5m and 50m and median
  volume between 30k and 3m, then weights turnover clip, volume clip, volume
  consistency and non-zero frequency `0.55/0.20/0.20/0.05`.

The following values came from the cache-only implementation path. `Normalized`
lists the clipped or aggregate unit values before multiplication by 100.

| Case | Symbol/component | Raw inputs | Normalized | Component | Weight | Contribution |
|---|---|---|---|---:|---:|---:|
| A top stable | ZMID Movement | median 3.2058; hit vector 1/.9667/.9/.7667/.6333 | median .8930; hit .8433 | 87.5638 | .40 | 35.0255 |
|  | ZMID Stability | MAD/med .2039; IQR/med .3934; robust .7333; outlier 0; divergence .0319; useful .9667 | consistency .7597; useful width .9833 | 84.9132 | .25 | 21.2283 |
|  | ZMID Zone | upper/lower consistency .3467/0; envelopes .8/.8667; outlier clip 1 | combined envelope .8327 | 46.2187 | .20 | 9.2437 |
|  | ZMID Liquidity | turnover 167.271m; volume 27.149m; consistency .9667; nonzero 1 | clips 1/1/.9667/1 | 99.3333 | .15 | 14.9000 |
| B near cutoff | SDTI Movement | median 2.5648; hit vector 1/.95/.8/.5333/.3833 | median .6599; hit .7133 | 67.8608 | .40 | 27.1443 |
|  | SDTI Stability | .2296/.4738/.8333/.0333/.1119/.95 | consistency .7014; useful .8783 | 77.2149 | .25 | 19.3037 |
|  | SDTI Zone | consistency .4867/.0156; envelopes .7/.8667; outlier clip .75 | envelope .7789 | 45.7903 | .20 | 9.1581 |
|  | SDTI Liquidity | turnover 10.132m; volume 220,989.5; consistency .7; nonzero 1 | .6534/.4336/.7/1 | 63.6072 | .15 | 9.5411 |
| C unstable rejection | WCDF Movement | median 2.1588; hit vector .9333/.7667/.55/.4/.3667 | .5123/.5650 | 53.0745 | .40 | 21.2298 |
|  | WCDF Stability | .4242/1.1787/.75/.0833/.5868/.7667 | .3372/.6964 | 48.0882 | .25 | 12.0221 |
|  | WCDF Zone | .0545/0; .8/.8167; outlier clip .5833 | envelope .8083 | 30.5915 | .20 | 6.1183 |
|  | WCDF Liquidity | turnover 367,249; volume 679; consistency .8333; nonzero 1 | 0/0/.8333/1 | 21.6667 | .15 | 3.2500 |
| D insufficient movement | CIEB Movement | median 1.5485; hit vector .8667/.5167/.2333/.15/.05 | .2904/.3000 | 29.3736 | .40 | 11.7494 |
|  | CIEB Stability | .2618/.5133/.8/.0167/.0820/.5167 | .6934/.4358 | 59.0346 | .25 | 14.7587 |
|  | CIEB Zone | .2746/0; .85/.7833; outlier clip .75 | envelope .8160 | 39.8864 | .20 | 7.9773 |
|  | CIEB Liquidity | turnover 8.156m; volume 342,009.5; consistency .8833; nonzero 1 | .6063/.5285/.8833/1 | 66.5800 | .15 | 9.9870 |
| E liquid rejection | CCAP Movement | median 3.4487; hit vector 1/.9833/.9333/.7667/.6 | .9813/.8500 | 93.5368 | .40 | 37.4147 |
|  | CCAP Stability | .2681/.5483/.7833/.05/.1633/.9833 | .6303/.9917 | 77.4856 | .25 | 19.3714 |
|  | CCAP Zone | .3218/0; .8167/.8333; outlier clip .75 | envelope .8250 | 41.5266 | .20 | 8.3053 |
|  | CCAP Liquidity | turnover 709.101m; volume 136.461m; consistency .9333; nonzero 1 | 1/1/.9333/1 | 98.6667 | .15 | 14.8000 |

Reconstructed totals are ZMID 80.3976, SDTI 65.1472, WCDF 42.6202, CIEB
44.4724 and CCAP 79.8914. Sub-last-decimal differences from displayed
contributions are caused only by calculating from unrounded component values.
Eligibility outcomes are:

- ZMID passes all gates.
- SDTI passes all gates and is only 0.1472 above the score threshold.
- WCDF fails score, stability, zone, liquidity and turnover.
- CIEB fails score, 2% frequency and zone.
- CCAP fails only zone consistency despite exceptional liquidity.

No AI or opaque model score is present.

## Live-data invariance

The 48-test selector suite independently mutates current quote, price change,
high, low, volume, RVOL, spread, VWAP state, breakout state, momentum state and
Rubix freshness. The historical result dictionary, source fingerprint, score,
rank, candidate membership and snapshot ID remain value-for-value identical.

A separate metamorphic test changes a non-candidate's current move by +5%;
candidate membership remains unchanged. The snapshot serializer contains only
historical provider, versions, cutoff, source fingerprints, historical scores,
ranks and eligibility. No live field is accepted by the selector config or
analysis function.

## No-lookahead proof

The loader filters raw daily rows and applicable corporate actions to the
explicit cutoff before adjustment. The analyzer then rejects rows after D-1,
incomplete flags, duplicate local session dates, invalid OHLCV and
future-normalized volume metadata.

Tests prove:

- changing D OHLCV cannot alter the list prepared for D;
- D first becomes eligible when preparing D+1;
- a weekend gap retains the last completed D-1 history;
- timezone-aware timestamps are converted to Africa/Cairo before the local
  session-date cutoff;
- an incomplete D row is rejected;
- a stale cache receives `HISTORICAL_DAILY_DATA_STALE`;
- an explicit historical cutoff also excludes later split events.

The D and D+1 fingerprints differ only when D is legitimately admitted for the
next preparation cycle.

## Threshold sensitivity

Base gates are: 30 valid sessions, score 65, median range 1.5%, 2% hit
frequency .30, stability 50, zone 45, liquidity 40 and median turnover EGP 1m.
The base set has 16 candidates, median score 71.7106 and median range 3.0714%.
`Population` is the current, numeric-score population after the tested session
minimum.

| Gate | Tested values | Population | Candidate counts | Median score/range at non-base edge | Turnover vs base | Membership changes |
|---|---|---:|---|---|---:|---|
| Median range | 1, 1.25, 1.5, 1.75, 2 | 211 | 16/16/16/16/16 | 71.7106 / 3.0714 | 0 | none |
| 2% hit frequency | .20, .25, .30, .35, .40 | 211 | 16/16/16/16/16 | 71.7106 / 3.0714 | 0 | none |
| Stability | 40, 45, 50, 55, 60 | 211 | 16/16/16/16/15 | 72.6003 / 3.0777 at 60 | .0625 | SPMD leaves at 60 |
| Zone | 40, 42.5, 45, 47.5, 50 | 211 | 39/26/16/10/6 | 76.0743/3.2116 at 40; 70.6987/3.0714 at 50 | .5897/.3846/0/.375/.625 | detailed below |
| Liquidity | 30, 35, 40, 45, 50 | 211 | 16/16/16/16/16 | 71.7106 / 3.0714 | 0 | none |
| Valid sessions | 25, 30, 35, 45, 60 | 211/211/210/210/207 | 16/16/16/16/16 | 71.7106 / 3.0714 | 0 | none |

Zone membership changes:

- At 40, 23 enter: ACAMD, ACGC, AMIA, ATQA, CCAP, CICH, CIRA, COPR,
  CRST, EGCH, EGTS, EMFD, ENGC, HELI, JUFO, MPCI, MPCO, OCDI, POUL, RAYA,
  RREI, TANM, VLMRA.
- At 42.5, ten enter: ACAMD, AMIA, ATQA, CICH, EMFD, HELI, MPCI, MPCO,
  POUL, TANM.
- At 47.5, COSG, DSCW, GBCO, IRON, SDTI and ZMID leave.
- At 50, CERA, COSG, DSCW, GBCO, IRON, MPRC, SDTI, SIPC, VALU and ZMID
  leave.

Verdict: **highly sensitive**, localized to the zone gate. The baseline 45
threshold may proceed unchanged as a disclosed research checkpoint, but final
threshold approval remains a risk requiring out-of-sample evidence.

## Temporal stability and walk-forward dry run

This is a selector snapshot study, not a trade-entry backtest. Every snapshot
uses only completed EODHD daily observations before its evaluated session.

| Window/schedule | Snapshots | Median candidates | Min–max | Median turnover | Mean/max turnover | Median/min rank Spearman |
|---|---:|---:|---:|---:|---:|---:|
| 30-session daily, 2026-07-06..27 | 15 | 29 | 20–33 | .3729 | .3386 / .4706 | .9918 / .9838 |
| 30-session weekly, 2026-04-23..07-20 | 12 | 29 | 23–33 | .5500 | .5559 / .6512 | .9293 / .8750 |
| 60-session daily, 2026-07-06..27 | 15 | 16 | 12–19 | .2639 | .2636 / .4706 | .9961 / .9937 |
| 60-session weekly, 2026-04-23..07-20 | 12 | 14.5 | 13–18 | .5000 | .4912 / .6364 | .9723 / .9534 |

For the 60-session daily sequence, SIPC and UNIP appear in 15/15 snapshots;
AIDC, ARAB and CICH in 14/15; RTVC in 13/15; CERA and IRON in 12/15; SPMD in
11/15. For weekly snapshots, AIDC and UNIP appear in 12/12, CICH in 11/12,
CERA and COPR in 10/12, and SPMD in 9/12.

The 60-session isolated-entry review flags ARAB on July 7 and SDTI on July 26
in the daily series, and KRDI on July 20 in the weekly series. These are
disclosed for future research; they do not prove an event caused selection.
The 30-session series has more event-adjacent entries and higher turnover.

Sector concentration cannot be honestly calculated: `data/sectors.csv` is not
present and the cached exchange catalogue used in this audit does not contain
sector metadata. All sector values would be `Unknown`; inventing a mapping
would be worse than reporting the limitation. This is a remaining validation
risk.

## Thirty-versus-sixty sessions

Across the 211 common scoreable histories:

| Measure | Result |
|---|---:|
| Pearson score correlation | .9203 |
| Spearman rank correlation | .8792 |
| 30-session candidates | 29 |
| 60-session candidates | 16 |
| Overlap | 11 |
| Jaccard overlap | .3235 |
| Median absolute stability-score difference | 3.616 |

Unique to 30 sessions: ACAMD, CCAP, CCRS, CIRA, COPR, ECAP, EGCH, EGTS, EMFD,
ENGC, GGCC, GGRN, KABO, MOED, MPCO, OCPH, ODIN and PHDC.

Unique to 60 sessions: DSCW, IRON, RTVC, SIPC and SPMD.

Recommendation: **60 primary / 30 recent confirmation**. The 30-session window
is useful as a recency diagnostic, but its larger set, lower rank stability and
higher turnover make it unsuitable as the primary selector. Do not blend until
a separately approved out-of-sample study defines and validates the blend.

## Outlier and corporate-action defence

Synthetic and real-data regressions prove:

- one extreme candle is bounded by clipped robust components and cannot
  dominate candidate membership;
- split adjustment removes false price volatility;
- adjusted-close/dividend fields cannot enter the OHLC range calculation;
- split events after the historical cutoff cannot normalize earlier snapshots;
- invalid OHLC, non-positive price/volume and incomplete rows are rejected;
- all duplicate local session dates are rejected rather than resolved
  last-write-wins;
- timezone-aware rows use Cairo local dates;
- open gaps at or above 10% are counted and disclosed;
- arithmetic mean and mean/median divergence are diagnostic only; median, MAD,
  IQR and robust frequencies drive ranking.

Daily OHLC can still contain closing-auction effects and cannot reveal
high-versus-low ordering. No first-touch, target/stop or continuous-session
claim is made.

## Nine volume-policy blocks

All nine receive `VOLUME_HISTORY_UNRESOLVED`, no liquidity score and no
composite score. Price metrics remain usable, but traded value cannot be safely
derived from unresolved volume. No volume is imputed.

| Symbol | Event date | Observed raw pattern | Price metrics | Safe traded value | Policy |
|---|---|---|---|---|---|
| AMES | 2026-04-28 | event 221,840; pre/post medians 506,002 / 815,401; ratio 1.6115 | usable | no | exclude pending event-specific convention |
| ASPI | 2026-06-01 | event 131,924,457; pre/post 28,090,822 / 400,288,037; ratio 14.2498 | usable | no | exclude; structural discontinuity |
| DEIN | 2026-07-08 | event 0; 55 zero-volume rows in last 60; pre median 3; no post median; only 24 bounded usable sessions | usable but insufficient | no | exclude; insufficient plus unresolved |
| EGBE | 2026-06-02 | event 176,211; pre/post 383,378 / 176,211; ratio .4596 | usable | no | exclude pending event rule |
| HDBK | 2026-07-08 | event 443,788; pre/post 269,414 / 307,234; ratio 1.1404 | usable | no | exclude pending event rule |
| KZPC | 2026-06-25 | event 737,328; pre/post 404,418 / 327,839; ratio .8106 | usable | no | exclude pending event rule |
| MHOT | 2026-07-08 | event 704,989; pre/post 336,504 / 655,532; ratio 1.9481 | usable | no | exclude pending event rule |
| NINH | 2026-05-14 | event 556,708; pre/post 987,688 / 556,708; ratio .5636 | usable | no | exclude pending event rule |
| SVCE | 2026-04-23 | event 10,757,603; pre/post 2,390,244 / 10,757,603; ratio 4.5006 | usable | no | exclude; structural discontinuity |

The honest current policy is exclusion until provider/event evidence supports a
versioned volume convention. A zero liquidity score would falsely state
measured illiquidity; the typed unresolved state correctly states missing
measurement authority.

## Provider and provenance proof

The selector accepts only frames whose provider metadata is EODHD/EODHD Daily.
It requires a source fingerprint, cutoff, interval, price mode and metric
version in each result. Negative tests prove:

- provider `yahoo` is rejected;
- missing provider metadata is rejected;
- missing fingerprint metadata is either deterministically derived from an
  otherwise valid EODHD frame or the result remains unavailable;
- a current-session row and an incomplete candle are excluded;
- a frame normalized through a later-than-requested cutoff cannot produce a
  liquidity/composite score;
- unavailable data returns
  `HISTORICAL_DAILY_DATA_UNAVAILABLE`/`VOLUME_HISTORY_UNAVAILABLE`, `None`
  score and no rank;
- no missing symbol receives numeric zero;
- Rubix live state is not a score parameter or serialized snapshot field;
- configuration rejects relabeling the source to Yahoo.

Yahoo remains available only in pre-existing frozen legacy-backtest tests; it
is not an operational fallback and did not supply any selector input.

## Recommended frozen checkpoint

Retain, without automatic tuning:

| Gate | Phase 2A checkpoint |
|---|---:|
| Primary lookback | 60 completed sessions |
| Recent confirmation | 30 completed sessions |
| Minimum valid sessions | 30 |
| Historical score | 65 |
| Median full-session range | 1.5% |
| 2% range-hit frequency | .30 |
| Range Stability | 50 |
| Zone Consistency | 45 |
| Liquidity | 40 |
| Median turnover | EGP 1m |
| Maximum staleness | 10 calendar days |

Remaining risks are zone-threshold sensitivity, unavailable sector
classification, closing-auction contamination in daily highs/lows, unresolved
volume conventions for nine symbols, stale direct-endpoint recoveries, and the
absence of approved first-touch/continuous-session intraday evidence.

## Phase 2B resolution

The Zone threshold sensitivity identified here is resolved by the robust,
continuous-ranking design in
`SCALPING_ZONE_CONSISTENCY_HARDENING.md`. That document supersedes only the
Phase 2A Zone formula, Zone gate, provisional list and related sensitivity
finding; all provenance, cutoff, volume and provider evidence here remains
controlling.

## Stop condition

Phase 2A stops here. It does not implement or approve frozen-watchlist
persistence, live entry readiness, dashboard redesign, automatic signals,
paper/real trades, portfolio behavior, provider calculations outside this
selector, broker integration, production or automatic execution. No merge or
push is part of this phase.
