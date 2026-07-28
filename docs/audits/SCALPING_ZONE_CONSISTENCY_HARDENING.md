# Scalping Zone Consistency Hardening

**Phase:** 2B

**Branch:** `fix/scalping-historical-volatility-selection`

**Evidence cutoff:** completed EODHD Daily sessions through 2026-07-27

**Decision:** accept the hybrid Zone Consistency formulation as `ROBUST` for
historical ranking; retain persistence, dashboard and live-entry work for a
later phase.

## Scope and stop condition

This phase changes only the daily historical selector's Zone Consistency
calculation, hard eligibility/ranking separation, 60/30 confirmation,
disclosures and tests. It does not persist a watchlist, consume Rubix or any
current-session field, redesign a dashboard, create entry rules, enable
production, enable broker execution or place an order.

The evidence population is the accepted Phase 2A cache-backed population:

| Measure | Count |
|---|---:|
| Strict validated histories loaded | 225 |
| Current-ready | 219 |
| Preferred 60-session depth | 215 |
| Numeric scoreable population | 211 |
| Unresolved volume histories | 9 |
| Hard-eligible population after Phase 2B | 179 |
| Configured displayed candidates | 20 |

`eligible` now means that hard data, opportunity and safety requirements pass.
`selected_candidate` means that an eligible symbol is in the independently
configured Top 20. The safety gates were not tuned to manufacture 20 names.

## Root cause of the Phase 2A sensitivity

### Previous formula (`DAILY_HISTORICAL_SELECTION_V1`)

For each completed session, the previous code calculated upper excursion
`u = 100 × (High - Open) / Open` and lower magnitude
`d = 100 × max(Open - Low, 0) / Open`.

For each side `x`:

1. `m = median(x)`, `MAD = median(|x - m|)`,
   `IQR = P75(x) - P25(x)`.
2. Side scale was `max(|m|, 0.25)`.
3. MAD quality was
   `clip01(1 - (MAD / scale) / 0.75)`.
4. IQR quality was
   `clip01(1 - (IQR / scale) / 1.50)`.
5. Side dispersion consistency was
   `0.55 × MAD quality + 0.45 × IQR quality`.
6. The normal zone was
   `[m - max(2 × MAD, 0.25), m + max(2 × MAD, 0.25)]`;
   envelope coverage was the fraction inside it.
7. An outlier used modified z-score
   `0.67448975 × |x - m| / MAD > 3.5`. If `MAD == 0`, the code switched
   to the Tukey `P25 ± 1.5 × IQR` rule; if both MAD and IQR were zero,
   no observation was an outlier.
8. Combined score was:

```text
100 × (
    0.30 × upper dispersion
  + 0.30 × lower dispersion
  + 0.25 × sqrt(upper coverage × lower coverage)
  + 0.15 × clip01(1 - max(side outlier rate) / 0.20)
)
```

The value then faced a separate hard eligibility threshold of 45, in addition
to hard thresholds on composite score, Range Stability and Liquidity.

### Why it failed

The primary problem was the hard ordinary-quality gate applied to a
quantile-based score with substantial clipping. It was not a universe-relative
normalization or explicit bucket discontinuity.

- Many EGX lower-excursion medians are zero or close to zero. Normalizing a
  side by its own median, with a fixed 0.25 floor, made small changes in a
  quantized side disproportionately large.
- MAD and IQR component clipping put many side dispersion values at zero.
  Coverage and the global outlier term could then rescue a score into the
  40–50 area without making the underlying side stable.
- The `MAD == 0` switch to IQR outliers could reclassify observations abruptly
  when a new candle moved a quartile.
- The arithmetic upper/lower contributions let a stronger side offset a weak
  side too easily.
- Most importantly, the 45 gate converted an ordinary one-point score change
  into a full membership change.

The accepted Phase 2A threshold study is controlling evidence:

| Old Zone threshold | 40 | 42.5 | 45 | 47.5 | 50 |
|---|---:|---:|---:|---:|---:|
| Eligible candidates | 39 | 26 | 16 | 10 | 6 |

That is a cliff: a 2.5-point change around 45 removed 37.5% of the base set.
The issue was correctly classified `HIGHLY_SENSITIVE`.

## Formulations compared

Every comparison uses the same 211 completed-session EODHD Daily histories,
the same cutoff, no current candle, no Rubix value, no AI value and no Yahoo
input.

