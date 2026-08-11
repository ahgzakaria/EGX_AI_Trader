# ORB — Preliminary Signal Performance (34 signals)

**Research Only. Production execution disabled.** This is an analysis of
measurements. No trade was placed, no order exists, and nothing here is a
recommendation to trade.

Source: `orb_signal_outcomes.db` (34 rows, all `MEASUREMENT_COMPLETE`), plus
read-only re-reads of the Rubix price paths for the forward-horizon and
time-to-threshold figures. `rubix_live_market.db` was opened `mode=ro` with
`PRAGMA query_only=ON`; nothing was written to it or to the frozen runtime.

## 0. What was not available, and what that forced

The analysis asked for `ret_1m … ret_30m`. **Those columns do not exist in the
store.** The measurement layer persisted MFE, MAE and session-end only, so the
six horizon returns and every time-to-threshold statistic below had to be
derived by re-reading the price paths. They are reproducible from the same
evidence under the same boundary (14:15 Cairo), but they are *not* currently
persisted, and that is itself a finding — see §10.

`TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED` still holds. No stop or target was
reconstructed anywhere in this report.

**Definitions.** Entry is the first Rubix quote at or after detection. `ret_Nm`
is the last price at or before `entry + N minutes`, against entry; a horizon
past 14:15 would be unavailable, and none were. Ties are frequent at short
horizons because Rubix records repeated identical snapshot prices, so
`% positive + % negative < 100`.

---

## 1. Overall statistics (n = 34)

| | mean | median | min | max | sd |
|---|---|---|---|---|---|
| MFE % | +2.862 | **+0.828** | 0.000 | +18.820 | 4.649 |
| MAE % | −1.631 | **−1.464** | −5.413 | 0.000 | 1.140 |
| session-end % | +0.869 | **−0.449** | −5.280 | +18.820 | 4.558 |

The mean and the median disagree in sign on both the favourable excursion
relationship and the session-end return. That disagreement is the single most
important structural fact in this dataset, and §9 quantifies it.

### Forward-horizon returns

| horizon | n | mean | median | positive | zero | negative | % positive | % negative |
|---|---|---|---|---|---|---|---|---|
| ret_1m | 34 | −0.036 | +0.000 | 4 | 19 | 11 | 11.8 % | 32.4 % |
| ret_3m | 34 | −0.179 | −0.063 | 3 | 11 | 20 | 8.8 % | 58.8 % |
| ret_5m | 34 | −0.279 | −0.108 | 5 | 8 | 21 | 14.7 % | 61.8 % |
| ret_10m | 34 | −0.400 | −0.166 | 5 | 3 | 26 | 14.7 % | 76.5 % |
| ret_15m | 34 | −0.407 | −0.265 | 5 | 7 | 22 | 14.7 % | 64.7 % |
| ret_30m | 34 | −0.366 | −0.322 | 7 | 5 | 22 | 20.6 % | 64.7 % |
| ret_session_end | 34 | +0.869 | −0.449 | 14 | 1 | 19 | 41.2 % | 55.9 % |

**Every measured horizon from 1 to 30 minutes is negative on both mean and
median, and the positive rate never exceeds 20.6 %.** A signal that is a
short-horizon entry edge should not look like this. Whatever positive outcome
exists in this sample arrives only over hours, and only for a minority.

Other measured inputs: entry lag 0.0–36.5 s (median 4.4 s); observations per
window 1,161–16,633 (median 3,308); measurement quality 34/34
`MEASUREMENT_COMPLETE`.

---

## 2. Threshold analysis

Basis: maximum excursion reached at any point between entry and 14:15.

| threshold | reached up | % | | threshold | reached down | % |
|---|---|---|---|---|---|---|
| ≥ +0.25 % | 20 | 58.8 % | | ≤ −0.25 % | **32** | **94.1 %** |
| ≥ +0.50 % | 19 | 55.9 % | | ≤ −0.50 % | **29** | **85.3 %** |
| ≥ +0.75 % | 18 | 52.9 % | | ≤ −0.75 % | 26 | 76.5 % |
| ≥ +1.00 % | 16 | 47.1 % | | ≤ −1.00 % | 23 | 67.6 % |
| ≥ +1.50 % | 13 | 38.2 % | | ≤ −1.50 % | 16 | 47.1 % |
| ≥ +2.00 % | 10 | 29.4 % | | ≤ −2.00 % | 10 | 29.4 % |
| ≥ +3.00 % | 7 | 20.6 % | | ≤ −3.00 % | 3 | 8.8 % |

