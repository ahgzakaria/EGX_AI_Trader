# Does the Strategy Select, and Does It Execute?

**Question:** every throttle tested reduced losses by reducing exposure rather
than by choosing better. So: does the entry date carry information, and does the
execution destroy value?

**Status:** measured on the corrected isolated baseline
(`reports/experiments/20260826_070634_baseline_corrected_costs`, 638 trades).
Read-only.

**Short answer:** selection carries no information (t = +0.65). Execution is
neutral (t = -0.02). The strategy's gross drift and its trading costs are the
same number to three decimal places, and everything else is noise around that.

---

## 0. A correction, and the trap that caused it

An earlier version of this analysis reported that execution destroyed
**-0.475% per trade**. That was wrong, and the cause is worth more than the
finding was.

`holding_days` is **calendar days** — `backtesting/trade.py` computes
`(exit - entry).days`. I indexed forward by that many **trading bars**, which
measured a holding period **49% longer** than the trades actually ran (mean 6.13
calendar against 4.13 bars). The extra drift became a phantom execution drag.

The unit collision is real and lives in the code, not just in my analysis:

| Field | Where | Unit |
|---|---|---|
| `holding_days` | `backtesting/trade.py:163` | **calendar days** |
| `max_holding_days` | `exit_manager.py:77, 293` (`entry_index + max_holding_days - 1`) | **trading bars** |

So `max_holding_days: 20` means twenty *bars*, about twenty-eight calendar days,
while a reported `AverageHoldingDays` of 6.13 is calendar — about 4.1 bars. A
reader comparing 6.13 against a cap of 20 concludes there is room to spare; in
the unit the cap is enforced in, the position sits at 4.1 of 20.

Pinned by `tests/test_holding_period_units.py` so the next reader meets it there
rather than in a wrong number.

## 1. Selection

For every trade, the same symbol entered on a **random** day and held the same
number of bars, 200 draws each, sampled within 250 bars either side:

| | |
|---|--:|
| Strategy entry, close to close | +0.9630% |
| Random entry, same symbol and length | +0.7944% |
| **Edge from choosing the day** | **+0.1686%** |
| **t** | **+0.65** |

The exit rule, the limit entry and position sizing are stripped from both sides,
so what remains is the choice of day. Costs are identical and cancel.

**t = 0.65 is nothing.** And the trades beat their own controls on only **31.5%**
of occasions, so even the weak positive mean is a small tail rather than a
tendency. Per year the edge is positive in 3 of 10, with 2025 alone (+2.648%)
carrying the aggregate.

The entry model does not know when to buy. It inherits whatever drift the symbol
had, which the gates already selected for by requiring an uptrend.

## 2. Execution

| | |
|---|--:|
| Hold entry close → exit close, **same bars** | +0.9630% gross |
| The same, after the corrected round trip | **-0.0008%** net |
| What the strategy's execution actually returned | **-0.0047%** net |
| **Execution versus simply holding** | **-0.0039%** |
| **t** | **-0.02** |

**Execution is neutral.** Four thousandths of a percent, t indistinguishable
from zero.

Decomposed, it is slightly *favourable*: the entry fills 0.103% above the day's
close (a limit at `buy_high`, so you pay up), while exits fill **0.204% above**
the close, because targets are reached intraday. Net, the mechanics earn about a
tenth of a percent rather than losing half of one.

There is nothing to fix here. The earlier claim that there was is withdrawn.

## 3. Where the money actually goes

| | |
|---|--:|
| Gross drift over the realised holding period | **+0.9630%** |
| Corrected round-trip cost | **0.9638%** |

**A difference of eight ten-thousandths of a percent.** The strategy operates
exactly at its own break-even with transaction costs, and every effect measured
in this investigation — selection, exits, throttles, score components — is noise
around that equality.

That reframes the whole problem. It is not that the strategy is badly executed or
badly filtered. It is that it captures a drift equal to what it pays to capture
it, and nothing in it improves on that.

## 4. What follows

- **Do not tune execution.** It is neutral, measured.
- **Do not tune selection thresholds.** The entry date carries no information, so
  a threshold on a scoring of it selects nothing
  ([SCORE_DIAGNOSIS.md](SCORE_DIAGNOSIS.md)).
- **Throttles reduce how often the cost is paid.** That is real but bounded: it
  moves the result toward zero, never above it.
- **The only lever with headroom is cost.** At 0.9638% against 0.9630% of drift,
  a materially cheaper round trip is the one change that would move the strategy
  from break-even to positive without needing a signal it does not have. Whether
  EGX brokerage at retail scale allows that is a commercial question, not an
  engineering one.
- **Or find a real signal.** Nothing measured here is one. The strongest reading
  in the entire project remains the overnight gap at t = +15.7
  ([OVERNIGHT_GAP_PROBE.md](OVERNIGHT_GAP_PROBE.md)) — seven times any effect
  inside the strategy — and it too fails to clear friction on a single night.

## Limits

- One dataset, one universe, in-sample.
- The random-entry control samples within ±250 bars of the real entry, so it
  compares against a similar era rather than the whole history.
- `+0.9630%` is a mean over heterogeneous holding lengths; it is not a per-day
  rate.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/does_selection_add_value.py \
  reports/experiments/20260826_070634_baseline_corrected_costs
```