### A — robust dispersion

This uses range-scaled MAD and IQR quality only. It avoids the old own-median
normalization, but many EGX series are quantized enough that the clipped
dispersion score remains low.

### B — expected-zone coverage

This combines robust-zone coverage and outlier quality. It is stable but
saturated: its 60-session median is 81.5439 and 123 of 211 values are at least
80. It does not discriminate ordinary profiles sufficiently.

### C — hybrid, selected

This combines range-scaled dispersion, robust-zone coverage, range-scaled
outlier rate and mean/median divergence. It retains useful dispersion while
remaining resistant to one or two event candles.

| 60-session combined result | A dispersion | B coverage | C hybrid |
|---|---:|---:|---:|
| P10 | 0.0000 | 68.0712 | 44.0225 |
| P25 | 6.0932 | 75.1722 | 50.0624 |
| P50 | 13.3065 | 81.5439 | 55.7275 |
| P75 | 22.9463 | 85.9365 | 58.9382 |
| P90 | 30.3190 | 89.6191 | 62.8987 |
| Base hard eligible | 50 | 179 | 179 |
| Eligible range across tested controls | 0–79 | 179–179 | 141–179 |
| Worst Top-20 Jaccard | 0.0000 | 0.8182 | 0.8182 |
| Minimum full-rank correlation | 0.995008 | 0.995976 | 0.995525 |
| Verdict | `HIGHLY_SENSITIVE` | `ROBUST_BUT_SATURATED` | **`ROBUST`** |

The C eligible range falls to 141 only in the deliberately rejected
counterfactual that restores an ordinary combined-score gate of 50. Even
there, the Top 20 is unchanged. That gate is not part of the selected design.

## Selected formula (`DAILY_HISTORICAL_SELECTION_V2`)

### Raw session values

For each completed session `t`:

```text
upper_t = 100 × (High_t - Open_t) / Open_t
lower_t = 100 × max(Open_t - Low_t, 0) / Open_t
range_t = upper_t + lower_t
range_scale = max(median(range_t), 0.50)
```

Upper and lower are calculated independently. The mean is never the primary
stability estimator; it is used only for the disclosed divergence diagnostic.

For either side `x`:

```text
m     = median(x)
P25   = percentile(x, 25)
P75   = percentile(x, 75)
IQR   = P75 - P25
MAD   = median(|x - m|)

mad_quality = clip01(1 - (MAD / range_scale) / 0.35)
iqr_quality = clip01(1 - (IQR / range_scale) / 0.80)
dispersion  = 0.55 × mad_quality + 0.45 × iqr_quality

zone_half_width = max(2.50 × MAD, 0.15 × range_scale)
normal_zone     = [
    max(0, m - zone_half_width),
    m + zone_half_width
]
coverage = count(x inside normal_zone) / count(x)

outlier_half_width = max(
    (3.50 / 0.67448975) × MAD,
    0.50 × range_scale
)
outlier_rate = count(x outside m ± outlier_half_width) / count(x)
outlier_quality = clip01(1 - outlier_rate / 0.25)

divergence_ratio = |mean(x) - m| / range_scale
divergence_quality = clip01(1 - divergence_ratio / 0.75)

side_score = 100 × (
    0.40 × dispersion
  + 0.30 × coverage
  + 0.20 × outlier_quality
  + 0.10 × divergence_quality
)
```

Every displayed side profile discloses the raw estimator inputs, P25/P75, IQR,
MAD, normal-zone bounds, coverage, outlier rate, divergence and normalized
component scores.

The range-scaled outlier minimum is important. A quantized side with zero MAD
and zero IQR no longer jumps between “no outliers” and an IQR branch merely
because one new observation moves a quartile.

### Upper/lower combination

Four deterministic policies were measured:

| Policy | P10 | P25 | P50 | P75 | P90 |
|---|---:|---:|---:|---:|---:|
| Weighted harmonic mean | 48.5910 | 54.6776 | 58.1206 | 61.1492 | 64.5111 |
| Conservative minimum + balance | 44.0225 | 50.0624 | 55.7275 | 58.9382 | 62.8987 |
| Geometric mean | 49.7177 | 55.1021 | 58.3324 | 61.2573 | 64.6126 |
| Arithmetic with asymmetry penalty | 48.1263 | 53.7282 | 57.3835 | 60.2812 | 63.6691 |