The adverse side dominates below 1.5 % and the favourable side takes over
above 2 %. That shape — near-certain small adverse movement, occasional large
favourable movement — is the signature of a low-hit-rate, high-payoff profile.
It is not evidence of one; it is consistent with one, and also consistent with
noise plus five lucky days.

---

## 3. Time to move

Seconds from entry to the first observation reaching the threshold. "never"
means the threshold was not reached before 14:15.

| threshold | reached | never | median | fastest | slowest |
|---|---|---|---|---|---|
| +0.5 % | 19/34 | 15 | 1,756 s (29 min) | 6 s | 10,907 s |
| +1.0 % | 16/34 | 18 | 3,373 s (56 min) | 18 s | 10,575 s |
| +1.5 % | 13/34 | 21 | 3,040 s (51 min) | 794 s | 10,575 s |
| −0.5 % | 29/34 | 5 | **636 s (11 min)** | 10 s | 5,463 s |
| −1.0 % | 23/34 | 11 | 2,333 s (39 min) | 10 s | 10,363 s |

**Adverse movement arrives roughly three times sooner than favourable
movement** (median 11 min to −0.5 % against 29 min to +0.5 %), and reaches more
signals (29 against 19). Median time to the adverse extreme is 66.8 min against
38.5 min to the favourable extreme, and in 15 of 34 the adverse extreme
occurred first.

Which side was touched first, among signals that touched at least one:

| | both touched | up first | down first | only up | only down |
|---|---|---|---|---|---|
| ±0.5 % | 14 | 6 | 8 | 5 | 15 |
| ±1.0 % | 7 | 5 | 2 | 9 | 16 |

---

## 4. By session

| session | n | mean MFE | median MFE | mean MAE | median MAE | mean close | median close | % close > 0 |
|---|---|---|---|---|---|---|---|---|
| 2026-08-06 | 12 | 2.327 | 0.709 | −1.668 | −1.546 | −0.010 | −0.713 | 33.3 % |
| 2026-08-10 | 10 | 2.352 | 0.114 | −1.927 | −1.655 | +0.931 | −1.038 | 20.0 % |
| 2026-08-11 | 12 | 3.823 | 1.463 | −1.347 | −1.001 | +1.695 | +0.556 | 66.7 % |

08-11 is the only session with a positive median on both MFE and session-end
return, and the only one where a majority closed positive. **One session out of
three is not a pattern.** With 10–12 signals per session, one favourable market
day is entirely capable of producing this spread on its own.

---

## 5. By detection time bucket (Cairo)

| bucket | n | mean MFE | median MFE | mean MAE | mean close | % close > 0 |
|---|---|---|---|---|---|---|
| 10:00–10:30 | 0 | — | — | — | — | — |
| 10:30–11:00 | 17 | 2.129 | 0.357 | −1.830 | +0.024 | 35.3 % |
| 11:00–12:00 | 12 | 3.701 | 1.103 | −1.512 | +1.852 | 50.0 % |
| 12:00–13:00 | 5 | 3.344 | 1.270 | −1.235 | +1.378 | 40.0 % |
| 13:00–14:00 | 0 | — | — | — | — | — |
| 14:00–14:15 | 0 | — | — | — | — | — |

Three of the six buckets are empty by construction, not by chance: the opening
range does not close until 10:15 so nothing can fire before it, and
`latest_research_entry_time` / `expiry_time` close the window in the afternoon
(the 800 `LATE_SESSION` and 359 `ENTRY_EXPIRED` rows on 08-11 are that cutoff
working). The whole distribution therefore lives in 10:30–13:00, and the
apparent "later is better" gradient rests on 5 observations in the last
non-empty bucket. **This breakdown is not usable for tuning.**

---

## 6. Rankings

### By MFE %

