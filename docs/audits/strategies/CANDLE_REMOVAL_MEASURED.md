# Removing Candle Confirmation — Measured, and It Costs

> **Drawdown note (2026-08-29).** Every `MaxDrawdown` in this document was
> measured before `backtesting/equity.py` marked open positions to market,
> so each is a *closed-trade* drawdown and understates the real figure by
> 0.3-2.6 percentage points. The numbers are left as measured; the
> conversion table for every archived run is in
> [DRAWDOWN_WAS_UNDERSTATED.md](DRAWDOWN_WAS_UNDERSTATED.md).


**Supersedes [CANDLE_CONFIRMATION_REMOVAL.md](CANDLE_CONFIRMATION_REMOVAL.md)**,
which measured this under the broken cost model.

**Status:** shipped. Seal on `strategy/decision_engine.py` re-cut a second time.
The measurement is unfavourable and is recorded here in full.

---

## 1. The numbers

All runs: sealed engine plus the breakout removal, corrected costs (0.964% round
trip), trailing stop on, `min_rr` 1.5, configuration pinned.

| | Baseline | Contribution removed, gate on | **Fully removed** |
|---|--:|--:|--:|
| Trades | 638 | 639 | **1,201** |
| Win rate | 20.53% | 19.56% | 19.98% |
| Profit factor | **0.94** | 0.92 | 0.91 |
| Total return | **-14.27%** | -18.55% | **-36.94%** |
| Max drawdown | **51.15%** | 51.43% | 58.89% |
| Avg per trade | **-0.005%** | -0.03% | -0.08% |
| Sharpe | **-0.01** | -0.05 | -0.14 |
| Years positive | 3/10 | 3/10 | **6/10** |

**Removal is worse on every measure except year-consistency.** That is recorded
first because it argues against what was done.

## 2. The middle column no longer exists

It was measured before `candle_score` was changed to refuse a fabricated `Open`.
With the current code the detector returns 0 on every bar this project can
reach, so `require_candle_confirmation: true` blocks **every** signal — zero
trades, not 639.

That is correct fail-closed behaviour: a confirmation that cannot be computed
cannot be required. But it means the choice is now binary, and the gate is off.

## 3. Why the cost is not evidence for keeping it

Trade count went from 638 to **1,201**. The gate was not selecting better
trades — it was halving how many were taken. With negative expectancy, fewer
trades means less loss.

This is the second time this pattern has appeared. The trailing stop did the same
thing ([TRAILING_STOP_VERDICT.md](TRAILING_STOP_VERDICT.md)): 80% of exits at a
small loss, removing it doubled exposure and deepened the loss.

**The strategy had two accidental throttles.** Neither improved selection; both
reduced exposure. Removing them did not break anything — it exposed a negative
expectancy that was being masked by trading less.

`candle_score` measures **r = -0.024, t = -0.61** against the outcome. There is
no signal in it. A component with no signal cannot be the reason results
worsened; what worsened them is trading more often at a losing expectancy.

Year-consistency improving to 6 of 10 fits the same reading: the same loss spread
across more trades, not an edge appearing.

## 4. Why it was removed anyway

Not performance. The argument is that the component was reporting things that did
not happen.

With `Open` carried forward from the previous close in 96–98% of bars:

- `Bullish Engulfing` requires `Open < prev Close` and `Bullish Harami` requires
  `Open > prev Close`. Both reduce to `x < x`. **Structurally unreachable.**
- `Doji` becomes "the close barely moved from yesterday" and fired on **63%** of
  trades — the most-cited confirmation in the entire record.
- `Morning Star` becomes a comparison of lagged returns.

A signal citing *Morning Star* in its reasons was not telling its reader what
happened. That is the whole case, and it does not depend on the backtest.

## 5. What was built instead of a deletion

`candle_score` was not stripped out. It now **refuses to score when the `Open` is
known to be fabricated**, and says so:

```
score 0, confidence 0
reasons: ["Candle patterns unavailable (Open OUT_OF_RANGE)"]
```

Three properties, each pinned by a test:

- It **refuses on a positive finding** (`CARRIED_FORWARD` / `OUT_OF_RANGE`) and
  explains itself, rather than going quiet and looking like a real zero.
- It **does not refuse on `UNKNOWN`.** pandas `attrs` does not survive every
  operation, so a frame can lose its provenance in transit. Condemning on
  UNKNOWN would silently disable candles wherever that happened. Absence of
  evidence is not evidence of fabrication.
- **The refusal lifts by itself** when a real `Open` arrives. Without that test
  nothing guarantees this is temporary; someone could fix the data and find
  candles still dark with no explanation.

## 6. Seal record

Second re-cut of `strategy/decision_engine.py` this session.

| | |
|---|---|
| Previous | `8c11e396d14aa55d7cf725829d7570d6080e2b03b84c88b953694a408ddb55ca` |
| New | `e2a85f8fe0380025c626682fbb7b03d9642a1227b70cf36649594c0b04ebe3c3` |

Unlike the breakout re-cut, **this one is not behaviour-neutral.** That cut was
justified by byte-identical outputs; this one changes results materially and is
justified by correctness alone. The distinction is deliberate.

Other 16 entries untouched and verifying. Suite: **3,500 passing.**

## 7. What this leaves open

The strategy is now running without either accidental throttle, on its true
negative expectancy. A **deliberate** limit on trade count is needed to replace
the accidental ones — `min_rr` is the nearest candidate and measured favourably
under the old cost model, untested under the corrected one.

Stated plainly: a throttle reduces losses, it does not create an edge. What
creates one is an entry signal that predicts, and nothing measured in this
investigation does. `atr_percent` at r = +0.080, t = +2.03 is the only positive
reading and is marginal against roughly twenty fields tested.

## Limits

- One dataset, one universe, in-sample.
- The middle column of §1 describes code that no longer exists.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/isolated_backtest.py --label candle_off \
  --set strategy.require_candle_confirmation=false --set backtest.trailing_enabled=true
```
