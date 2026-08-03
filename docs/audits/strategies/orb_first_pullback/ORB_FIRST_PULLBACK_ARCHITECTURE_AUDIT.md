# ORB + First Pullback — Architecture Audit

Audit date: 2026-08-02

Branch: `feat/scalping-opening-range-first-pullback`

Worktree: `F:\EGX_ORB_first_pullback_wt`

Baseline HEAD: `3d6240f7809602ebed0d7a13d79539f3c74377de`

Scope: Phase 1 design only; no strategy or production logic was changed.

## Executive conclusion

The repository already has reliable building blocks for canonical symbols, EODHD D-1 history, Cairo-aware session classification, read-only Rubix access, spread/freshness gates, WAL persistence, deterministic paper fills, conservative same-bar exit ordering, and account-level kill switches. Those components should be composed behind a new isolated `scalping_orb` domain package.

The current primary scalping path must not be relabelled as the new strategy. Its opening-range and VWAP logic is embedded in older readiness/scoring code and does not implement the required sequence of completed opening range, genuine momentum, anti-chase, first pullback, reclaim, structural stop, and risk-by-stop-distance sizing. Reusing those decisions would preserve exactly the behavior this project is intended to replace.

## Current user-facing architecture

| Area | Current implementation | Finding | Phase 2/4 disposition |
|---|---|---|---|
| Navigation | `app.py` wires `show_scalping_dashboard`, `show_active_scalping_trades`, and `show_scalping_history` | Separate Scalping pages already exist | Retain routes; redesign the dashboard only in Phase 4 |
| Main dashboard | `dashboard/scalping.py::show_scalping_dashboard` | Three primary tabs: Stable Range-Bound, Uptrend Pullback, Live Monitor | Replace primary experience in Phase 4 with the seven ORB pipeline tabs |
| Stable Range-Bound UI | `dashboard/scalping.py::_render_range_bound_tab` | Presented as a primary candidate/readiness path | Move under **Secondary Research Filters / فلاتر بحثية ثانوية**; do not delete backend |
| Uptrend Pullback UI | `dashboard/uptrend_pullback.py` rendered from `dashboard/scalping.py` | Daily EMA5/EMA10 selector is also connected to live readiness | Keep daily context only; remove its role as an intraday entry-readiness producer in the ORB UX |
| Live monitor | `dashboard/scalping.py::_render_live_monitor_tab`, `_live_entry_monitor_panel` | Combines readiness from the two old selectors | ORB service becomes the sole primary execution-state source |
| Active trades | `dashboard/scalping.py::show_active_scalping_trades` | Reads the existing scalping paper database | Phase 4 should add an ORB-specific reader; do not mix or rewrite old history |
| History | `dashboard/scalping.py::show_scalping_history` | Existing signals/rejections/exits/summary views | Preserve; add an isolated ORB Session Review reader in Phase 4 |
| Settings/backtest | `dashboard/scalping.py::show_scalping_settings`, `show_scalping_backtest` | Existing fixed-percent risk/exit settings and minute-data backtest UI | Do not reuse fixed 2% stops; add auditable ORB research settings separately |

## Existing backends

### Stable Range-Bound

- Historical selection and scoring live in `scalping_expected_range/`.
- `scalping_expected_range/frozen_watchlist.py` provides immutable D-1 watchlists, deterministic identity/fingerprints, a WAL repository, and EODHD history loading.
- `scalping_expected_range/live_readiness.py` reads Rubix in bounded batches and currently calculates opening-range triggers, an OHLCV-derived VWAP proxy, and a readiness score that can reach `ENTRY_READY_RESEARCH_ONLY`.
- `scalping_expected_range/paper_state.py` provides restart-safe cursor, state, transition, deduplicated signal, counter, and feed-health patterns.

Reusable: frozen input identity, D-1 cutoff discipline, read-only Rubix batch-query pattern, immutable signal deduplication pattern, feed-health recording pattern.

Not reusable as ORB decisions: historical range-consumption scoring, current trigger state, current readiness score, and any old selector-to-entry-readiness connection.

### Daily EMA5/EMA10 Uptrend Pullback