| # | signal | MFE % | MAE % | close % | | # | signal | MFE % | MAE % | close % |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 08-10 OCPH | 18.82 | −0.82 | 18.82 | | 18 | 08-11 CCAP | 0.77 | −0.38 | 0.19 |
| 2 | 08-11 ALUM | 14.36 | −0.04 | 10.63 | | 19 | 08-06 SVCE | 0.53 | −2.65 | −1.90 |
| 3 | 08-11 OCDI | 12.01 | −0.84 | 8.90 | | 20 | 08-06 ACTF | 0.36 | −1.43 | −1.07 |
| 4 | 08-11 EGAL | 10.46 | −0.32 | 6.86 | | 21 | 08-06 CLHO | 0.22 | −2.91 | −1.45 |
| 5 | 08-06 EFIC | 8.91 | −2.46 | 0.68 | | 22 | 08-10 EFIC | 0.21 | −3.50 | −1.26 |
| 6 | 08-06 CRST | 7.87 | −0.93 | 7.87 | | 23 | 08-11 EGCH | 0.14 | −1.61 | −1.33 |
| 7 | 08-06 EGAS | 4.64 | −0.59 | −0.35 | | 24 | 08-10 ORAS | 0.14 | −0.68 | −0.35 |
| 8 | 08-06 DSCW | 2.91 | 0.00 | 1.46 | | 25 | 08-10 ETEL | 0.09 | −3.48 | −2.38 |
| 9 | 08-10 EALR | 2.62 | −1.57 | 0.79 | | 26 | 08-10 TMGH | 0.02 | −1.24 | −1.09 |
| 10 | 08-11 ELKA | 2.33 | −1.16 | 0.58 | | 27 | 08-06 ARAB | 0.00 | −2.04 | −1.22 |
| 11 | 08-10 EGTS | 1.63 | −1.74 | −0.54 | | 28 | 08-06 EASB | 0.00 | −2.86 | −2.72 |
| 12 | 08-11 MFPC | 1.61 | −0.40 | 1.32 | | 29 | 08-06 VALU | 0.00 | −1.59 | −1.51 |
| 13 | 08-06 MEPA | 1.60 | −1.06 | 0.00 | | 30 | 08-10 IRON | 0.00 | −2.88 | −2.51 |
| 14 | 08-11 AMOC | 1.49 | −0.53 | 0.53 | | 31 | 08-10 VALU | 0.00 | −1.94 | −1.18 |
| 15 | 08-11 LCSW | 1.44 | −1.99 | 0.72 | | 32 | 08-10 HRHO | 0.00 | −1.42 | −0.98 |
| 16 | 08-11 KABO | 1.27 | −1.27 | −0.58 | | 33 | 08-11 MIPH | 0.00 | −5.41 | −5.28 |
| 17 | 08-06 EGTS | 0.89 | −1.50 | 0.11 | | 34 | 08-11 PHGC | 0.00 | −2.20 | −2.20 |

### By MAE % (worst first)

08-11 MIPH −5.41 · 08-10 EFIC −3.50 · 08-10 ETEL −3.48 · 08-06 CLHO −2.91 ·
08-10 IRON −2.88 · 08-06 EASB −2.86 · 08-06 SVCE −2.65 · 08-06 EFIC −2.46 ·
08-11 PHGC −2.20 · 08-06 ARAB −2.04 · 08-11 LCSW −1.99 · 08-10 VALU −1.94 ·
08-10 EGTS −1.74 · 08-11 EGCH −1.61 · 08-06 VALU −1.59 · 08-10 EALR −1.57 ·
08-06 EGTS −1.50 · 08-06 ACTF −1.43 · 08-10 HRHO −1.42 · 08-11 KABO −1.27 ·
08-10 TMGH −1.24 · 08-11 ELKA −1.16 · 08-06 MEPA −1.06 · 08-06 CRST −0.93 ·
08-11 OCDI −0.84 · 08-10 OCPH −0.82 · 08-10 ORAS −0.68 · 08-06 EGAS −0.59 ·
08-11 AMOC −0.53 · 08-11 MFPC −0.40 · 08-11 CCAP −0.38 · 08-11 EGAL −0.32 ·
08-11 ALUM −0.04 · 08-06 DSCW 0.00

### By session-end return

Top: 08-10 OCPH +18.82 · 08-11 ALUM +10.63 · 08-11 OCDI +8.90 ·
08-06 CRST +7.87 · 08-11 EGAL +6.86 · 08-06 DSCW +1.46 · 08-11 MFPC +1.32.

Bottom: 08-11 MIPH −5.28 · 08-06 EASB −2.72 · 08-10 IRON −2.51 ·
08-10 ETEL −2.38 · 08-11 PHGC −2.20 · 08-06 SVCE −1.90 · 08-06 VALU −1.51.

---

## 7. Behaviour classes

**Strongest — favourable move that persisted to the close.** OCPH (+18.82 MFE,
+18.82 close, kept 100 %), CRST (+7.87 / +7.87, kept 100 %), ALUM (+14.36 /
+10.63, kept 74 %), OCDI (+12.01 / +8.90, kept 74 %), EGAL (+10.46 / +6.86,
kept 66 %). Four of these five had an MAE shallower than −0.85 %: they went
almost straight up.

