# AI Stock Analysis On Demand — Shared Interface Contract

This is the **single shared contract** between the two parallel development worktrees.
It defines **types only** — no calculation, no I/O, no provider access, no strategy logic.
The machine-readable source of truth is [`core/ai_stock_analysis_contract.py`](../core/ai_stock_analysis_contract.py);
this document explains it. A sample instance lives in
[`tests/fixtures/ai_stock_analysis_evidence.json`](../tests/fixtures/ai_stock_analysis_evidence.json).

**Neither worktree may edit the contract during parallel work** — it is integration-only.
Any change to the shared types is an integration step done on `main`, agreed by both sides.

## The four layers (never collapse them)

| Layer | What | Owner | Rule |
|---|---|---|---|
| 1. Calculated numeric evidence | every number the feature uses | Core | the **only** source of numeric truth |
| 2. AI-written narrative | prose describing the evidence | Core | **never** originates or alters a number |
| 3. UI presentation | on-screen layout | UI | renders evidence + narrative; no calculation |
| 4. Exported-card content | shareable self-contained card | UI | pre-formatted strings composed from 1 + 2 |

**Numbers flow one way:** evidence → (rendered into) narrative/UI/card. Narrative and card
carry `derived_from_evidence_version` / `evidence_version` so any figure can be traced back
to the exact evidence set it came from. `NarrativeResult.contains_no_original_numbers` is the
explicit invariant: the narrative engine must never introduce a number that is not already an
evidence field.

## Enums
- **MarketPhase** — `PRE_SESSION`, `CONTINUOUS`, `CLOSING_AUCTION`, `CLOSED`, `HOLIDAY`, `WEEKEND`
- **Recommendation** — `WAIT`, `WATCH`, `NEAR_READY`, `READY_WITH_CONDITIONS`, `AVOID`, `DATA_INSUFFICIENT`
- **ScenarioState** — `READY_WITH_CONDITIONS`, `NEAR_READY`, `WAIT`, `INVALID`, `AVOID`, `DATA_INSUFFICIENT`
- **DataStatus** — `CURRENT`, `CACHE_MODE`, `LIVE_UNAVAILABLE`, `VOLUME_UNSAFE`, `HISTORY_INSUFFICIENT`, `DATA_UNAVAILABLE`

## Types
- **AnalysisRequest** — inputs for one on-demand request (symbol, request_id, as_of, phase, lookback, language). Inputs only.
- **PriceSummary** *(evidence)* — last/prev close, change, O/H/L, currency, price series + adjustment policy, optional live quote.
- **IndicatorSummary** *(evidence)* — SMA/RSI/ATR, avg volume/turnover, and the volume provenance (`volume_series`, `volume_adjustment_policy`, `volume_safe_for_lookback`).
- **KeyLevel** *(evidence)* — one level with `kind`, `price`, and an auditable `basis`.
- **ScenarioResult** *(evidence)* — one decision scenario with `state`, entry/target/invalidation, R:R, machine `conditions` and `missing_confirmations` (not prose).
- **ConfidenceComponent** / **ConfidenceBreakdown** *(evidence)* — weighted components and the derived `overall` (0–100).
- **DataQualitySummary** *(evidence)* — `DataStatus`, domain/provider, freshness, sufficiency, `volume_safe_for_lookback`, and provenance flags (`yahoo_network_used` must be `False`, `yahoo_seed_present`).
- **AnalysisResult** *(evidence aggregate)* — the single numeric source of truth: request + price + indicators + levels + scenarios + confidence + data quality + recommendation + `evidence_version` + `evidence_hash`.
- **NarrativeResult** *(narrative)* — headline/summary/rationale/risks/disclaimer + `model` + `derived_from_evidence_version` + `contains_no_original_numbers`.
- **CardPayload** *(exported card)* — pre-formatted display rows + labels + narrative headline/summary + `evidence_version`. No calculation.
- **AnalysisHistoryRecord** *(history)* — durable record: recommendation, data status, `evidence_version`, `evidence_hash`, confidence, narrative model.

## Data-architecture invariants (enforced by the core engine, reflected here)
- Current research = **EODHD**; live intraday = **Rubix**; unsupported symbols = Rubix Daily
  Bridge over a **frozen Yahoo seed**.
- **Yahoo is never operational and never a comparison source.** `yahoo_network_used` is
  always `False` in `DataQualitySummary`.
- Production and broker execution remain **disabled**.

## Ownership boundaries (parallel work)
**Claude Core** owns: `core/ai_stock_analysis_service.py`, `core/ai_analysis_evidence.py`,
`core/ai_analysis_narrative.py`, `core/ai_stock_analysis_history.py`,
`tests/test_ai_stock_analysis_core.py`, core-validation reports.

**Codex UI** owns: `dashboard/ai_stock_analysis.py`,
`dashboard/ai_stock_analysis_components.py`, `core/analysis_card_generator.py`,
`tests/test_ai_stock_analysis_ui.py`, `tests/test_analysis_card_generator.py`, temporary
visual previews.

**Integration-only (neither branch edits during parallel work):** `app.py`,
`requirements.txt`, `config/settings.json`, `core/data_provider.py`,
`core/research_router.py`, strategy files, and the shared contract files
(`core/ai_stock_analysis_contract.py`, this document, the fixture).
