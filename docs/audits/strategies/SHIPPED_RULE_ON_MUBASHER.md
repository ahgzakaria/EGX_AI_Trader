# The shipped rule's evidence, on the frozen Mubasher record

**Date:** 2026-09-11
**Question:** does the evidence for CONFIRMED_VOLUME_BREAKOUT depend on the
backtest having read Yahoo?
**Answer:** its direction does not; its size in the validation era does.

- **Every input keeps positive lift** in both eras and in walk-forward.
- **The 2016–22 lift is unchanged.**
- **The 2023–26 lift is about a third smaller on Mubasher:** +1.27% to +1.39%,
  against +1.92% on Yahoo.
- **The result leans on fewer trades.** The ten largest carry about a quarter of
  net return, not two fifths.

The rule was not changed. This is a change of input, not a new configuration.

## Why it was run

Every figure quoted for the shipped rule was measured on the Yahoo snapshot
that purpose `backtest` reads. That snapshot lacks 33 active symbols and was
rewritten for six after its freeze
([MUBASHER_VS_YAHOO.md](../providers/MUBASHER_VS_YAHOO.md)). MubasherTrade PRO's
record is the better-supported source, and it is now frozen in
`data/frozen_mubasher`:

- the owner's Export History of 2026-09-07, byte for byte;
- verified identical to the terminal's `history.db` on all 746,434 sessions;
- all 230 active symbols;
- commit `6715990`.

Before a backtest reads it, this checks whether the rule's conclusions depend on
the source.

## Method

The harness is `shipped_rule_evidence.py`'s own: `trades_for`, `score` and
`benchmark_series`, with the stop under the base, twenty sessions, per-symbol
round trip, and lift over the average eligible name on the same days. Only the
input changes, one difference at a time:

| input | symbols | what differs from the row above |
|---|---|---|
| Yahoo, as shipped | 190 | — (reproduces the documented 953 trades, +3.85% / +1.92%, walk-forward +2.41% on 929) |
| **A** Mubasher, Yahoo's span | 190 | the prices only: each series cut to that symbol's Yahoo span before indicators |
| **C** Mubasher, full history | 190 | plus the warm-up the ten-year window cuts off; trades counted from 2016-07-19 |
| **B** Mubasher, every active symbol | 213 | plus the symbols Yahoo lacks (those with at least 300 bars) |

- **Cleaning:** both inputs are cleaned as the backtest loader cleans — NaN rows
  and zero-volume bars dropped.
- **The open:** the rule never reads `Open`. Mubasher's served open is the
  previous close.

## Result

| input | trades | lift 2016–22 | lift 2023–26 | walk-forward | mean net | median net | top 10 share |
|---|--:|--:|--:|--:|--:|--:|--:|
| Yahoo, as shipped | 953 | +3.85% | **+1.92%** | **+2.41%** (929) | +4.78% | −0.20% | 39.5% |
| **A** Mubasher, Yahoo span | 1,011 | +3.88% | +1.27% | +1.87% (982) | +4.44% | +0.21% | 26.9% |
| **C** Mubasher, full history | 1,064 | +3.71% | +1.39% | +1.97% (1,041) | +4.99% | +0.57% | 23.5% |
| **B** Mubasher, every active | 1,118 | +3.59% | +1.37% | +1.90% (1,093) | +5.02% | +0.48% | 23.4% |

| | profit factor | bad years |
|---|--:|--:|
| Yahoo | 2.04 | 2/10 |
| A | 2.03 | 1/10 |
| C | 2.19 | 1/10 |
| B | 2.18 | 2/10 |

### What moves, and what does not

- **The 2016–22 lift does not depend on the source:** +3.85% against +3.88% on
  identical symbols and spans.
- **The 2023–26 lift does.** Prices alone (A) take it from +1.92% to +1.27%.
  Adding warm-up and coverage brings it back only to +1.37–1.39%. Walk-forward
  follows the same way, from +2.41% to +1.87–1.97%.
- **The result depends less on a handful of trades.** On Yahoo the ten largest
  trades are 39.5% of all net return. On Mubasher they are 23–27%, and the
  median trade turns from −0.20% to positive.

### Trade by trade (A against Yahoo, same symbols)

- **Taken on the same symbol and signal day:** 859 trades.
- **Taken on only one source:** Yahoo 94, Mubasher 152.
- **The shared trades net less on Mubasher:** +4.30% against +5.03% on average,
  and the same trade differs by more than a point on 20.3% of them.
- **The single-source trades lean the other way:**

  | taken only on | mean net | median net |
  |---|--:|--:|
  | Yahoo | +2.51% | +0.19% |
  | Mubasher | +5.23% | +2.00% |

  That fits
  [MUBASHER_VS_YAHOO.md](../providers/MUBASHER_VS_YAHOO.md) §5, where a third
  source confirmed Mubasher-only signals 69.7% of the time and Yahoo-only
  signals 32.6%.

**B adds 24 symbols** the Yahoo panel does not carry: ACAP, ACTF, CRST, DGTZ,
GGRN, GPIM, GPPL, GTEX, HBCO, IEEC, KASABF, KRDI, NARE, ORAS, PHGC, QNBE,
SAIB, TANM, TAQA, TYCN, UBEE, UTOP, VLMR, VLMRA. B holds 213, one fewer than
190 + 24; which panel symbol B lacks was not traced. Adding them moves the
2023–26 lift by 0.02 points and the 2016–22 lift by 0.12.

## What this does and does not change

- **The rule's standing survives.** It is still the one rule here with positive
  lift in both eras, on either source.
- **The figure to hold a forward test against is smaller.**
  [FORWARD_TESTING_STARTED.md](FORWARD_TESTING_STARTED.md) compares forward lift
  with +1.92%. On the better-supported record that expectation is +1.27–1.39%.
- **The termination is not reopened.** The portfolio-level finding in
  [INVESTIGATION_SUMMARY.md](INVESTIGATION_SUMMARY.md) — the automated system
  lost to buy-and-hold — was not re-run here and is not claimed either way.
  Nothing in this comparison tests another configuration.
- **"+1.36% valid era"** quoted elsewhere (for instance the Breakout Watch header)
  is the bare trigger's lift before the shipped filters, from `signal_scan.py`.
  It is a different measurement and was not re-run.

## Limits

- **Neither source has a real daily open.** The rule does not read one.
- **Mubasher's discontinuities of early September 2021** fall in the training
  era. The training lift is the one that did not move.
- **The per-symbol costs** come from the same spread table for both inputs.
- **Nothing in the program reads the frozen store yet.** Purpose `backtest`
  still reads Yahoo.

## Reproduce

```
venv/Scripts/python.exe scripts/research/shipped_rule_on_mubasher.py
```
