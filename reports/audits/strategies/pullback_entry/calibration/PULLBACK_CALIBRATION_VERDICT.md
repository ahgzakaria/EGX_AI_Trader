# Pullback Calibration Verdict

- Operational status: `REJECTED_FOR_OPERATIONAL_USE`
- Reproducibility status: `RETAINED_AS_RESEARCH_BASELINE`
- Product role: diagnostic Pullback Health Analysis only; no production adoption.

## Scope and implementation correctness

- Calibration version: `ai_pullback_calibration@1.0.0`
- Symbols requested: 241
- Symbols with sufficient cached EODHD data: 235
- Symbols skipped: 6
- Date range: 2002-01-15 to 2026-06-30
- Symbol-days evaluated: 561142
- Completed cached EODHD daily bars only; no network refresh and no Yahoo.
- Skipped symbols and reasons: `{"AMII": "INSUFFICIENT_COMPLETED_HISTORY", "KORA": "INSUFFICIENT_COMPLETED_HISTORY", "NULL": "INSUFFICIENT_COMPLETED_HISTORY", "TWSA": "INSUFFICIENT_COMPLETED_HISTORY", "TYCN": "INSUFFICIENT_COMPLETED_HISTORY", "UTOP": "INSUFFICIENT_COMPLETED_HISTORY"}`
- Breakout/breakdown and EMA5/EMA10 scalping logic were not changed.
- This remains Research Only and cannot change production BUY decisions.

## Evaluator correctness

The previous evaluator did ignore structural stop/target execution: it measured fixed-horizon close returns and MAE/MFE even after a trade should have exited. Those values are now labelled unmanaged diagnostics only. Managed trades enter at the next session open, apply 30 bps total transaction cost, activate the stop and targets on that entry bar, and stop accruing returns and excursions at exit. If stop and target occur in one daily candle, stop-first is used. Exit-candle MAE/MFE is also clipped at the executed boundary so post-exit intraday prices are not counted. Drawdown is calculated in chronological entry order from starting equity, not symbol iteration order.

Target 1 is conservatively treated as a full-position exit in this evaluator; Target 2 is therefore a diagnostic touch before that exit, not an assumed runner. A scaled-exit policy would require an explicit research configuration and is not silently invented here.

### Managed versus unmanaged results

| Result | Count | Win rate | Average/expectancy % | Median % | Worst MAE % | Best MFE % |
|---|---:|---:|---:|---:|---:|---:|
| Unmanaged 3_bars | 210 | 0.428571 | -0.04034 | -0.382226 | -35.190232 | 44.892955 |
| Unmanaged 5_bars | 210 | 0.438095 | 0.019665 | -1.126151 | -41.670374 | 44.892955 |
| Unmanaged 10_bars | 210 | 0.52381 | 2.328734 | 0.785223 | -48.053279 | 74.329919 |
| Unmanaged 20_bars | 210 | 0.547619 | 2.881292 | 1.522668 | -49.385246 | 107.01107 |
| Managed to actual exit | 207 | 0.391304 | -0.719171 | -3.354545 | -15.416667 | 18.59525 |

Managed stop-hit rate is 0.584541; Target-1 hit rate is 0.376812; Target-2 diagnostic touch rate is 0.024155; profit factor is 0.806053; chronological maximum drawdown is -92.0369%; median R is -1.033854.

## Exact state and gate funnel

- NOT_APPLICABLE: 525549
- DEVELOPING_PULLBACK: 3233
- WAIT_REVERSAL_CONFIRMATION: 13814
- HEALTHY_PULLBACK: 1532
- DEEP_PULLBACK: 3664
- CONFIRMED_PULLBACK_ENTRY: 210
- FAILED_PULLBACK: 13140

| Ordered gate | Individual pass | Cumulative pass | Cumulative rate |
|---|---:|---:|---:|
| valid_completed_eodhd_observation | 557151 / 561142 | 557151 | 0.992888 |
| valid_prior_uptrend | 35593 / 507543 | 35593 | 0.06343 |
| confirmed_swing_high_found | 510628 / 557151 | 35593 | 0.06343 |
| valid_impulse_low_found | 507543 / 510628 | 35593 | 0.06343 |
| correction_detected | 510628 / 557151 | 35593 | 0.06343 |
| support_zone_identified | 507543 / 507543 | 35593 | 0.06343 |
| support_reached_or_approached | 447694 / 507543 | 30357 | 0.054099 |
| volume_acceptable | 294613 / 495594 | 20358 | 0.03628 |
| reversal_confirmation_detected | 198363 / 507543 | 7423 | 0.013228 |
| entry_trigger_crossed | 32709 / 507543 | 1518 | 0.002705 |
| acceptable_available_upside | 502441 / 507543 | 1518 | 0.002705 |
| acceptable_reward_risk | 26582 / 507543 | 227 | 0.000405 |
| confirmed_pullback_entry | 210 / 507543 | 210 | 0.000374 |

