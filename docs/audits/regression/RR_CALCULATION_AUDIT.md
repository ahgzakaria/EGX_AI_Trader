# RR Calculation Mathematical Audit

Date: 2026-07-19  
Scope: Swing/Daily candidates that actually reached the Risk gate and failed it  
Current candle archive: `RUN_20260719_191032`  
Phase 8 archive: `RUN_20260714_125023`  
Risk rejections audited: **113**

## Verdict

The RR calculation is **numerically correct for the strategy formula that is currently frozen**. Every component was recomputed from raw archived OHLCV without calling the project's Entry, Support/Resistance, ATR, or EMA implementations. All 113 Risk rejections matched the production engine and the archived Phase 8/RC1 implementation on identical candles.

There is no RR regression and no first divergent line of code:

- Support matches independently recomputed 20-bar minimum Low.
- Displayed resistance matches independently recomputed 20-bar maximum High including the current bar.
- Target resistance matches independently recomputed maximum High over the prior bars, excluding the current bar.
- ATR matches an independent Wilder ATR(14) implementation.
- Entry, Stop, Target1, Target2, risk distance, reward distance, and rounded RR all match.
- Current and Phase 8 code produce identical values on the same candles for all 113 symbols.
- Intermediate two-decimal rounding changes the Risk pass/fail result for **0 symbols**.
- Rubix Last is not used in any calculation.

The mathematical reason for the low RR is the geometry of strong, recently advanced stocks: the 20-session support is often far below the current Close, producing a large downside distance, while Target2 is anchored to previous resistance plus only `2 × ATR`. Median risk distance is **16.55% of Entry**, while median reward distance is **10.58% of Entry**, resulting in median RR **0.67**.

No strategy rule, threshold, indicator, or price level was changed during this audit.

## Audit outputs

- Complete 113-row calculation table:
  [`reports/audits/rr_audit.csv`](../../../reports/audits/rr_audit.csv)
- Machine-readable verification summary: `reports/rr_audit_summary.json`
- All 113 charts: `reports/rr_audit_charts/`
- Chart index: `reports/rr_audit_charts/INDEX.md`
- Reproducible audit program: `scripts/audit_rr_calculation.py`

The CSV contains, for every actual sequential Risk rejection:

- Entry, BuyLow, Stop, Target1, Target2
- displayed and raw Support
- displayed resistance including the current bar
- target resistance excluding the current bar
- engine and independent ATR
- Close and informational Rubix Last
- risk distance, reward distance, engine RR, independent RR, and raw unrounded RR
- independent EMA20/50/200 comparisons
- a separate verification boolean for every input
- Phase 8 values on identical candles
- Phase 8 sealed-dataset date and values
- the exact formula and chart path

## 1. Exact production mathematics

For the final completed daily bar at index `i`, the strategy uses:

```text
start = max(0, i - 19)

support_raw = min(Low[start : i + 1])
target_resistance_raw = max(High[start : i])       # current bar excluded
displayed_resistance = max(High[start : i + 1])    # current bar included

ATR = Wilder ATR(14)
Entry = round(Close[i], 2)
BuyLow = round(max(support_raw, Close[i] - 0.30 × ATR), 2)
Stop = round(support_raw - 0.30 × ATR, 2)
Target1 = round(target_resistance_raw, 2)
Target2 = round(target_resistance_raw + 2.00 × ATR, 2)

Risk = Entry - Stop
Reward = Target2 - Entry
RR = round(Reward / Risk, 2)
RiskPass = 1.5 <= RR <= 100.0
```

Production source locations:

- `strategy/entry.py:5-12`: 20-bar Support and prior-bar target Resistance.
- `strategy/entry.py:82-108`: BuyLow, Entry/BuyHigh, Stop, and Risk.
- `strategy/entry.py:134-154`: Target1, Target2, Reward, and RR.
- `strategy/support.py:3-8`: separately displayed Support and Resistance.
- `indicators/technical.py:100-113`: production ATR construction.
- `strategy/decision_engine.py:261`: exact Risk boolean.

## 2. Independent recalculation method

The audit did not reuse the production math for its expected values.

1. Raw OHLCV was loaded from the sealed scan archive.
2. True Range was independently computed as:

   ```text
   max(High-Low, abs(High-PreviousClose), abs(Low-PreviousClose))
   ```

