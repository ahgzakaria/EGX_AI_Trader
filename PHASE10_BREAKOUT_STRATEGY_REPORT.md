# Phase 10 — BREAKOUT_SWING Implementation and Validation Report

## Executive verdict

`BREAKOUT_SWING` was implemented as a new, fully separate research strategy beside the validated Classic Swing strategy. It is **disabled by default**, is **Decision Support Only**, and contains no broker/order integration.

The frozen Classic engine was not changed. Its sealed Phase 8 replay remains exactly reproducible. `BREAKOUT_SWING` must **not** be enabled as the default strategy yet: its historical return is high, but its drawdown, failure rate, false-breakout rate, and weak combined-portfolio result are materially worse than the validated Classic risk profile.

## Isolation architecture

The new package is self-contained:

- `strategy_breakout/breakout_strategy.py` — top-level decision and trace.
- `strategy_breakout/breakout_entry.py` — breakout entries and six independent stop candidates.
- `strategy_breakout/breakout_exit.py` — independent future-bar exit simulation.
- `strategy_breakout/breakout_targets.py` — five target models and partial levels.
- `strategy_breakout/breakout_scoring.py` — independent Breakout Score and Edge Score.
- `strategy_breakout/breakout_backtest.py` — independent candidate generation, exits, and comparison adapter.
- `strategy_breakout/settings.json` — separate research-only configuration.

No module in `strategy_breakout/` imports the Classic `strategy` package. The existing portfolio simulator and statistics calculator are reused only as unchanged infrastructure after each strategy has independently produced completed trade candidates.

## Implemented entry, stop, and target models

Entry models, independently configurable:

- Breakout Close.
- Retest Entry.
- First Pullback after Breakout.

Breakout evidence:

- Previous resistance breakout.
- Opening Range Breakout when an opening-range field exists.
- High-volume breakout.
- EMA20 continuation.
- Consolidation breakout.
- ATR expansion.
- Higher-high breakout.

Stop models, calculated and testable independently:

- ATR Stop.
- Breakout Low.
- Retest Low.
- EMA20 Stop.
- Recent Swing Low.
- Support Buffer.

Target models, calculated and testable independently:

- Measured Move.
- ATR Expansion.
- Risk Multiple.
- Next Weekly Resistance using only earlier rows.
- Trailing ATR.
- Partial profit levels.

Breakout RR is calculated only from Breakout entry, stop, and target geometry. Classic RR is never reused.

## Daily-bar execution integrity

The signal uses a completed candle, entry occurs on the configured future bar, and exit evaluation begins on the next completed candle. The entry candle is deliberately excluded from exit evaluation because daily OHLC cannot prove whether the high or low occurred first after the opening entry. This also prevents same-date positions from remaining open in the unchanged portfolio simulator.

When both stop and target fit inside a later daily candle, the stop is assumed first. This is the conservative path.

## Dashboard integration

The Swing/Daily Dashboard now displays two separate strategies:

- Classic Strategy: BUY / WATCH / AVOID.
- BREAKOUT_SWING: BUY / WATCH / AVOID.

The per-symbol comparison shows:

- Classic Decision and Classic RR.
- Breakout Decision and Breakout RR.
- Breakout Score, Breakout Confidence, and Edge Score.
- Which strategy currently has the higher displayed quality.

The values are never merged. The canonical `Signal`, Classic ranking, paper-trading recorder, forward-testing recorder, and operational actionability remain driven exclusively by Classic. Any Breakout initialization or evaluation failure is isolated and shown as `UNAVAILABLE`; it cannot remove or rewrite a Classic scanner row.

## Reproducible backtest contract

- Baseline run: `RUN_20260714_125023`.
- Sealed dataset SHA-256: `fafa064f87acf39992f53f3f8bf3b22b09d2ef34fb70b26d53dd932f38704414`.
- Comparison period: 2020-08-09 through 2026-06-09 signal dates.
- Same symbols, initial capital, risk, portfolio capacity, costs, and slippage.
- Classic and Breakout generate trades independently.
- Combined mode combines only the completed candidate books at the unchanged portfolio-capacity layer.
- No network data and no global AI model were used by the Phase 10 comparison.

## Backtest results

| Metric | Classic Strategy | BREAKOUT_SWING | Combined Strategies |
|---|---:|---:|---:|
| Trades | 729 | 843 | 746 |
| Win rate | 26.34% | 45.08% | 37.00% |
| Net profit | 66,427.21 | 161,251.71 | 10,619.40 |
| Return | 66.43% | 161.25% | 10.62% |
| Profit Factor | 1.29 | 1.20 | 1.02 |
| Expectancy | 91.12 | 191.28 | 14.24 |
| Maximum Drawdown | 18.02% | 46.22% | 67.42% |
| Average holding days | 6.08 | 11.46 | 11.07 |
| Sharpe | 0.79 | 0.67 | -0.01 |
| Sortino | 1.76 | 1.05 | -0.02 |
| Final capital | 166,427.21 | 261,251.71 | 110,619.40 |

Breakout-specific diagnostics:

- Candidate trades: 4,897.
- Executed trades: 843.
- Portfolio rejected: 4,054.
- Breakout Failure Rate: 41.87%.
- False Breakout Rate: 30.37%.
- Maximum consecutive losses: 17.