**Weakest — never traded above entry at all (MFE exactly 0.00): 8 of 34.**
08-06 ARAB, EASB, VALU · 08-10 IRON, VALU, HRHO · 08-11 MIPH, PHGC. Their MAEs
run −1.42 % to −5.41 %. For these signals the reclaim was confirmed and the
price never printed above the entry quote again for the rest of the session.

**False-breakout-like — MFE positive but session-end negative: 11 of 34
(32 %).** Ranked by give-back: EGAS 5.00 pp · ETEL 2.47 pp · SVCE 2.43 pp ·
EGTS(08-10) 2.17 pp · KABO 1.85 pp · CLHO 1.68 pp · EGCH 1.47 pp ·
EFIC(08-10) 1.46 pp · ACTF 1.43 pp · TMGH 1.11 pp · ORAS 0.49 pp.

**Favourable excursion with poor persistence.** Among the 16 signals that
reached MFE ≥ 1 %, the fraction of the favourable move still held at 14:15:
EGAS kept −7.6 %, EGTS(08-10) −33.3 %, KABO −45.5 % (all three closed below
entry despite a ≥ 1 % favourable excursion), MEPA 0 %, EFIC(08-06) 7.7 %,
ELKA 25 %, EALR 30 %, AMOC 35.7 %, DSCW 50 %, LCSW 50 %, EGAL 65.6 %,
ALUM 74.0 %, OCDI 74.1 %, MFPC 81.7 %, CRST 100 %, OCPH 100 %.

**Immediate versus slow movers.** Of the 19 that reached +0.5 %, five did so
within 250 s (OCPH 6 s, EFIC 17 s, MEPA 72 s, ELKA 80 s, DSCW 249 s) and five
took over an hour (CRST 8,299 s, LCSW 8,994 s, CCAP 9,090 s, EGTS 10,907 s).
Speed did not separate outcomes: the fastest mover was the best signal in the
sample and the second-fastest gave back 92 % of its excursion.

**Two context splits, both weak.** Entry lag < 10 s (n = 27) closed +0.95 %
mean / 44.4 % positive, against ≥ 10 s (n = 7) at +0.54 % / 28.6 % — 7
observations decide nothing. Signals that never flickered out of
`ENTRY_READY_RESEARCH` (n = 17) closed +2.05 % mean against −0.32 % for those
that did (n = 17), but the positive *rate* is identical at 41.2 %, so the
difference is one large winner sitting on one side.

---

## 8. Excursion diagnostic: MFE / |MAE|

> **This is an excursion diagnostic. It is NOT an R-multiple and NOT a trading
> return.** It compares two extremes that were reached at different times, in
> either order, with no entry rule, no exit rule and no stop between them. A
> value of 3.0 does not mean 3R was available; it means the favourable extreme
> was three times the adverse extreme at some point during the session.

Computable for 33 of 34 (08-06 DSCW excluded: MAE is exactly 0.00).

| | value |
|---|---|
| mean | 14.566 |
| **median** | **0.593** |
| min | 0.000 |
| max | 373.000 (08-11 ALUM, MAE −0.04 %) |

| ≥ 0.5 | ≥ 1.0 | ≥ 1.5 | ≥ 2.0 | ≥ 3.0 |
|---|---|---|---|---|
| 17/33 | 14/33 | 13/33 | 11/33 | 8/33 |

The mean is meaningless here — it is dominated by ALUM, whose −0.04 % adverse
excursion puts a near-zero in the denominator. **The median of 0.593 is the
usable figure: the typical signal's favourable extreme was about six-tenths of
its adverse extreme.** 19 of 34 signals had MFE < |MAE|.

---

## 9. Sample-size limitations — read this before using anything above

**n = 34, across 3 sessions, from one exchange, in one month.** These are hard
constraints, not disclaimers:

1. **The positive mean is five observations.** Session-end returns sum to
   +29.53 pp across 34. The top five contribute **+53.08 pp — 180 % of the
   total**. Remove the single best and the mean falls from +0.869 to +0.325;
   remove the top three and it turns negative (−0.284); remove the top five and
   it is −0.812 with a median of −0.982. The 20 %-trimmed mean is **−0.391**.
   Every positive headline in this report is one to five trading days away from
   reversing.
2. **The sign test finds nothing.** 14 of 33 non-zero session-end returns were
   positive. One-sided P(≥ 14 | p = 0.5) = 0.852 — the observed rate is *below*
   half, and the test cannot reject chance in either direction.