- Typed daily analysis and support/trend profiles are in `scalping_uptrend_pullback/selection.py`.
- Its frozen watchlist and live-readiness adapter share the existing D-1 and Rubix infrastructure.
- It is a daily selector. It is not the required intraday first-pullback-after-ORB state machine.

Reusable only as optional pre-session context: daily trend, support/resistance, liquidity, volatility, and available upside. A trend condition must not become mandatory unless later evidence supports it.

## Provider and symbol boundaries

| Concern | Existing source | Decision |
|---|---|---|
| Active universe | `data/universe/egx_universe.csv`, loaded by `core.symbols`/`core.universe` | Reuse the 241 ACTIVE canonical symbols |
| EODHD mapping | explicit universe records plus `providers.symbol_mapping.to_eodhd_symbol` | Use explicit mapping/status only; no suffix guessing |
| Rubix subscriptions | `providers.rubix_subscription.build_rubix_subscription_plan` | Reuse; it admits only `VERIFIED_FEED_OBSERVED` mappings and reports unmapped symbols |
| Rubix storage symbols | suffix-free ticker in SQLite | Resolve only through the authoritative universe record |
| Daily history | EODHD completed data up to D-1 | Reuse frozen cutoff/freshness infrastructure |
| Intraday | `data/rubix_live_market.db` through read-only SQLite | Sole operational intraday source |
| Yahoo | legacy comments and compatibility names remain in some modules | Must not enter the ORB path, freshness comparison, fallback, or calibration |

The database currently contains more symbols than the active universe because it also contains historical/retired observations. Database membership must never replace the 241-symbol authoritative active universe.

## Session and timestamp handling

- `core/egx_session.py` owns Cairo timezone normalization, trading weekdays/holidays, previous/next sessions, completed-session freshness, and auction-aware phases.
- Its auction-aware boundary is continuous trading `10:00–14:15` Cairo and closing auction `14:15–14:25` Cairo.
- `scalping_expected_range.live_readiness.live_session_phase` and `_query_boundaries` already cap completed-minute queries at the continuous close and avoid treating the 14:15 auction minute as continuous data.
- `scalping/session_validator.py` separately audits continuous and auction UTC windows and can inform feed-quality tests.

The new engine should consume one canonical session-boundary object derived from `core.egx_session`, use timezone-aware UTC internally, convert to Cairo for exchange rules, and use half-open intervals. Opening range is `[10:00, 10:15)` Cairo: fifteen one-minute bars starting 10:00 through 10:14, finalized only at 10:15 after the fifteenth bar closes. Continuous bars are `[10:00, 14:15)`; auction observations are stored and displayed separately but never fed into the ORB strategy.

## Intraday data and indicator utilities

| Utility | Strength | Limitation for the new strategy |
|---|---|---|
| `scalping.data_sources.RubixScalpingDataSource` | Read-only SQLite URI, schema validation, UTC-to-Cairo conversion | Loads a whole calendar day and does not itself enforce completed bars or continuous/auction separation |
| `scalping.setup_detector.sanitize_intraday_bars` | Removes invalid OHLCV rows, sorts, and deduplicates timestamps | A bar sanitizer, not a tick/bar builder; current OR uses `session.head(N)`, which is unsafe with sparse minutes |
| `scalping.decision.spread_percent` | Correctly returns unavailable when bid/ask are missing | Reuse, but map missing values explicitly to `SPREAD_UNAVAILABLE` |
| `scalping.decision.assess_actionability` | Fails closed on provider, freshness, quote age, bid/ask, liquidity, spread, and cutoff | Current session label and old config are too coarse; adapt the pattern to ORB typed gates |
| `scalping_expected_range.live_readiness._minute_vwap` | Deterministic typical-price × bar-volume calculation | It is a bar proxy, not true exchange VWAP; do not expose it as true VWAP |
| `_opening_range_triggers` in the same module | Detects close breakout and later retest from completed stored bars | Coupled to old scoring; lacks 5m qualification, extension, first-pullback identity, structural stop, and transition evidence |
| `scalping.range_scanner` | Reads historical 1m OHLCV and has research summaries | Old range strategy semantics; not the ORB primary engine |

