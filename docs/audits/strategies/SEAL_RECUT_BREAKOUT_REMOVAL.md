# Release Seal Re-cut — `strategy/decision_engine.py`


> **Defensive-asset note (2026-09-10).** This document compares configurations on
> drawdown without stating what the capital does when it is not in a position.
> If the answer is EGP cash, the comparison is affected:
> [REGIME_DETECTABILITY.md](REGIME_DETECTABILITY.md) found that the same shipped
> configuration shows −18.27% in pounds and −65.29% in dollars against the
> market's −66.38%, so an EGP drawdown overstates the protection. **Marked
> ambiguous rather than corrected**, because the defensive asset is not stated
> here and was not assumed.

> **Drawdown note (2026-08-29).** Every `MaxDrawdown` in this document was
> measured before `backtesting/equity.py` marked open positions to market,
> so each is a *closed-trade* drawdown and understates the real figure by
> 0.3-2.6 percentage points. The numbers are left as measured; the
> conversion table for every archived run is in
> [DRAWDOWN_WAS_UNDERSTATED.md](DRAWDOWN_WAS_UNDERSTATED.md).


**What changed:** `breakout_score` no longer contributes to the score, the
confidence or the reasons. It is still computed and still reported as
`"Breakout"` in the decision output.

**Seal record:**

| | |
|---|---|
| File | `strategy/decision_engine.py` |
| Old hash | `6fc3f11b1fdecce23aab587a769243c308791ff027e2ec412ea16f572bda7613` |
| New hash | `8c11e396d14aa55d7cf725829d7570d6080e2b03b84c88b953694a408ddb55ca` |
| Manifest | `strategy_selector/frozen_strategy_manifest.json` |
| Enforcers | `test_adaptive_selector.py:175`, `test_breakout_swing.py:168` |
| Other 16 entries | untouched, verify intact |

Hashes are of line-ending-normalised bytes. A raw `sha256` of the files on a
Windows checkout matches **nothing** in the manifest — all 17 appear broken —
because git checks out CRLF. Anyone verifying by hand must normalise first.

---

## Why the seal was cut

`breakout_score` scored **0.000 on all 638 trades** of the corrected baseline: a
single distinct value across the entire record, summed into the score, the
confidence and the reasons every time.

It is unreachable by construction. It rewards `Close` above the 20-bar high or
within 2% of it; `quality_filter` demands at least 3% of room below resistance
and `require_quality_filter` is `true`. Measured over 10,373 real bars, the two
hold together on **1.15%** where independence predicts 13.4%.

So twenty of the hundred points could never be earned. The strategy ranked on an
80-point scale believing it had 100.

## The contradiction was tested in both directions

Removing the component is not the only way to resolve it. The other way is to
let it fire, by lowering `quality_min_resistance_room` — config only, no seal.
That was measured:

| | Quality thesis (shipped) | Breakout thesis (`room` = 0) |
|---|--:|--:|
| Profit factor | **0.94** | 0.90 |
| Total return | **-14.27%** | -23.79% |
| Max drawdown | **51.15%** | 56.76% |
| Win rate | 20.53% | 19.48% |

The breakout thesis is clearly worse. The quality filter stays; the component
that cannot coexist with it goes.

## The change is provably behaviour-neutral

Baseline and post-removal runs, same pinned config, same data:

| Output file | Result |
|---|---|
| `backtest_results.csv` | **byte-identical** |
| `backtest_statistics.csv` | **byte-identical** |
| `equity_curve.csv` | **byte-identical** |
| `symbol_statistics.csv` | **byte-identical** |

638 trades, 20.53% win rate, profit factor 0.94, -14.27% return, and the same
per-year table in both. Not "approximately unchanged" — the same bytes.

This was better than predicted. The expectation was a small non-zero difference,
because at *signal* level breakout could be positive within that 1.15% overlap
and lift a score past `min_score` before the quality gate rejected it. It never
happened: no such signal survived every gate in ten years of data.

## What this does and does not buy

It does **not** improve the strategy. Nothing measured here changes any number.

It removes a latent hazard. The component was inert under the shipped config and
**harmful under a neighbouring one** — anybody setting
`require_quality_filter: false` or lowering the resistance room would silently
re-enable a contribution that measures worse. A component that is dead in one
configuration and damaging in another is worse than an absent one.

It also makes the scale honest. `min_score: 50` was being compared against a
total that could reach 80, not 100.

## What was deliberately not done

The score has no signal — `r = -0.032, t = -0.81` — and its components do not
predict returns ([SCORE_DIAGNOSIS.md](SCORE_DIAGNOSIS.md)). Rebuilding or
reweighting it on this data would be fitting noise: every effect measured in this
investigation has `|t| < 2.3`, and two confident recommendations have already
been reversed by better measurement.

So the seal was cut for a correctness fix that is provably neutral, and not for
a redesign. The strategy still loses money after real costs, and that is not a
scoring problem.

## Reproduce

```
venv/Scripts/python.exe scripts/research/isolated_backtest.py --label check \
  --set strategy.require_candle_confirmation=true --set backtest.trailing_enabled=true
```

Compare against
`reports/experiments/20260826_070634_baseline_corrected_costs`.