3. ATR(14) was independently seeded with the first 14 True Ranges and updated using Wilder smoothing:

   ```text
   ATR[t] = ((ATR[t-1] × 13) + TR[t]) / 14
   ```

4. Support, both resistance definitions, Entry, Stop, Targets, Risk, Reward, and RR were reconstructed directly with independent code.
5. EMA20/50/200 were independently reconstructed with `adjust=False` exponential weighting and matching minimum periods.
6. Each independent value was compared with the engine and with the archived RC1/Phase 8 functions.

## 3. Verification results

| Verification | Passed | Failed |
|---|---:|---:|
| Support | 113 | 0 |
| Displayed resistance | 113 | 0 |
| Target resistance / Target1 | 113 | 0 |
| ATR(14) | 113 | 0 |
| Entry | 113 | 0 |
| Stop | 113 | 0 |
| Target1 | 113 | 0 |
| Target2 | 113 | 0 |
| RR | 113 | 0 |
| EMA20/50/200 | 113 | 0 |
| Phase 8 code on identical candles | 113 | 0 |

Summary statistics for the rejected set:

| Measure | Result |
|---|---:|
| Median RR | 0.67 |
| Minimum RR | -0.22 |
| Highest failed RR | 1.45 |
| Median Entry-to-Stop distance | 16.55% |
| Median Entry-to-Target2 distance | 10.58% |
| Median Support distance below Close | 15.47% |
| Current Close above prior resistance | 26 symbols |
| Target2 at or below Entry | 3 symbols |
| Negative reward | 2 symbols |
| Rounding changed gate outcome | 0 symbols |

## 4. Important resistance semantic

The engine contains two resistance concepts:

1. **Target resistance** in `entry_signal`: highest High of the previous portion of the 20-bar window, excluding the current candle. This is used for Target1 and Target2.
2. **Displayed/Quality resistance** in `support_resistance`: highest High including the current candle. This is returned as `Resistance` and used by QualityFilter.

They can differ sharply on a breakout candle. This is why a screen may show Resistance near the current High while Target1 is below Entry. The distinction is old and identical in RC1; it is not a provider/UI regression. Whether those two concepts should be redesigned is a strategy-research question and was deliberately not changed.

## 5. Visual and mathematical examples

### IBCT.CA — strong trend but asymmetric geometry

```text
Close / Entry = 14.70
Support raw = 11.880000
ATR = 0.687401
Stop = round(11.88 - 0.30 × 0.687401, 2) = 11.67
Prior resistance = 14.630000
Target2 = round(14.63 + 2 × 0.687401, 2) = 16.00
Risk = 14.70 - 11.67 = 3.03
Reward = 16.00 - 14.70 = 1.30
RR = round(1.30 / 3.03, 2) = 0.43
```

The chart shows the risk band is more than twice the reward band. Rubix Last was 14.50 and is plotted as an informational cross only.

![IBCT RR geometry](../../../reports/rr_audit_charts/IBCT_CA.png)

### DTPP.CA — large advance moved Entry far above 20-bar support

```text
Entry = 242.40
Support = 114.00
ATR = 13.812349
Stop = 109.86
Prior resistance = 234.50
Target2 = 262.12
Risk = 132.54
Reward = 19.72
RR = 0.15
```

The price advance is technically strong, but the frozen Stop remains anchored below the 20-bar low. A high trend score therefore does not imply attractive RR.

![DTPP RR geometry](../../../reports/rr_audit_charts/DTPP_CA.png)

### BIOC.CA — Target2 below Entry

```text
Entry = 105.70
Support = 66.75
ATR = 4.461017
Stop = 65.41
Prior resistance = 88.09
Target2 = 97.01
Risk = 40.29
Reward = -8.69
RR = -0.22
```

The current candle advanced beyond the prior resistance by more than `2 × ATR`, so the formula places Target2 below Entry. The negative RR is mathematically correct for the existing target formula. Rubix Last was 126.84, but was not used.

![BIOC RR geometry](../../../reports/rr_audit_charts/BIOC_CA.png)

### ETRS.CA — closest rejected candidate

```text
Entry = 10.90
Stop = 9.69
Target2 = 12.65
Risk = 1.21
Reward = 1.75
RR before intermediate rounding = 1.44624
Production RR = 1.45
```

