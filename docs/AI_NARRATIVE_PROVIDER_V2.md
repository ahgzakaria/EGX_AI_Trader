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
| `AI_NARRATIVE_BASE_URL` | OpenAI v1 | Any OpenAI-compatible Responses endpoint. |
| `OPENAI_API_KEY` | — | Secret, standard name. Read from the environment only. |
| `AI_NARRATIVE_API_KEY` | — | Optional documented alias, used only when `OPENAI_API_KEY` is unset. |

Modes: `disabled` · `external_ai` · `deterministic_fallback`. The model is never
hard-coded — it comes from `AI_NARRATIVE_MODEL`; the adapter's built-in default applies
only when that is empty. Example values live in [.env.example](.env.example) with every
secret left blank.

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

This is enforced at the transport level too: the request uses OpenAI's **Responses API**
(`POST /v1/responses`) with strict Structured Outputs — `text.format.type = "json_schema"`,
`strict: true`, `additionalProperties: false`, and all eight sections in `required` — so
the shape is constrained by the API, not merely requested in prose. The request carries no
tools (`"tools": []` — no function calling, no web search, no file search), no
`previous_response_id`, and `"store": false`, so nothing persists and no unrelated context
enters. A Structured-Outputs **refusal** arrives as a `refusal` content item and is turned
into a typed error, never mistaken for narrative.

Rejected outright: any number that is not an approved *textual rendering* of a typed
evidence value, any direct order (`اشترِ الآن`, `بيع فورًا`, `ادخل بكل السيولة`,
`ضاعف مركزك`), any certainty claim, any markup.

### Numeric validation — exact, field-aware, no tolerance

There is **no absolute and no relative epsilon anywhere**. A 0.05 epsilon would accept
`1.89` for a stock evidenced at `1.84` — a 2.7% error on a low-priced EGX security. Instead
every typed evidence value is expanded into the finite set of textual forms that render
*that exact value* ([core/ai_narrative_numbers.py](../core/ai_narrative_numbers.py)):

* every 0–6 dp rendering that preserves the value exactly (trailing-zero family for free:
  `1.84` → `1.84`, `1.840`, `1.8400`);
* the one canonical display precision for the field's kind — price/indicator/percent/ratio
  2 dp, score/volume/count 0 dp — the same rendering the UI already prints;
* for a 0..1 unit ratio (scenario confidence, level strength), the ×100 form the UI shows
  as `55 / 100`;
* Arabic-Indic digits, `٪`, `٬`, `٫`, Western thousands separators, a leading `+` and the
  Unicode minus, all handled by normalizing both sides to one canonical key.

Consequences: `1.84` rejects `1.80`/`1.85`/`1.89`; `97.80` rejects `97.85`; `91.00` rejects
`91.05`; `15.37%` rejects `15.42%`; a negative MACD approves only its own sign; and nothing
can match a large volume or turnover by proximity because nothing is compared numerically
at all. A percent sign is gated separately — a number may be written as a percentage only
if it came from a percentage-typed field, a 0–100 score, or a source string where it was
itself followed by `%`. Numbers inside auditable evidence strings (session dates, level
bases, machine reasons) stay traceable via the same tokenizer.

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

## Regeneration

`regenerate_narrative()` (and the page's «إعادة توليد الشرح بالذكاء الاصطناعي» button)
rebuilds Layer 2 only. It reuses the same `AnalysisResult` object — so the evidence hash is
unchanged — and calls no research router, no EODHD, no Rubix, no data provider, no
indicator/key-level/scenario builder and no universe loader. That isolation is asserted by
monkeypatching every one of those entry points to raise.

## Tests

* `tests/test_ai_narrative_provider_v2.py` — the guarded path end to end (mocked provider).
* `tests/test_ai_narrative_numbers.py` — numeric exactness, including the low-price
  boundary cases (1.84 vs 1.85/1.89, 97.80 vs 97.85, 91.00 vs 91.05, 15.37% vs 15.42%).
* `tests/test_ai_narrative_openai_adapter.py` — the Responses adapter with
  `urllib.request.urlopen` mocked: endpoint, strict schema, no tools, refusal handling,
  status-only error mapping, and the key never appearing in output or logs.

No paid API call is made by the automated suite, and no socket is opened.