## Rejection diagnosis

### Contributing reasons

- REWARD_RISK_BELOW_THRESHOLD: 475859 contributing; 0 primary
- ENTRY_TRIGGER_NOT_CROSSED: 474834 contributing; 3100 primary
- PRIOR_TREND_STRUCTURE_NOT_DESCENDING_FAILED: 368676 contributing; 0 primary
- MINOR_PIVOT_BEFORE_MEANINGFUL_TARGET: 347465 contributing; 0 primary
- NO_STRONG_REVERSAL_CONFIRMATION: 309180 contributing; 8801 primary
- PRIOR_TREND_IMPULSE_DURATION_VALID_FAILED: 304597 contributing; 0 primary
- PRIOR_TREND_EMA20_SLOPE_POSITIVE_FAILED: 249301 contributing; 0 primary
- PRIOR_TREND_EMA50_SLOPE_POSITIVE_FAILED: 242319 contributing; 0 primary
- EMA50_SLOPE_INVALIDATED: 239314 contributing; 0 primary
- PRIOR_TREND_EMA20_ABOVE_EMA50_AT_SWING_FAILED: 236097 contributing; 0 primary

### Primary reasons

- INVALID_PRIOR_UPTREND: 471950
- NO_CONFIRMED_SWING_HIGH: 46523
- NO_STRONG_REVERSAL_CONFIRMATION: 8801
- AGGRESSIVE_SELLING_VOLUME_EXPANSION: 6041
- ATR_UNAVAILABLE: 3991
- CORRECTION_EXCEEDS_RESEARCH_DEPTH_LIMIT: 3974
- DEEP_PULLBACK_REQUIRES_STRONGER_CONFIRMATION: 3615
- PULLBACK_DID_NOT_REACH_SUPPORT: 3233
- ENTRY_TRIGGER_NOT_CROSSED: 3100
- NO_VALID_IMPULSE_LOW: 3085

### Largest losses

The largest total cumulative loss is: `valid_prior_uptrend` rejects 521558 observations at that stage and leaves 35593.
After prior-trend qualification, reversal confirmation is the largest candidate-stage loss; the exact gate counts above distinguish it from the later trigger and R/R losses.

## Signal frequency and statistical sufficiency

- Confirmed entries: 210
- Confirmation frequency: 0.000374
- Managed trades: 207

The expanded default sample is large enough to show that the current configuration is rare and has negative managed expectancy. It is not sufficient to validate a replacement configuration: several variants have only a handful of trades, and symbol/year dependence remains.

## Reversal confirmation and deep EMA50 behavior

The five evidence flags are reported independently. Entry uses OR across the four strong confirmations (support-zone reclaim, EMA20 reclaim, close above previous high, correction-trendline break). A bullish rejection candle alone cannot activate entry. Structural validity, support reach, trigger crossing, available upside and R/R remain required.

A correctness issue was found and fixed: the trigger previously included EMA20 for every setup, which forced an EMA20 reclaim even for a deep pullback intentionally supported at EMA50. A deep EMA50 zone can now use support-zone/previous-high confirmation below EMA20. Shallow/healthy behavior is unchanged.

## Targets and available upside

- Minor target available pass rate: 0.983967
- Meaningful target available pass rate: 0.932158
- R/R pass against nearest minor target: 0.053206
- R/R pass against first meaningful target: 0.124338
- R/R pass against broader structural target: 0.763517

Minor, meaningful and broader resistance are now separate diagnostics. The entry gate still uses the conservative nearest target; targets are not inflated merely to pass R/R.

## Sensitivity results

Sensitivity rows completed: 19.