No existing trustworthy 5-minute aggregation engine satisfies duplicate-event handling, partial-bar completeness, no-future-information, session reset, and auction exclusion together. Phase 2 requires a new deterministic bar component, while reusing the existing session and read-only source primitives.

## Rubix collector and storage

- The application reads `data/rubix_live_market.db`; configuration can resolve through `RUBIX_DB_PATH` or `config/settings.json`.
- `providers/rubix_sqlite_provider.py` validates `quotes`, `candles_1m`, and `feed_metrics` and exposes read-only quote/minute metadata.
- `scripts/launch_rubix_production.py` supervises the external collector; the app does not connect to Rubix directly.
- `services/system_health.py`, `services/launcher_startup.py`, and `scripts/rubix_health.py` provide health/readiness patterns.
- The collector database is the sole writer-owned source. The ORB engine must never migrate it, add tables to it, or acquire a write connection.

Actual fields and history quality are detailed in `ORB_FIRST_PULLBACK_DATA_READINESS.md`.

## Paper trading, sizing, and portfolio risk

| Component | Reuse decision |
|---|---|
| `scalping.database.ScalpingDatabase` | Reuse transaction/WAL/immutability patterns, not the existing DB or schema |
| `scalping_expected_range.paper_state.PaperStateStore` | Reuse restart/dedupe/transition patterns, not its scenario tables |
| `scalping.paper_portfolio.ScalpingPaperPortfolio` | Reuse orchestration ideas only; its fill and risk contracts assume fixed-percent stops |
| `scalping.risk_engine.can_open` | Reuse kill-switch concepts: max positions/trades, consecutive losses, daily realized loss, heat, cooldown |
| `scalping.risk_engine.position_size` | Do not reuse as-is: it derives stop distance from a universal percentage |
| `scalping.entry_engine.build_entry_fill` | Reuse tick rounding, ask-side fill, fees/slippage patterns; replace target/stop calculation with frozen structural levels |
| `scalping.exit_engine.evaluate_bar` | Reuse conservative stop-first handling when stop and target occur in the same bar; extend for partials, trailing, and time stop |

ORB position size must be `floor(configured_cash_risk / executable_entry_to_frozen_stop_distance)`, then capped by exposure, liquidity, portfolio heat, and configured concurrent positions. It must not hard-code capital, widen stops, or average down.

## Existing tests and fixtures to reuse

- `tests/test_scalping_module.py`: Rubix 1m schema fixture, risk switches, sizing, WAL, entry/exit behavior.
- `tests/test_live_entry_readiness.py`: quote/candle batch fixtures, session boundaries, stale/missing/mapping states.
- `tests/test_rubix_completed_daily_bridge.py`: minute-volume reliability and completed-session safeguards.
- `tests/test_market_data_providers.py`: provider schema and quote/minute fixtures.
- `tests/test_egx_dynamic_calendar.py`, `tests/test_expected_range_freshness.py`, and session-validation tests: Cairo calendar and phase behavior.
- `tests/test_clean_scalping_ui.py`, `tests/test_app_navigation.py`: existing UI/nav contracts that Phase 4 must deliberately update.
- AI, Swing/Daily, breakout/breakdown, Pullback Health, universe, and infographic suites are mandatory regression boundaries.

## Target architecture

```text
EODHD completed D-1 ──> Pre-session context ──┐
                                              ├─> ORB state service ─> research repository
Rubix read-only quotes/1m ─> bars/quality ────┘         │
                                                        ├─> paper risk/trade manager
                                                        └─> dashboard read model

Stable Range-Bound ─┐
                    ├─> secondary context only (never ENTRY_READY)
Daily Pullback ─────┘
```

The state engine remains deterministic and typed. An LLM may describe stored evidence later, but may not select candidates, transition state, set levels, size a position, or manage a trade.

## Boundaries confirmed for Phase 1

- No trading, collector, database, provider, UI, risk, or strategy code was changed.
- No production or broker execution was enabled.
- No existing database was migrated or written.
- No Yahoo route or fallback was added.
- Stable Range-Bound and Daily Uptrend Pullback backends remain intact.
- Swing/Daily, AI Analysis, breakout/breakdown, Pullback Health, paper history, and saved runs remain untouched.
