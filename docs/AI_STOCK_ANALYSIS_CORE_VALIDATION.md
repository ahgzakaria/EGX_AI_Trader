# AI Stock Analysis Core — Validation Report

Branch: `claude/ai-stock-analysis-verification-bf14a4` (official CORE branch)
Scope: deterministic backend for analyzing **one** manually-requested EGX symbol.
Contract followed (unmodified): `core/ai_stock_analysis_contract.py`,
`docs/AI_STOCK_ANALYSIS_CONTRACT.md`.

## Modules delivered (Core-owned files only)

| File | Layer | Responsibility |
|---|---|---|
| `core/ai_analysis_evidence.py` | 1 — numeric evidence | Deterministic indicators + levels + scenarios + confidence + data quality → `AnalysisResult`. The only source of numbers. |
| `core/ai_analysis_narrative.py` | 2 — narrative | Safe Arabic narrative interface; numeric-hallucination validation; deterministic fallback. |
| `core/ai_stock_analysis_history.py` | history | Append-only JSONL store of `AnalysisHistoryRecord`. |
| `core/ai_stock_analysis_service.py` | orchestration | One-symbol pipeline: phase → history → live overlay → evidence → narrative → history. |
| `tests/test_ai_stock_analysis_core.py` | tests | 34 core tests. |

## Requirements coverage

- **One-symbol-only**: `analyze_symbol` validates and rejects collections/empties; never scans the universe.
- **Price summary**: last/prev close, change %, O/H/L, currency, optional Rubix live quote.
- **EMA 20/50/200**: computed (`_ema`) and surfaced as dynamic support/resistance `KeyLevel`s (`EMA20/50/200`) and in trend reasons. See limitation #1 on the `sma_*` field names.
- **SMA 20/50/200, RSI(14 Wilder), MACD(12/26/9), ATR(14 Wilder), Avg Volume(20), Volume Ratio, OBV**: all implemented. OBV computed **only when volume is lookback-safe**.
- **Trend / momentum classification**: `_classify_trend`, `_classify_momentum`.
- **Expected-range position**: position within the 20-day high/low channel.
- **Deterministic support/resistance**: channel high/low + SMA/EMA dynamics, sorted by distance.
- **Breakout & invalidation levels**: channel-high breakout, ATR-based invalidation stop.
- **Scenario engine**: `breakout_continuation` (+ `support_breakdown`) with machine `conditions`/`missing_confirmations`.
- **Deterministic confidence**: weighted trend/momentum/liquidity/data-quality components; `overall = Σ weight·score`.
- **Structured evidence**: full `AnalysisResult` with `evidence_version` + reproducible `evidence_hash`.
- **Safe Arabic AI narrative**: pluggable generator; output validated so it can never originate a number; else deterministic fallback.
- **Numeric hallucination validation**: `validate_no_original_numbers` against the evidence-traceable number set.
- **Deterministic fallback narrative**: evidence-only Arabic prose (`deterministic-fallback@1.0.0`).
- **Append-only analysis history**: JSONL, never rewrites existing lines.

## Data-architecture invariants enforced

- CURRENT_RESEARCH_V2 / EODHD for daily history; Rubix for live overlay; Rubix Daily Bridge via the router for unsupported symbols.
- **No Yahoo operational use or comparison** — `yahoo_network_used` is hard-coded `False`.
- Production / broker execution untouched; no strategy, threshold, indicator, TP/SL, or execution flag changed.

## Contract correction (pre-UI amendment)

This build **amends the shared contract** — the sanctioned final integration step before UI
work — so every technical number the UI needs is a dedicated typed field. Nothing is hidden
in strings, reasons, or KeyLevel labels.

- **IndicatorSummary** now has explicit typed fields: `sma_20/50/200`, **separate**
  `ema_20/50/200`, `rsi_14`, `macd`/`macd_signal`/`macd_histogram`, `atr_14`,
  `average_volume_20`, `volume_ratio`, `obv`, `expected_range_position`, `turnover`,
  `volume_safe`, plus volume provenance. EMA values never occupy SMA fields.