The selected rule is:

```text
combined = 0.65 × min(upper_score, lower_score)
         + 0.35 × sqrt(upper_score × lower_score)
```

It is monotonic in both sides, puts most weight on the weaker side, and still
recognizes balanced strength. A stable side cannot hide a highly erratic side.
The default extra asymmetry penalty is zero because the selected combination
already penalizes imbalance; a separate multiplicative penalty could violate
monotonicity. Nonzero penalty values remain explicit sensitivity controls.

### Confidence labels

| Combined score | Label |
|---|---|
| `>= 70` | `VERY_STABLE_ZONE` |
| `>= 60` | `STABLE_ZONE` |
| `>= 45` | `MODERATE_ZONE` |
| `>= 30` | `UNSTABLE_ZONE` |
| `< 30` | `SEVERELY_ERRATIC_ZONE` |

These are historical confidence labels, not entry signals and not ordinary
eligibility gates.

## Hard safety floor and final eligibility

Zone safety rejects only severe behavior:

- combined score below 20;
- either side below 10;
- either side's outlier rate above 0.35;
- event domination: maximum side mean/median divergence ratio above 1.50 and
  either minimum side coverage below 0.85 or abnormal open-gap rate above
  0.20.

The last condition is typed `ZONE_EVENT_DOMINATED`. High-gap session count,
rate and maximum gap remain separately disclosed. Corporate actions are first
handled by the existing EODHD split-adjustment and volume policy; a surviving
event-like daily profile is then typed by this safety rule rather than silently
treated as normal.

Final hard eligibility requires:

- validated EODHD Daily provenance and valid completed OHLC;
- preferred 60-session depth;
- safe volume history, with all nine `VOLUME_HISTORY_UNRESOLVED` histories
  still excluded;
- median daily range at least 1.50%;
- at least 30% of sessions with a 2% range;
- median turnover at least EGP 1,000,000;
- no severe Zone safety reason.

Daily Movement Potential, Daily Range Stability, Daily Zone Consistency and
Daily Liquidity remain continuous ranking components with weights
`0.40 / 0.25 / 0.20 / 0.15`. The previous ordinary hard gates on composite
score, Range Stability, Zone Consistency and Liquidity Score are removed.

The hard-eligible population is 179. The candidate display is a separate,
configurable Top 20 ordered by confirmed historical score.

## 60-session primary / 30-session confirmation

The 60-session profile is always the primary score. The 30-session profile is
stored and displayed separately; the two are not averaged.

```text
deterioration = zone_60 - zone_30 - 5
recent_penalty = min(3, max(0, deterioration) × 0.10)
confirmed_historical_score = primary_historical_score - recent_penalty
```

Recent improvement receives no score bonus and therefore cannot erase an
unstable 60-session history. A deterioration of more than five zone points
applies a small, transparent penalty capped at three composite points.

Statuses use an eight-point transition:

- long-term unstable/severely erratic:
  `LONG_TERM_UNSTABLE`;
- recent score at least eight below primary:
  `LONG_TERM_STABLE_RECENT_WEAKENING`;
- long-term moderate and recent at least eight higher:
  `LONG_TERM_MODERATE_RECENT_IMPROVING`;
- stable/very stable without material deterioration:
  `LONG_TERM_AND_RECENT_STABLE`;
- otherwise:
  `LONG_TERM_MODERATE_RECENT_STEADY`;
- fewer than the preferred 60 sessions:
  `INSUFFICIENT_PREFERRED_DEPTH`.

## Final score distributions

All rows below use the 211 scoreable histories and the selected hybrid.

| Window / side | P10 | P25 | P50 | P75 | P90 | ±1 of old 45 | ±3 | ±5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 60 upper | 41.4917 | 48.4596 | 54.3217 | 59.1803 | 63.8348 | 9 | 24 | 49 |
| 60 lower | 54.6799 | 59.8073 | 63.8044 | 67.4246 | 71.0485 | 5 | 9 | 10 |
| 60 combined | 44.0225 | 50.0623 | 55.7275 | 58.9382 | 62.8987 | 10 | 28 | 46 |
| 30 upper | 41.3235 | 48.6493 | 55.1077 | 61.0282 | 65.2142 | 12 | 27 | 41 |
| 30 lower | 54.2680 | 59.5185 | 64.4248 | 69.7694 | 72.8452 | 0 | 5 | 8 |
| 30 combined | 44.1505 | 49.6489 | 55.5865 | 60.2834 | 64.7354 | 8 | 24 | 41 |

