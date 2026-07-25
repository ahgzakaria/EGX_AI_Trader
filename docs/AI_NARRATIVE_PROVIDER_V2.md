# AI Narrative Provider V2 — guarded external narrative layer

The deterministic evidence engine (Layer 1) remains the **only** source of every market
number. This layer adds an optional external language model as a **narrative and reasoning
layer only**, behind validation that rejects anything it must not say.

```
AnalysisResult
  → sanitized evidence payload      core/ai_narrative_prompt.py
  → external provider adapter        core/ai_narrative_openai.py   (or an injected one)
  → structured JSON response
  → schema + numeric + wording validation   core/ai_narrative_validator.py
  → accepted AI narrative
                        …any failure at any step → Deterministic Fallback
```

Entry point: `core.ai_narrative_provider.build_narrative(result, ...)`. It never raises.

## Configuration (environment only)

| Variable | Default | Meaning |
| --- | --- | --- |
| `AI_NARRATIVE_ENABLED` | `false` | Master switch. False ⇒ behaviour identical to V1. |
| `AI_NARRATIVE_PROVIDER` | `none` | `none`/`deterministic` ⇒ fallback mode; `openai` ⇒ external. |
| `AI_NARRATIVE_MODEL` | *(empty)* | Model id passed to the adapter. |
| `AI_NARRATIVE_TIMEOUT_SECONDS` | `20` | Per-request timeout (clamped 1–120). |
| `AI_NARRATIVE_MAX_RETRIES` | `1` | Retries for **transient** failures only (clamped 0–3). |
| `AI_NARRATIVE_CACHE_ENABLED` | `true` | Cache accepted narratives in-process. |
| `AI_NARRATIVE_BASE_URL` | OpenAI v1 | Any OpenAI-compatible `/chat/completions` endpoint. |
| `AI_NARRATIVE_API_KEY` / `OPENAI_API_KEY` | — | Secret. Read from the environment only. |

Modes: `disabled` · `external_ai` · `deterministic_fallback`.

**Secrets are never** written to source, `config/settings.json`, history records, reports,
logs, the UI, or the exported card. Failure reasons are built from exception *types* and
rule ids, never from provider text.

## What the model may see

A strict allow-list projection of the analysis (`build_evidence_payload`): symbol, company
name, timestamp, market phase, session date, price summary, trend/momentum, SMA/EMA, RSI,
MACD, ATR, volume safety, supports/resistances/breakout/invalidation, supplied scenarios,
confidence components, provider provenance, data-quality warnings. Nothing else exists in
the payload — no keys, no paths, no portfolio, no other symbol, no universe, no raw logs.

Evidence strings are sanitized (controls, bidi marks, code fences and chat-role markers
removed, length-capped) and framed as **untrusted data** the model may describe but never
obey.

## What the model must return

A single JSON object with exactly these keys, concise Arabic, no markdown tables:
`executive_summary_ar`, `technical_read_ar`, `positive_scenario_ar`,
`negative_scenario_ar`, `confirmation_conditions_ar`, `invalidation_conditions_ar`,
`risk_notes_ar`, `data_limitations_ar`.

Rejected outright: any number not traceable to the evidence (fixed 0–4 dp renderings plus a
0.05 **absolute** epsilon — no relative tolerance, so nothing can "match" volume/turnover
by proximity), any direct order (`اشترِ الآن`, `بيع فورًا`, `ادخل بكل السيولة`,
`ضاعف مركزك`), any certainty claim, any markup. Arabic-Indic digits, `٪`, `٬`, `٫` and
thousands separators are normalized before comparison, so honest formatting is accepted.

The **headline stays deterministic** even on the AI path: the single most prominent line on
the page and the card is always evidence-composed.

## Provenance

Every narrative carries `NarrativeProvenance` (source, provider, model, prompt version,
evidence hash, generated-at, validation status, latency, cached, fallback reason). It is
shown in the page's technical-details expander, appended to the append-only history, and
drives the card line — `Narrative  AI Narrative` **only** after a real external response
passed every check; otherwise `Narrative  Deterministic Fallback`.

## Cache

Keyed by `(symbol, evidence_hash, provider, model, prompt_version)`. Accepted narratives
only; failures are never cached. A changed evidence hash always triggers a new request.

## Tests

`tests/test_ai_narrative_provider_v2.py` — 70 tests, provider always mocked. No paid API
call is made by the automated suite, and no socket is opened.