- **PriceSummary** now has typed `session_date`, `close`, `previous_close`,
  `change_amount`, `change_percent`, `open`, `high`, `low`, `volume`, `turnover`, and a
  live block `last`/`bid`/`ask`/`spread_percent`/`quote_timestamp`. Missing values stay
  `None` (never zero-filled).
- **ScenarioResult** now has typed `trigger`, `entry_low`, `entry_high`, `target`, `stop`,
  `remaining_room_percent`, `risk_reward`, `confidence`, and machine-fact
  `confirmation_requirements` / `invalidation_conditions`.

### Session-phase correction
Auction-aware Cairo bands: `<10:00 PRE_SESSION`, `10:00–<14:15 CONTINUOUS`,
`14:15–<14:25 CLOSING_AUCTION`, `>=14:25 CLOSED`. The 14:25–14:30 tail is **CLOSED**, never
CONTINUOUS. Boundary tests cover 14:14:59 / 14:15:00 / 14:24:59 / 14:25:00 / 14:30:00.

### Provenance correction
`yahoo_network_used` / `yahoo_seed_present` / `provider` / `data_domain` / `live_provider`
are **derived from the history-frame metadata**, not hard-coded. The Yahoo-never invariant
is verified (`yahoo_network_used` stays False), and a frozen bootstrap seed may set
`yahoo_seed_present=True`.

### Volume-safety correction
The Core reads the router's already-corrected volume series verbatim (no second universal
split-volume transform; test asserts `average_volume_20` equals the mean of the supplied
series). When `volume_safe` is False: `average_volume_20`, `volume_ratio`, `obv`, and
`turnover` are `None`, liquidity confidence drops, and volume-dependent narrative claims are
suppressed.

### Numeric-validation correction
The narrative allow-list is built from all newly-typed fields; matching now uses exact
fixed-precision renderings (0–4 dp) plus a tiny absolute epsilon — the broad relative
tolerance is removed, so an unrelated figure can no longer match a large volume value.
Session-timestamp years remain traceable; fabricated prices are rejected. Deterministic
Arabic fallback retained.

## Determinism

- Every emitted number is rounded to a fixed precision.
- `evidence_hash` covers only completed-session numeric/decision content — it **excludes**
  wall-clock timestamps, request ids, and the volatile live quote (which now no longer
  feeds confidence) — so identical frames → identical `evidence_hash` / `evidence_version`
  (tests: `test_evidence_is_deterministic`, `test_live_quote_does_not_change_evidence_hash`).

## Test results

- New/updated core tests: **50 passed** (`tests/test_ai_stock_analysis_core.py`).
- Full regression: **656 passed, 7 skipped, 3 failed**.
  - Baseline: **606 passed, 7 skipped, 3 failed**.
  - Net: **+50 passed, no new failures.**
  - The 3 failures are pre-existing/environmental and unrelated to this feature:
    - `test_adaptive_selector.py::test_frozen_classic_and_breakout_manifest_is_unchanged`
    - `test_breakout_swing.py::test_frozen_engine_files_match_release_candidate`
    - `test_market_data_providers.py::test_dashboard_route_falls_back_and_backtest_stays_on_yahoo` (yahoo cache miss)

## Limitations / integration notes

1. **Live overlay** defaults to a best-effort Rubix read that degrades to
   `live_available=False` when no Rubix DB/config is present; fully injectable for tests.
   Live values populate the typed price block and `data_quality.live_available` but do not
   affect the deterministic `evidence_hash`.
2. **`CardPayload`** (Layer 4) and `NarrativeResult` (Layer 2) are unchanged by this
   amendment — the UI composes display strings from the now-typed evidence fields.
3. No UI, no PNG cards, no `app.py` wiring — those are Codex-UI / integration scope.
