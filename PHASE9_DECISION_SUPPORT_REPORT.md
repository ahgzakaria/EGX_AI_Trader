# Phase 9 — Decision Support Report

Date: 2026-07-14  
Scope: additive recommendation-quality and operator-UX layer only  
Execution policy: **Decision Support / Paper Only — no broker or order interface**

## Executive Summary

Phase 9 adds an independent institutional-style advisory terminal over completed scanner output. It does not call the scanner, rewrite a strategy signal, replace Strategy Score or Strategy Rank, feed the portfolio, alter paper fills, or participate in a backtest. The user remains the sole decision maker for Buy, Sell, Wait, Ignore, and position size.

The new Edge Score is a transparent 0–10 evidence score. It is displayed as `EdgeScore` and `EdgeRank`; the original `Score`, `Signal`, `Rank`, AI values, entry/exit levels, and operational-actionability fields remain unchanged. A Phase 9 `BLOCKED` quality gate removes a row only from advisory priority lists. It never changes the frozen strategy row.

## Architecture

```text
Completed Scanner Results (read only)       Rubix adapter SQLite (read only)
                 |                                      |
                 +---------------+----------------------+
                                 |
                    DecisionSupportService
                     |       |        |
                Edge score  Market   Quality evidence
                     |       health   (RVOL/spread/liquidity/ATR)
                     +-------+--------+
                             |
               Decision Support Terminal
                 |          |          |
              Heatmaps   Paper alerts  Daily Markdown report
```

The only writable Phase 9 store is `data/decision_support.db`. It contains immutable advisory observations, paper alerts, and user-managed pinned symbols. It has no order, execution, brokerage-account, credential, or authentication tables.

## New Modules

- `decision_support/edge_score.py` — bounded transparent 0–10 Edge Score, factor contributions, missing-factor list, and evidence completeness.
- `decision_support/quality.py` — RVOL, spread, liquidity, bid/ask balance, and ATR feasibility measurements.
- `decision_support/market_health.py` — breadth, advance/decline, regime mix, volume strength, market bias, and overall market score.
- `decision_support/sector_analysis.py` — optional sector mapping and grouping. Missing sector data is reported as Unknown.
- `decision_support/analytics.py` — read-only closed paper-outcome analysis by setup, hour, and weekday.
- `decision_support/database.py` — isolated immutable observations, persistent paper alerts, and pins.
- `decision_support/reporting.py` — reproducible `DAILY_DECISION_SUPPORT_YYYYMMDD.md` output.
- `decision_support/service.py` — orchestration over copies of completed scan rows and read-only Rubix quotes.
- `dashboard/decision_support.py` — Decision Terminal and Performance Analytics pages.
- `tests/test_decision_support.py` — calculation, safety, immutability, language, and frozen-section regression tests.

## Modified Files

- `app.py` — adds two navigation pages only.
- `config/settings.json` — adds an isolated `decision_support` section.
- `config/settings_manager.py` — adds matching defaults without changing frozen sections.
- `dashboard/stock_details.py` — display-only Entry/Target/Stop/EMA/VWAP/Support/Resistance and Volume overlays.
- `scripts/create_release.py` — includes the Phase 9 package and report in RC1.

## Edge Score

The configured factors are trend quality, momentum, relative volume, liquidity, spread, bid/ask balance, ATR feasibility, resistance room, support quality, market strength, sector strength, setup quality, Rubix freshness, historical paper setup performance, volatility, and optional AI probability.

Only observed factors enter the weighted mean. A missing factor is not treated as zero and is not fabricated. The terminal exposes:

- Edge Score (0–10)
- deterministic Edge Rank
- evidence completeness percentage
- factor contributions
- missing evidence
- neutral recommendation text and warnings

Edge Score does not replace the validated scanner score or AI ranking.

## Quality and Market Evidence

- RVOL uses current source volume divided by the mean of prior bars for the configured lookback; the current bar is excluded from its own baseline.
- Spread uses the observed Rubix Bid and Ask midpoint.
- Liquidity uses only available turnover, volume, RVOL, spread, and optional depth/trade-count fields.
- ATR feasibility reports `LOW_PROFIT_POTENTIAL` if the observed ATR is below the requested +2% paper-scalping move.
- Market Health uses completed scan breadth, advances/declines, regime mix, and RVOL.
- Rubix freshness, provider and quote latency remain visible for every recommendation.

## UI Improvements