The cluster around 45 still exists. That is evidence that 45 is unsuitable as
an ordinary hard gate, not a reason to move the cliff elsewhere.

| Bin | 60 upper | 60 lower | 60 combined | 30 upper | 30 lower | 30 combined |
|---|---:|---:|---:|---:|---:|---:|
| 0–20 | 0 | 0 | 0 | 0 | 0 | 0 |
| 20–30 | 2 | 0 | 0 | 4 | 0 | 0 |
| 30–40 | 12 | 1 | 7 | 17 | 1 | 13 |
| 40–50 | 49 | 10 | 46 | 41 | 8 | 41 |
| 50–60 | 104 | 44 | 118 | 89 | 50 | 98 |
| 60–70 | 36 | 130 | 35 | 53 | 101 | 54 |
| 70–80 | 6 | 22 | 3 | 4 | 45 | 2 |
| 80–90 | 1 | 2 | 2 | 2 | 3 | 2 |
| 90–100 | 1 | 2 | 0 | 1 | 3 | 1 |

## Sensitivity after hardening

Base candidate medians are: confirmed composite 82.7406, daily range 3.5152%,
Range Stability 79.0543 and Zone Consistency 58.6285.

| Control | Values | Hard eligible range | Minimum Top-20 overlap | Worst Jaccard | Minimum rank correlation | Boundary changes |
|---|---|---:|---:|---:|---:|---|
| Combined severe floor | 15 / 20 / 25 | 179–179 | 20 | 1.0000 | 1.000000 | none |
| One-side severe floor | 5 / 10 / 15 | 179–179 | 20 | 1.0000 | 1.000000 | none |
| Zone weight | .15 / .20 / .25 | 179–179 | 18 | .8182 | .995525 | at .15, MPCI/PRDC replace GBCO/POUL |
| Asymmetry control | 0 / .05 / .10 | 179–179 | 20 | 1.0000 | .999914 | none |
| Outlier weight | .15 / .20 / .25 | 179–179 | 19 | .9048 | .999728 | at .15, MPCI replaces POUL |
| Severe outlier rate | .30 / .35 / .40 | 177–182 | 20 | 1.0000 | 1.000000 | none |

This is `ROBUST`, not perfectly invariant. Weight changes can change names at
the actual ranked-list boundary, which is expected. The base boundary is
especially close: POUL is eligible rank 20 at 81.6355 and MPCI is rank 21 at
81.6310, a 0.0045-point gap. The membership change under an outlier-weight
variation is therefore an honest Top-N boundary effect, not a safety cliff.

## Temporal stability

The temporal study holds all non-zone components and hard eligibility fixed,
then recomputes the selected zone formula from completed OHLC prefixes only.
It isolates turnover attributable to Zone Consistency.

| Schedule | Snapshots | Candidate count min–max | Median / max turnover | Median / min rank correlation | Median / P90 / max absolute zone change | Label-transition rate |
|---|---:|---:|---:|---:|---:|---:|
| Daily, 2026-07-06..27 | 15 | 20–20 | 0 / .181818 | .999428 / .999042 | .5286 / 1.99196 / 16.8915 | .055901 |
| Weekly, 2026-04-29..07-27 | 12 | 20–20 | .181818 / .260870 | .997692 / .997141 | 1.4273 / 4.27724 / 18.7404 | .135302 |

Daily Zone-only membership turnover totals 14 symbol changes across 14
transitions. Nine transitions have no candidate change. The nonzero changes
are all one-for-one boundary moves except 2026-07-09 to 2026-07-12, which has
two entries and two exits:

- GBCO for NIPH;
- NIPH and RREI for ELKA and GBCO;
- GBCO for PRDC;
- PRDC for MPCI;
- MPCI for PRDC;
- PHDC for MPCI.

Median movement is gradual. The maximum changes come from quantized,
event-dominated profiles; those profiles are now hard-safety typed and cannot
enter the candidate list. A dedicated regression adds one ordinary completed
session and requires a Zone change below two points.

## Outlier and event robustness

Synthetic regressions prove:

- a stable upper and stable lower side both score above 90;
- a stable side cannot hide an erratic opposite side;
- one 40% upper or lower excursion changes the combined score by less than
  five points;