The executed Breakout sample spans every year from 2020 through 2026. It is not limited to a favorable short subperiod.

## Market-regime results

| Strategy | Regime | Trades | Win rate | Net profit | Profit Factor |
|---|---|---:|---:|---:|---:|
| Classic | BULL | 460 | 24.35% | 56,462.40 | 1.35 |
| Classic | SIDEWAYS | 269 | 34.94% | 9,964.81 | 1.14 |
| BREAKOUT_SWING | BULL | 579 | 46.80% | 182,430.41 | 1.35 |
| BREAKOUT_SWING | SIDEWAYS | 263 | 41.44% | -19,032.22 | 0.93 |
| BREAKOUT_SWING | BEAR | 1 | 0.00% | -2,146.48 | 0.00 |
| Combined | BULL | 508 | 39.76% | 91,296.47 | 1.21 |
| Combined | SIDEWAYS | 237 | 30.80% | -78,530.59 | 0.64 |

The new strategy's economic edge is concentrated in BULL regimes and becomes negative in SIDEWAYS conditions. No market-regime detection code was modified; these are observations only.

## Sector result limitation

The sealed Phase 8 dataset and current project do not contain an authoritative symbol-to-sector reference. Sector membership was therefore recorded as `UNKNOWN` rather than inferred or fabricated. `reports/phase10_breakout_sector.csv` explicitly records this limitation for all 843 executed Breakout trades.

## Current-session comparison

Using the immutable current scan archive `RUN_20260719_191032`:

- Classic BUY: 0.
- BREAKOUT_SWING BUY: 19.
- BUY overlap: 0.
- Breakout WATCH: 8.
- Breakout AVOID: 179.
- One archived `^CASE30` frame was not evaluated because it contains only one row; it is reported as an error and is not treated as AVOID.

These 19 BUY values are separate research decisions. They do not create paper trades, forward-test signals, orders, or Classic BUY records.

## Replay and regression validation

Official replay result:

- Source: `RUN_20260714_125023`.
- Replay: `RUN_20260719_221327`.
- Dataset hash match: yes.
- Metrics match: `true`.
- Walk-Forward predictions match: `true`.

Validated metrics reproduced:

- Classic Strategy: 729 trades, 66.43% return, PF 1.29, DD 18.02%.
- AI Ranking Only: 731 trades, 72.98% return, PF 1.31, DD 16.80%.

Validation suite:

- Full automated tests: **159 passed**.
- Targeted Phase 10 tests: **10 passed** as part of the full suite.
- Syntax/import checks: passed.
- Streamlit local health smoke test: passed on a temporary port and process stopped cleanly.
- Classic file byte comparison against `EGX_AI_Trader_RC1`: passed for the frozen strategy, indicators, backtest engine, and portfolio simulator.

## Files added

- `strategy_breakout/__init__.py`
- `strategy_breakout/settings.json`
- `strategy_breakout/breakout_strategy.py`
- `strategy_breakout/breakout_entry.py`
- `strategy_breakout/breakout_exit.py`
- `strategy_breakout/breakout_targets.py`
- `strategy_breakout/breakout_scoring.py`
- `strategy_breakout/breakout_backtest.py`
- `scripts/run_phase10_breakout.py`
- `tests/test_breakout_swing.py`
- `reports/phase10_breakout_summary.csv`
- `reports/phase10_breakout_trades.csv`
- `reports/phase10_breakout_regime.csv`
- `reports/phase10_breakout_sector.csv`
- `reports/phase10_breakout_current_scan.csv`
- `reports/phase10_breakout_diagnostics.json`

## Files modified

- `core/scanner.py` — additive Breakout research evaluation and fail-isolation only. Canonical Classic signal flow is unchanged.
- `dashboard/home.py` — separate visual comparison only; no trading calculation.

## Required answers

### Did Classic Strategy change?

**No.** Frozen Classic files remain byte-identical to RC1, and the official replay reproduces all Classic metrics.

### Did AI change?

**No.** No AI file was modified. Official Walk-Forward predictions are byte/record-equivalent according to Replay (`predictions_match=true`).

### Did RR change?

**Classic RR did not change.** BREAKOUT_SWING has its own independent entry/stop/target geometry and its own RR.

### Did Backtest change?

**The existing backtest did not change.** A new independent Breakout research backtester and a comparison runner were added beside it.

### Did Replay change?

**No.** The official Replay implementation was not modified and reproduced both metrics and predictions.

### How many BUY signals were generated by Breakout Strategy?

**19 BUY signals** on the latest archived current scan. The historical research backtest generated 4,897 candidate BUY trades, of which 843 were executed under the unchanged portfolio constraints.

### How many BUY signals overlap with Classic Strategy?

**0 on the latest archived scan.** Historical same-symbol/same-signal-date candidate overlap was also 0 for this baseline and initial unoptimized configuration.

### Would you recommend enabling Breakout Strategy by default?

**No. Keep it disabled and research-only.** Although standalone return was higher, maximum drawdown rose to 46.22%, Profit Factor fell to 1.20, false breakouts were 30.37%, failures were 41.87%, and Combined performance deteriorated to a 10.62% return with 67.42% drawdown. The next valid step is a separate robustness and forward-testing phase; it is not threshold optimization and it must not replace Classic.