ETRS is the highest failed RR. Even without intermediate price-level rounding, it remains below 1.5, proving that rounding did not create the rejection.

![ETRS RR geometry](../../../reports/rr_audit_charts/ETRS_CA.png)

### COPR.CA — low-priced rounding cross-check

```text
Entry = 0.38
Stop = 0.35
Target2 = 0.42
RR before intermediate rounding = 1.33783
Production RR = 1.33
```

The two-decimal price convention moves the numeric value slightly, but both raw and production RR fail 1.5. Across all 113 candidates, no gate outcome changed due to rounding.

![COPR RR geometry](../../../reports/rr_audit_charts/COPR_CA.png)

## 6. Phase 8 comparison

### Correct regression comparison: identical candles

The current and RC1/Phase 8 files are byte-identical:

| File | SHA-256 current and RC1 |
|---|---|
| `strategy/entry.py` | `4EA1BA556938A4700830001AF1276772B29009CF0F25E8EC7C4B6CA96A0F2671` |
| `strategy/support.py` | `EB11875D8AD624C9C6628770CE5D5D4FE3685ADF67FDE32FAC016B44F7FD65F4` |
| `indicators/technical.py` | `C79862F31F86669366F7E56F9F5611825BF1EB1AC66606A9C635E73B152BFF51` |

The RC1 modules were also loaded and executed independently on the current archived candles. All 113 symbols matched Entry, Stop, Target1, Target2, Support, Resistance, ATR, and RR exactly. Therefore there is no first changed line responsible for current RR.

### Sealed Phase 8 dataset comparison: different candles

The Phase 8 sealed dataset ends on 2026-07-12 for 112 audited symbols and 2026-06-24 for one, while every current audited calculation uses 2026-07-16. RR differs for 110 symbols when comparing those two dataset endpoints because the rolling 20-bar window, Close, Support, prior Resistance, and ATR legitimately changed as new candles arrived.

Examples:

| Ticker | Phase 8 data date / RR | Current date / RR | Explanation |
|---|---|---|---|
| IBCT.CA | 2026-07-12 / 1.23 | 2026-07-16 / 0.43 | Entry advanced; rolling Support remained much lower |
| DTPP.CA | 2026-07-12 / 0.47 | 2026-07-16 / 0.15 | large price advance expanded downside distance |
| BIOC.CA | 2026-07-12 / 1.13 | 2026-07-16 / -0.22 | new Close moved above prior resistance + 2 ATR |
| ETRS.CA | 2026-07-12 / 0.84 | 2026-07-16 / 1.45 | newer candles improved reward geometry, but not enough to pass |
| COPR.CA | 2026-07-12 / 5.00 | 2026-07-16 / 1.33 | rolling support/resistance/ATR window changed |

These are input-data changes, not code divergence. Comparing metrics from different candle dates cannot identify a regression; the identical-candle comparison above is the controlling test.

## 7. Rubix isolation

Rubix Last appears in
[`reports/audits/rr_audit.csv`](../../../reports/audits/rr_audit.csv)
and each chart for visual comparison only. The calculation starts from the archived
completed daily OHLCV and never reads Rubix Last/Bid/Ask. Large differences, such as
BIOC Close 105.70 versus Rubix Last 126.84, do not alter Entry, Stop, Targets, ATR, or RR.

## 8. Exact conclusion

The computation is correct relative to the frozen implementation:

- no formula drift;
- no incorrect ATR;
- no wrong window boundary;
- no live-price contamination;
- no rounding-induced false rejection;
- no Phase 8 same-candle divergence.

The strategy rejects these trades because the existing formula deliberately combines a relatively distant 20-bar-support Stop with a Target2 based on previous resistance plus 2 ATR. For the current strong/extended candidates, downside distance is typically larger than upside distance. Changing that geometry would change the strategy and was not performed.

## 9. Validation

- Independent audit completed for all 113 sequential Risk failures.
- 113 charts generated successfully.
- Both inspected chart classes—ordinary low RR and negative reward—render the requested levels and EMA20/50/200 correctly.
- Audit script syntax/import check passed.
- Full automated regression suite passed: **149 tests**.
- Production source files were not modified.