| Variant | Type | Trades | Win rate | Expectancy % | PF | Drawdown % | Median R | Positive years | Positive symbols |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| default_full_history | EXPANDED_BASELINE | 207 | 0.391304 | -0.719171 | 0.806053 | -92.0369 | -1.033854 | 8/21 | 48/109 |
| combo_pivot1_impulse1_5 | COMBINATION | 5 | 0.8 | 3.40443 | 3.61833 | -6.5011 | 1.126254 | 1/1 | 3/4 |
| combo_pivot1_rr1_25 | COMBINATION | 5 | 0.8 | 3.40443 | 3.61833 | -6.5011 | 1.126254 | 1/1 | 3/4 |
| combo_pivot3_rr1_75 | COMBINATION | 33 | 0.363636 | -1.334704 | 0.624041 | -43.7859 | -1.042221 | 0/2 | 11/30 |
| default_tail_250 | OFAT | 21 | 0.380952 | -0.921742 | 0.701289 | -26.9439 | -1.045342 | 1/2 | 6/18 |
| healthy_atr_2_5 | OFAT | 21 | 0.380952 | -0.921742 | 0.701289 | -26.9439 | -1.045342 | 1/2 | 6/18 |
| healthy_atr_3_5 | OFAT | 21 | 0.380952 | -0.921742 | 0.701289 | -26.9439 | -1.045342 | 1/2 | 6/18 |
| impulse_atr_1_5 | OFAT | 21 | 0.380952 | -0.921742 | 0.701289 | -26.9439 | -1.045342 | 1/2 | 6/18 |
| impulse_atr_2_5 | OFAT | 21 | 0.380952 | -0.921742 | 0.701289 | -26.9439 | -1.045342 | 1/2 | 6/18 |
| pivot_radius_1 | OFAT | 5 | 0.8 | 3.40443 | 3.61833 | -6.5011 | 1.126254 | 1/1 | 3/4 |
| pivot_radius_3 | OFAT | 36 | 0.388889 | -1.127975 | 0.67271 | -42.1218 | -1.040716 | 0/2 | 11/31 |
| retracement_max_50 | OFAT | 21 | 0.380952 | -0.921742 | 0.701289 | -26.9439 | -1.045342 | 1/2 | 6/18 |
| retracement_max_70 | OFAT | 21 | 0.380952 | -0.921742 | 0.701289 | -26.9439 | -1.045342 | 1/2 | 6/18 |
| rr_1_25 | OFAT | 25 | 0.4 | -0.82724 | 0.726896 | -30.3417 | -1.045342 | 0/2 | 7/21 |
| rr_1_75 | OFAT | 18 | 0.388889 | -0.897984 | 0.707863 | -24.2159 | -1.048687 | 1/2 | 6/16 |
| support_tolerance_0_75 | OFAT | 9 | 0.333333 | -1.685583 | 0.613241 | -22.1752 | -0.803389 | 0/1 | 2/8 |
| support_tolerance_1_0 | OFAT | 0 | N/A | N/A | N/A | N/A | N/A | 0/0 | 0/0 |
| volume_ratio_1_0 | OFAT | 21 | 0.380952 | -0.921742 | 0.701289 | -26.9439 | -1.045342 | 1/2 | 6/18 |
| volume_ratio_1_15 | OFAT | 21 | 0.380952 | -0.921742 | 0.701289 | -26.9439 | -1.045342 | 1/2 | 6/18 |

## Configuration recommendation

Keep the current defaults frozen as the regression baseline, but reject them as an operationally useful research configuration: managed expectancy and profit factor are negative/under one on the expanded sample. Do not promote or loosen them. Pivot radius 1 looks positive only on five recent trades and is statistically insufficient; pivot radius 3 increases frequency while degrading quality. The small combinations do not supply a stable replacement.

The managed evidence is 207 executable trades, 39.13% wins, -0.719% expectancy, 0.806 profit factor, 58.45% stop rate, and -1.034 median R. The positive-looking unmanaged 20-bar return ignored intervening structural stops and was therefore misleading as entry evidence. Sensitivity analysis did not identify a statistically credible replacement. The measurements may still help human analysis of correction quality, but no production adoption is recommended.

## Lookahead audit

Confirmed pivots carry both pivot date and knowable confirmation date; a radius-N pivot is unavailable until N right-hand completed bars exist. Scenario cutoff is never earlier than pivot confirmation. Support/Fibonacci/targets are computed from the frozen cutoff only, future appended pivots do not change them, and managed entry is one completed bar after the signal. No lookahead leak was found in the audited path.

## Remaining uncertainty

Daily OHLC cannot reveal intrabar ordering. The conservative stop-first rule bounds that ambiguity but cannot remove it. The drawdown is a chronological sequential-signal curve, not a capital-allocation portfolio simulation. Target-1 scaling is not modeled. Sparse positive variants must not be treated as calibration proof.