- two event sessions among 60 do not reduce the combined score below 85;
- a quantized event-dominated history receives `ZONE_EVENT_DOMINATED` and is
  hard excluded;
- the conservative combination is monotonic;
- an ordinary additional completed session changes Zone Consistency by less
  than two points.

Three cache-backed histories were reviewed as anonymized real examples:

| Alias | Character | Upper | Lower | Combined | Label | Safety |
|---|---|---:|---:|---:|---|---|
| Real A | balanced, stable | 62.5635 | 71.1008 | 64.0098 | `STABLE_ZONE` | ready |
| Real B | quantized, asymmetric | 36.2134 | 73.4926 | 41.5948 | `UNSTABLE_ZONE` | severe outlier rate |
| Real C | stable, just outside Top 20 | 57.5735 | 75.4064 | 60.4841 | `STABLE_ZONE` | ready |

For reproducibility, the raw 60-session excursion percentages for Real A and
Real B are disclosed below. The aliases deliberately do not identify the
symbols.

<details>
<summary>Real A raw upper/lower values</summary>

```text
upper:
4.5775, 3.2203, .9934, 0, 2.2337, .6791, 2.0797, 1.8771, .3407,
1.5517, 1.2007, .6861, 3.0981, 3.6789, .6462, 5.4575, 0, 3.4483,
.9836, 1.5385, 3.1034, .8475, 4.8110, 2.3026, 2.9605, 1.3223,
2.3295, 3.9344, 4.6326, 1.2461, 1.4634, 4.4628, 2.2436, 1.7828,
1.2966, 2.8892, 1.4286, 2.1944, 3.5604, 1.6768, 1.7054, .9539,
5.6452, 1.3761, 4.6154, 1.0294, 2.6549, 0, 3.7594, 4.5255,
4.2553, 1.3774, 3.3613, 3.9835, 1.2000, 2.6667, .1312, 1.8868,
2.2942, 3.0464

lower:
0, 0, 2.1523, 3.5058, .6873, 2.0374, 0, .3413, 1.3629, 0,
.1715, .3431, 0, 0, 1.6155, 0, 3.7915, .3284, 4.0984, 1.3675,
0, 1.5254, 0, 1.3158, .4934, .8264, .8319, .8197, 0, 4.2056,
1.9512, 0, 1.4423, .9724, .3241, 0, .9524, 1.5674, .1548,
1.6768, 2.9457, 1.5898, 0, 1.0703, 0, .8824, 1.1799, 3.2117,
0, 0, .1418, 1.7906, 0, 0, 1.3333, .5333, 3.0184, .6739, 0, 0
```

</details>

<details>
<summary>Real B raw upper/lower values</summary>

```text
upper:
5, 0, 0, 0, 0, 0, 5, 5, 5, 5, 5, 5, 0, 5, 5, 5, 0, 0, 5, 5,
5, 0, 0, 5, 5, 0, 4.7619, 4.7619, 0, 0, 5, 5, 5, 0, 4.7619,
0, 0, 4.7619, 4.7619, 4.7619, 0, 0, 5, 0, 0, 0, 4.7619,
4.5455, 4.3478, 0, 4.3478, 4.3478, 4.1667, 4, 4, 3.8462, 4, 0,
4.1667, 0

lower:
0, 0, 0, 0, 0, 4.7619, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
4.7619, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 4.7619, 0, 0, 0, 0,
0, 0, 4.5455, 0, 0, 0, 0, 4.7619, 0, 0, 0, 0, 0, 0, 4.3478,
4.3478, 0, 0, 0, 4, 0, 3.8462, 4, 4, 0, 4.1667
```

</details>

## Re-evaluation of the 16 provisional candidates

`New rank` is the confirmed all-scoreable rank. Membership means inclusion in
the separately capped Top 20. The recent column is the disclosed 30-session
combined Zone score.