3. **Sessions are not independent observations.** 10–12 signals share one
   market day, so the effective sample is closer to 3 than 34. 08-11 alone
   supplies three of the five large winners.
4. **No session had unusual market-wide conditions checked.** Nothing here
   controls for index direction, sector movement, or whether all 12 signals on
   a day were the same trade in different tickers.
5. **The exit rule is missing, so this cannot represent the strategy.** The
   session-end return holds every signal to 14:15. The strategy does not do
   that — it has a stop and a 2R target. §10 is the direct consequence.
6. **No multiple-comparison discipline was applied.** Six horizons, seven
   thresholds, three sessions and six time buckets were examined. At n = 34,
   something will look significant by construction; nothing here was corrected
   for that, and no cut in §4 or §5 should be treated as a finding.

---

## 10. What the engine already knew and threw away

Every one of the 34 signals passed gates that *required* a stop and targets to
exist and be valid. From [engine.py:1085-1113](scalping_orb/engine.py:1085), an
evaluation only reaches `ENTRY_READY_RESEARCH` after `_structural_risk` and
`_targets` both return without rejection reasons — otherwise it becomes
`RECLAIM_FAILED`. So for all 34, by construction and without reconstructing
anything:

* a structural stop existed, strictly below the trigger, at most **3.0 %**
  below it (`maximum_stop_distance_percent`, else `STOP_DISTANCE_EXCEEDED`);
* `target_1 = trigger + 1.0R`, `target_2 = trigger + 2.0R`;
* the effective reward/risk to the usable target was **≥ 1.5**
  (`minimum_reward_risk`, else `REWARD_RISK_BELOW_MINIMUM`).

Those values were computed, frozen into the state transition, and then dropped:
`ShadowStateRecord` at
[shadow_service.py:720-744](scalping_orb/shadow_service.py:720) copies the
state, fingerprints and timing out of the evaluation and copies neither `risk`
nor `targets`.

This matters for reading §1–§8. The measured MAE distribution (median −1.46 %,
23 of 34 reaching −1 %, 10 reaching −2 %) sits squarely inside the 0–3 % band
where the unknown stops must have been. **A large share of these signals
plausibly stopped out before their favourable excursion occurred — and this
dataset cannot say which**, because the entry proxy is a quote rather than the
trigger, and the per-signal stop is gone. The give-back figures in §7 are
similarly unreadable: a strategy with a 1R/2R target ladder would not have held
EGAS from +4.64 % to −0.35 %.

---

## Conclusion: **CONCERNING** — but the right response is persistence, not abandonment

Answering the practical question directly: *do these 34 signals show evidence
that the strategy is worth continuing to validate?*

**The measured evidence leans negative on every axis except the mean, and the
mean is the statistic this sample supports least.**

* Median session-end return is **−0.449 %**; only 41.2 % closed positive.
* All six forward horizons are negative on mean *and* median; the positive rate
  never exceeds 20.6 %.
* Adverse thresholds are reached more often (85.3 % vs 55.9 % at 0.5 %) and
  about three times sooner (median 11 min vs 29 min).
* Median MFE/|MAE| is **0.593**; 19 of 34 had a smaller favourable extreme than
  adverse extreme; 8 of 34 never printed above entry at all.
* The positive mean is 180 % attributable to five observations and turns
  negative on removing three.

That is `CONCERNING`, not `INCONCLUSIVE`: the data is not neutral, it leans
negative. It is not proof of no edge either — n = 34 across 3 correlated
sessions cannot establish that, and a low-hit-rate high-payoff profile
genuinely can look like this over 34 samples.

**But the verdict is on the measurement, and the measurement is not the
strategy.** Holding every signal to 14:15 is not what this strategy does. The
one thing that would make these numbers interpretable — the per-signal stop and
target that the engine already computed — was discarded at the persistence
boundary and costs nothing to keep.

So the recommendation is narrow and cheap: **add the persistence contract in
§11 before running more sessions, and re-evaluate on signals that carry their
own stop and targets.** Continuing to accumulate sessions *without* it would
add observations to a dataset that structurally cannot answer the question. And
should the strategy's rules be revised, this report is the honest baseline to
revise against — 34 signals whose central tendency was negative.

---

## 11. Proposed minimal persistence contract (NOT IMPLEMENTED)

Nothing below is implemented. This is a specification for review.