- Decision Support Terminal with compact market-health cards.
- Top 5, Top 10, Momentum, Breakout, Pullback, and Reversal evidence lists.
- quick symbol search and quality/signal/sector/Edge/spread filters.
- resizable tables plus a visible-column chooser.
- persistent pinned symbols.
- recommendation card with Entry Zone, current price, paper +2% target, paper −2% stop, frozen R/R, confidence, Edge, liquidity, spread, ATR, volume, RVOL, sector, market state, warnings, invalidation, and holding-horizon disclosure.
- Market, Sector, Liquidity, Momentum, and Edge heat maps.
- display-only price-level, EMA/VWAP, support/resistance and volume chart overlays.
- Performance Analytics from immutable closed paper outcomes only.

The wording is intentionally neutral, for example “This opportunity meets the configured high-quality criteria” and “I would wait.” It never directs the user to place an order.

## Paper Alerts and Daily Report

Supported advisory alerts include new frozen-strategy opportunity, Edge threshold, spread improvement, liquidity improvement, Yahoo fallback, and weak market. Target/stop alerts continue to be supplied by the existing paper-only tracking modules; Phase 9 does not duplicate or modify that lifecycle.

The daily Markdown report includes market summary, top opportunities, blocked advisory opportunities, sector status when available, market bias, and risk/discretion reminders. Every report explicitly states that nothing was executed.

## Scalping Boundary

The validated scalping engine remains mathematically unchanged: +2% target, −2% stop, Paper Only, no overnight holding, Rubix-fresh actionability, setup detection, entry simulation, exits, costs, ranking, and risk controls are untouched. Phase 9 improves how existing opportunities are selected for display and compared by advisory Edge evidence; it does not alter trade frequency or simulation output.

## Validation

### Automated tests

- Targeted Phase 9/navigation tests: **18 passed**.
- Full regression suite: **126 passed**.
- Python syntax/import compilation: **passed**.
- Streamlit headless smoke test: **`STREAMLIT_SMOKE_OK`**.

### Phase 8 sealed replay

- Source run: `RUN_20260714_125023`
- Replay run: `RUN_20260714_160832`
- Dataset hash: `fafa064f87acf39992f53f3f8bf3b22b09d2ef34fb70b26d53dd932f38704414`
- Metrics match: **true**
- Walk-Forward predictions match: **true** (895 archived vs 895 regenerated)

Validated reference metrics expected from the sealed archive:

| Mode | Trades | Return | Net Profit | Profit Factor | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| Strategy Only | 729 | 66.43% | 66,427.21 | 1.29 | 18.02% |
| AI Ranking Only | 731 | 72.98% | 72,979.38 | 1.31 | 16.80% |

## Performance Impact

Phase 9 runs only when its dashboard page is opened and a completed scan already exists. It performs local DataFrame calculations and read-only SQLite queries. Scanner, strategy, AI, portfolio, backtest, replay, and forward-testing execution paths have no new Phase 9 dependency. Persistence is minute-deduplicated to avoid repeated identical observations.

## Known Limitations

- The current Rubix adapter schema exposes Last, Bid, Ask, Volume, timestamps, and candles. It does not expose Bid Size, Ask Size, or trade count; those factors remain explicitly unavailable and reduce evidence completeness.
- `data/sectors.csv` is not present, so sector classification, rotation, leaders, weakness, and sector outcome statistics remain Unknown rather than inferred.
- Historical performance is based only on immutable closed paper positions. It is absent when no sufficient evidence exists.
- Native Streamlit tables are resizable and support a column chooser implemented in the page, but filter controls are not browser-level sticky headers.
- Edge thresholds and weights are initial configurable research values, not optimized trading parameters and not validated as an economic trading rule.

## Future Recommendations

1. Obtain an authoritative EGX ticker-to-sector file and version it as a reproducible dataset.
2. Extend the independent read-only adapter schema, if the licensed feed supplies it, with Bid Size, Ask Size, and trade count.
3. Accumulate forward paper evidence before judging Edge calibration or changing any display threshold.
4. Add regime and sector fields prospectively to new paper observations only; never backfill unavailable historical evidence by inference.
5. Keep real-money execution outside this project unless a separate legal, operational, security, and risk-governance program is completed.

## Final Safety Confirmation

- Trading logic changed: **No**.
- AI model changed: **No**.
- Backtest calculation changed: **No**.
- Validated metric changed: **No; final sealed replay result recorded above after completion**.
- Software remains Paper Only: **Yes**.
- Software remains Decision Support Only: **Yes**.
- Approved for automated real-money trading: **NO**.