| Symbol | Old Zone | New upper | New lower | New combined / label | Recent 30 | Old rank | New rank | Member | Reason |
|---|---:|---:|---:|---|---:|---:|---:|---|---|
| ARAB | 62.7306 | 36.2134 | 73.4926 | 41.5948 / `UNSTABLE_ZONE` | 50.4798 | 5 | 57 | no | `ZONE_OUTLIER_RATE_SEVERE` |
| ZMID | 46.2187 | 62.5635 | 71.1008 | 64.0098 / `STABLE_ZONE` | 64.0541 | 6 | 5 | **yes** | retained, hard eligible rank 5 |
| VALU | 47.7976 | 67.5640 | 61.7221 | 62.7213 / `STABLE_ZONE` | 65.5110 | 12 | 11 | **yes** | retained, hard eligible rank 11 |
| MPRC | 47.8597 | 57.5735 | 75.4064 | 60.4841 / `STABLE_ZONE` | 57.1720 | 18 | 26 | no | hard eligible rank 25, outside Top 20 |
| GBCO | 46.3265 | 62.3278 | 65.1452 | 62.8154 / `STABLE_ZONE` | 65.3959 | 22 | 20 | **yes** | retained, hard eligible rank 19 |
| COSG | 46.1743 | 58.2547 | 70.2587 | 60.2571 / `STABLE_ZONE` | 66.2498 | 32 | 31 | no | hard eligible rank 30, outside Top 20 |
| CERA | 47.5763 | 61.3428 | 71.2776 | 63.0162 / `STABLE_ZONE` | 57.8112 | 49 | 50 | no | hard eligible rank 48, outside Top 20 |
| RTVC | 51.1828 | 70.0553 | 64.8320 | 65.7284 / `STABLE_ZONE` | 61.4886 | 70 | 67 | no | hard eligible rank 63, outside Top 20 |
| UNIP | 57.0253 | 38.0000 | 47.7102 | 39.6027 / `UNSTABLE_ZONE` | 45.8977 | 78 | 136 | no | `ZONE_OUTLIER_RATE_SEVERE` |
| KRDI | 69.8462 | 57.0665 | 71.3148 | 59.4212 / `MODERATE_ZONE` | 54.5463 | 81 | 128 | no | hard eligible rank 120, outside Top 20 |
| MENA | 51.3086 | 53.9680 | 70.4178 | 56.6555 / `MODERATE_ZONE` | 64.7354 | 82 | 95 | no | hard eligible rank 87, recent improving |
| SIPC | 48.0963 | 65.3391 | 68.5201 | 65.8892 / `STABLE_ZONE` | 56.1004 | 86 | 80 | no | hard eligible rank 74; recent penalty .4789 |
| DSCW | 46.8586 | 65.0656 | 66.2506 | 65.2720 / `STABLE_ZONE` | 64.3877 | 89 | 74 | no | hard eligible rank 69, outside Top 20 |
| IRON | 46.7267 | 62.3996 | 66.9040 | 63.1741 / `STABLE_ZONE` | 61.4992 | 115 | 109 | no | hard eligible rank 101, outside Top 20 |
| SPMD | 52.8472 | 50.8286 | 44.5189 | 45.5865 / `MODERATE_ZONE` | 44.0313 | 128 | 155 | no | `ZONE_OUTLIER_RATE_SEVERE` |
| SDTI | 45.7903 | 63.1060 | 71.4356 | 64.5185 / `STABLE_ZONE` | 65.6858 | 132 | 124 | no | hard eligible rank 116, outside Top 20 |

Only ZMID, VALU and GBCO overlap the old set. The old-to-new overlap is 3,
with 13 old departures and 17 new entries; Jaccard similarity is .0909. This
large one-time turnover is expected because the old list was manufactured by
several ordinary hard cliffs and the new list is a Top-N composite ranking. It
is not evidence of ongoing temporal instability.

## Revised candidate list and boundary

Final ranked candidates:

1. RAYA
2. ACAMD
3. EGTS
4. CCRS
5. ZMID
6. OCDI
7. PRCL
8. AREH
9. EGCH
10. CCAP
11. VALU
12. CRST
13. RREI
14. MASR
15. NIPH
16. AMER
17. PHDC
18. EEII
19. GBCO
20. POUL

Strongest eligible rejections:

| Eligible rank | Symbol | Confirmed score | Zone 60 | Zone 30 | Status |
|---:|---|---:|---:|---:|---|
| 21 | MPCI | 81.6310 | 58.3638 | 51.1311 | moderate / recent steady |
| 22 | AIDC | 81.4350 | 56.1565 | 60.1407 | moderate / recent steady |
| 23 | GOUR | 81.4143 | 56.2422 | 54.8986 | moderate / recent steady |
| 24 | PRDC | 81.4049 | 53.6906 | 57.5057 | moderate / recent steady |
| 25 | MPRC | 81.3756 | 60.4841 | 57.1720 | stable |
| 26 | TANM | 80.7270 | 60.7101 | 59.4760 | stable |
| 27 | HELI | 80.6417 | 58.6943 | 61.1743 | moderate / recent steady |
| 28 | ICID | 80.5991 | 57.6032 | 57.7737 | moderate / recent steady |
| 29 | ELKA | 80.5491 | 51.2152 | 47.9549 | moderate / recent steady |
| 30 | COSG | 80.0333 | 60.2571 | 66.2498 | stable |