Everything requested already exists in `ORBResearchEvaluation`
([engine.py:313-337](scalping_orb/engine.py:313)) at the instant
`ENTRY_READY_RESEARCH` is emitted. **No field needs to be calculated and no
field is unavailable.** The gap is entirely that `ShadowStateRecord` does not
copy `risk` and `targets`.

### Already available at signal time — copy, do not compute

| Requested | Source at emission | Type |
|---|---|---|
| trigger price | `reclaim.trigger_price` / `targets.trigger_price` | REAL |
| structural stop | `risk.proposed_stop` | REAL |
| stop basis | `risk.stop_basis` (`BELOW_FIRST_PULLBACK_LOW`) | TEXT |
| stop reference | `risk.raw_pullback_low` | REAL |
| stop buffer + basis | `risk.buffer_applied`, `risk.buffer_basis` (`INTRADAY_ATR_BUFFER` / `PERCENT_BUFFER_ATR_UNAVAILABLE`) | REAL, TEXT |
| risk distance | `risk.stop_distance_absolute`, `_percent`, `_atr` | REAL ×3 |
| risk per share (1R) | `targets.risk_per_share` | REAL |
| target 1 | `targets.target_1` (+ `target_1_r_multiple`) | REAL, REAL |
| target 2 | `targets.target_2` (+ `target_2_r_multiple`) | REAL, REAL |
| usable target | `targets.usable_target` | REAL |
| effective reward/risk | `targets.effective_reward_risk` | REAL |
| RR gate outcome | `targets.meets_minimum_reward_risk`, `resistance_before_target_1` | INTEGER ×2 |
| resistance inputs | `targets.nearest_daily_resistance`, `reward_before_resistance` | REAL ×2 |
| ATR reading | `breakout.intraday_atr.value`, `.status`, `.interval_minutes`, `.lookback_bars`, `.observed_bars` | REAL, TEXT, INT ×3 |
| strategy version | `evaluation.strategy_fingerprint` | TEXT |
| engine version | `evaluation.engine_version` | TEXT |
| opening-range identity | `opening_range_version_identity`, `opening_range_revision` | TEXT, INT — *already persisted* |
| evidence fingerprint | `evidence_fingerprint`, `candidate_identity` | TEXT ×2 — *already persisted* |

Also worth carrying, already present and currently dropped:
`breakout.breakout_identity`, `breakout.opening_range_high`,
`breakout.distance_above_or_high_percent`, `pullback.low`,
`pullback.depth_from_high_percent`, `pullback.depth_atr`,
`reclaim.confirmation_bar_end_utc`. These are what make a later measurement
able to say *which* structure the stop referenced.

### Must be calculated — none

Every requested field is already materialised. `risk_per_share` is
`stop_distance_absolute`; the R multiples are config constants already carried
on `TargetProjection`.

### Not available at signal time, and never will be

| Field | Why |
|---|---|
| **entry fill price** | No order exists and none will. The trigger is a *price level the engine confirmed*, not a fill. Outcome measurement must keep using an explicit, labelled proxy (first quote at or after detection) and must never call it a fill. |
| slippage, fees, size, position value | No execution, no sizing, no broker. Out of scope by design. |
| realised exit price / exit reason | Requires a management rule this system deliberately does not run. |
| ATR value when `status != AVAILABLE` | Genuinely absent — the reading warms up. Persist the **status alongside the value**, never a null that reads as zero. |
| `nearest_daily_resistance` when D-1 context is missing | Legitimately `NULL`. Must be distinguishable from "no resistance above". |

### Shape

One row per emitted `ENTRY_READY_RESEARCH`, in a new table
(`orb_signal_qualification`) keyed
`(run_id, cycle_id, canonical_ticker, opening_range_version_identity)` to match
Lane A's existing uniqueness, written inside the same transaction as the
Lane A row so a signal and its levels cannot disagree about whether they
happened.

Additive only: a new table plus a `SCHEMA_VERSION` bump, no column removed and
no existing row rewritten. The frozen 34 stay exactly as they are — this
contract applies to *future* signals, and the outcome store keeps
`TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED` for the historical set permanently.

Three properties worth pinning in tests when this is built: a stored stop is
always strictly below its stored trigger; a stored `effective_reward_risk` is
always ≥ `minimum_reward_risk` for an emitted signal; and an ATR status other
than `AVAILABLE` never stores a numeric ATR.

---

*No code was changed to produce this report. The frozen ORB runtime
(`283f86d`), the Rubix collector, the Daily Scan and EODHD were not touched. No
merge, no push, no deployment.*