The frozen cache-only snapshot identifier is
`b65b14e4ab8302e699036986a9dcd66547779cd6b68b537c10501db467cb889e`.

## Invariance, no-lookahead and provider policy

Focused regression coverage proves:

- mutating the complete `rubix_live` payload cannot change the snapshot;
- every individual live field is independently invariant;
- the current day D is excluded from a watchlist prepared for D;
- future bars and future corporate actions are excluded at the loader cutoff;
- stale-cache and normalization cutoffs remain lookahead-free;
- an incomplete daily candle is rejected;
- Yahoo-tagged frames, missing provider metadata and attempts to configure the
  selector as Yahoo are rejected;
- missing EODHD data returns typed unavailable/readiness state, never a zero
  metric;
- production and broker order flags remain false.

All Phase 2B probes blanked EODHD credentials and read only the accepted local
cache. No current quote, Rubix session, AI score, Yahoo source or network
fallback entered the evidence.

## Seven-skip review

All seven cases were introduced in commit
`170ff2d5a742c5b9dfe340b22ca7800aefa3b1ba`, which is an ancestor of the
branch's first commit `c34e6c9`. They therefore predate this branch.

| Test | Exact skip reason | Selector-related? | Acceptable? |
|---|---|---|---|
| `test_real_symbol_volume_is_never_universally_multiplied[COMI]` | `COMI EODHD data not cached` | no; provider-volume correctness | yes, intentional real-cache prerequisite |
| `...[EAST]` | `EAST EODHD data not cached` | no | yes |
| `...[SWDY]` | `SWDY EODHD data not cached` | no | yes |
| `...[KZPC]` | `KZPC EODHD data not cached` | no | yes |
| `...[UNIP]` | `UNIP EODHD data not cached` | no | yes |
| `...[ORAS]` | `ORAS EODHD data not cached` | no | yes |
| `test_kzpc_regression_short_window_event_flagged` | `KZPC not cached` | no; event-volume regression | yes, intentional real-cache prerequisite |

The six parameterized cases verify that real corrected volume is never
universally multiplied. The KZPC case verifies a known short-window corporate
event is flagged and raw volume preserved. With credentials blanked and those
real cache documents absent in the isolated worktree, skipping is the explicit
test design. No selector test is skipped, so there is no acceptance blocker.

## Validation

| Validation | Result |
|---|---:|
| Focused historical selector | **62 passed in 6.06s** |
| Complete repository suite | **1450 passed, 7 skipped in 79.52s** |
| `git diff --check` before commits | passed |

The complete suite includes existing Expected Range, scalping, provider,
dashboard, launcher, paper, production and broker safety coverage.

## Remaining risks

- The calibration is a deterministic point-in-time study through 2026-07-27,
  not an out-of-sample profitability claim.
- POUL and MPCI are separated by 0.0045 composite points. Top-N boundary
  membership can legitimately change with a new completed session or a
  material weight decision.
- Daily bars cannot identify intraday path or first touch. No such claim was
  added.
- Corporate-action safety depends first on the upstream EODHD adjustment and
  volume metadata. The Zone layer types event-like residual behavior but
  cannot independently reconstruct an action.
- Confidence-label transitions are visible and may be more frequent than
  candidate changes; labels are diagnostic, not trade signals.
- Sector concentration remains undisclosed because no validated sector map is
  present.
- The final watchlist is still in-memory only. Persistence, dashboard and live
  entry readiness remain intentionally out of scope.

## Phase 2B conclusion

The selected hybrid is robust, monotonic, explainable, resistant to isolated
events and separate from Range Stability. Ordinary Zone quality contributes
continuously to rank; only severe behavior is a hard rejection. The 60-session
profile remains authoritative, while the 30-session profile supplies a
transparent deterioration penalty and confidence status without overwriting
long-term evidence.

Phase 2B stops here.
