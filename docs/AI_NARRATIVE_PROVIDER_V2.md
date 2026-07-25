# AI Narrative Provider V2 — AI narrative with deterministic facts

The deterministic evidence engine (Layer 1) remains the **only** source of every market
number. This layer adds an optional external language model as a **narrative layer only**:
the model writes qualitative Arabic prose and *cites* facts by id — it never writes a
number. Every figure the reader sees is inserted by the application from a typed evidence
field, with the application's own label, unit, rounding and ordering.

```
AnalysisResult
  → fact registry (typed facts + section permissions)   core/ai_narrative_facts.py
  → number-free qualitative payload + strict schema     core/ai_narrative_prompt.py
  → external provider adapter                           core/ai_narrative_openai.py
  → {qualitative_text_ar, fact_refs} per section
  → schema + no-digits + fact-binding + wording checks   core/ai_narrative_validator.py
  → deterministic composition (prose + rendered fact lines)
  → accepted AI narrative
                        …any failure at any step → Deterministic Fallback
```

Entry point: `core.ai_narrative_provider.build_narrative(result, ...)`. It never raises.

## Providers

| Provider | Status | Cost | Notes |
| --- | --- | --- | --- |
| `ollama` | **operational default** | free | Local model over loopback. No API key. No cloud. |
| `openai` | available, **disabled** | paid | Kept working; selected only if explicitly configured. |
| *(none)* | default | — | Deterministic Fallback, byte-identical to V1. |

### Ollama — free, local, offline

`POST http://127.0.0.1:11434/api/chat` with `stream: false`, the per-analysis strict
schema in `format`, `tools: []`, and `think: false` (Qwen reasoning off). Only
`message.content` is read — a `thinking` field is never parsed, returned, logged or
stored. Ollama **Cloud is never contacted**: the endpoint must resolve to loopback, and a
remote URL is refused unless `AI_NARRATIVE_OLLAMA_ALLOW_REMOTE=true`. Temperature,
`keep_alive` and the model all come from the environment.

**Install the model yourself — nothing is downloaded automatically:**

```bash
ollama pull qwen3:4b
```

Optional smoke test, and an optional larger model for quality comparison:

```bash
ollama run qwen3:4b
```

```bash
ollama pull qwen3:8b
```

**Health check** — `GET /api/tags`, read-only and short-timeout, reporting only
`reachable`, `model_installed`, the model name, the endpoint and a sanitized error token.
It runs only when a local provider is configured, never raises, and never blocks the
deterministic analysis. The page shows one of: **Local AI Ready** · **Local AI Model
Missing** · **Local AI Unavailable**, alongside the narrative-source badge.

A validated local answer is labelled **Local AI** everywhere — badge, technical details
and the PNG footer (`Narrative  Local AI  ·  qwen3:4b`). Anything else stays
**Deterministic Fallback**.

## Why fact binding

An exact numeric allow-list stops *invented* values but is global across the evidence, so
it cannot stop **semantic misattribution**: 1.90 is a genuine risk/reward, so "الهدف 1.90"
would pass a value-only check while being flatly wrong. The same hole allowed RSI as a
target, a stop as a positive target, volume as turnover, or remaining-room as the daily
change. The fix is structural — the model is not allowed to emit digits at all.

## Provider output shape

Each of the eight sections is an object, not a string:

```json
"positive_scenario_ar": {
  "qualitative_text_ar": "السيناريو الإيجابي يرتبط بتجاوز مستوى التفعيل.",
  "fact_refs": ["scenario.primary.trigger", "scenario.primary.target",
                "scenario.primary.risk_reward"]
}
```

The strict JSON Schema is built **per analysis**: `additionalProperties: false`, both
fields required in every section, and each section's `fact_refs` is an `enum` of exactly
the fact ids that section may cite for this result. No tools, no web search, no file
search, no conversation persistence.

## Fact registry

[core/ai_narrative_facts.py](../core/ai_narrative_facts.py) builds one registry per
analysis from **typed contract fields only** — nothing is parsed out of prose, a level
`basis` string, or a machine reason. Each fact carries `fact_id`, source path, raw typed
value, field kind, deterministic formatted value, Arabic label, and the sections allowed
to cite it. A fact whose value is missing is absent from the registry, so it cannot be
cited; volume-derived facts (`price.volume`, `price.turnover`, `indicator.volume_ratio`,
`indicator.average_volume_20`) are withheld entirely when `volume_safe` is false.

Ids include `price.close`, `price.change_percent`, `indicator.sma_20`, `indicator.ema_20`,
`indicator.rsi_14`, `indicator.macd`, `indicator.macd_histogram`, `indicator.atr_14`,
`indicator.volume_ratio`, `level.support_1`, `level.resistance_1`, `level.breakout`,
`level.invalidation`, `scenario.primary.trigger`, `.entry_low`, `.entry_high`, `.target`,
`.stop`, `.risk_reward`, `.remaining_room_percent`, `confidence.overall`, plus the
non-numeric states `classification.trend`, `classification.momentum`, `recommendation`,
`data.status`, `data.volume_safe`.

## Section permissions

| Section | May cite |
| --- | --- |
| executive_summary | close, change amount/percent, trend, momentum, recommendation, overall confidence |
| technical_read | typed indicator fields, trend, momentum, volume safety (volume facts only when safe) |
| positive_scenario | trigger, entry range, target, remaining room, risk/reward, scenario confidence, resistance, breakout |
| negative_scenario | stop, invalidation, supports |
| confirmation_conditions | trigger, breakout, volume ratio (only when `volume_safe`), momentum, volume safety |
| invalidation_conditions | stop, invalidation, first support |
| risk_notes | ATR, risk/reward, scenario confidence, overall confidence, volume safety |
| data_limitations | data status, freshness, provider, latest session, sessions used, volume safety |

A reference outside its section's list rejects the whole response.

## Deterministic rendering

After validation the application composes each section as the model's prose followed by
`label: value` lines, in **registry order** (never the model's), duplicates collapsed:

```
السيناريو الإيجابي يرتبط بتجاوز مستوى التفعيل.
نقطة التفعيل: 93.50 جنيه
الهدف المحسوب: 97.80 جنيه
العائد إلى المخاطرة: 1.90
```

The AI owns the words. The application owns the numeric formatting, currency labels, field
labels, ordering and rounding — `format_value()` is the only place a market number becomes
text.

## Configuration (environment only)

| Variable | Default | Meaning |
| --- | --- | --- |
| `AI_NARRATIVE_ENABLED` | `false` | Master switch. False ⇒ behaviour identical to V1. |
| `AI_NARRATIVE_PROVIDER` | `none` | `none`/`deterministic` ⇒ fallback mode; `ollama` ⇒ local; `openai` ⇒ paid. |
| `AI_NARRATIVE_MODEL` | *(empty)* | Model id passed to the adapter (`qwen3:4b` for Ollama). |
| `AI_NARRATIVE_TIMEOUT_SECONDS` | `20` | Per-request timeout (clamped 1–120); use `60` for a local model. |
| `AI_NARRATIVE_MAX_RETRIES` | `1` | Retries for **transient** failures only (clamped 0–3). |
| `AI_NARRATIVE_CACHE_ENABLED` | `true` | Cache accepted narratives in-process. |
| `AI_NARRATIVE_OLLAMA_URL` | `http://127.0.0.1:11434` | Local Ollama server. Loopback only. |
| `AI_NARRATIVE_OLLAMA_TEMPERATURE` | `0.2` | Sampling temperature for the local model. |
| `AI_NARRATIVE_OLLAMA_KEEP_ALIVE` | `5m` | How long Ollama keeps the model resident. |
| `AI_NARRATIVE_OLLAMA_DISABLE_THINKING` | `true` | Send `think: false` (no Qwen reasoning trace). |
| `AI_NARRATIVE_OLLAMA_ALLOW_REMOTE` | `false` | Must be true to permit a non-loopback URL. |
| `AI_NARRATIVE_BASE_URL` | OpenAI v1 | Any OpenAI-compatible Responses endpoint (paid path). |
| `OPENAI_API_KEY` | — | Secret, standard name. Only used by the `openai` provider. |
| `AI_NARRATIVE_API_KEY` | — | Optional documented alias, used only when `OPENAI_API_KEY` is unset. |

Modes: `disabled` · `external_ai` · `deterministic_fallback`. The model is never
hard-coded — it comes from `AI_NARRATIVE_MODEL`; the adapter's built-in default applies
only when that is empty. Example values live in [.env.example](.env.example) with every
secret left blank.

**Secrets are never** written to source, `config/settings.json`, history records, reports,
logs, the UI, or the exported card. Failure reasons are built from exception *types* and
rule ids, never from provider text.

## What the model may see — no market values at all

`build_qualitative_payload` sends a strict allow-list projection containing **states, not
values**: symbol, company name, market phase, recommendation, trend/momentum,
scenario state, data status/freshness/history/volume-safety flags, number-stripped
condition kinds and machine reasons, and the list of citable facts (id, number-free label,
allowed sections). No price, no indicator value, no level, no timestamp — the model cannot
leak a figure it was never given, and indicator periods are stripped from the labels it
sees so it cannot copy a digit from `المتوسط المتحرك البسيط 20` either.

Nothing else exists in the payload — no keys, no paths, no portfolio, no other symbol, no
universe, no raw logs. Evidence strings are sanitized (controls, bidi marks, code fences
and chat-role markers removed, length-capped) and framed as **untrusted data** the model
may describe but never obey.

## What the model must return

A single JSON object with exactly the eight section keys, each an object with exactly
`qualitative_text_ar` (concise Arabic, **no digits**) and `fact_refs` (approved ids).

This is enforced at the transport level too: the request uses OpenAI's **Responses API**
(`POST /v1/responses`) with strict Structured Outputs — `text.format.type = "json_schema"`,
`strict: true`, `additionalProperties: false`, and all eight sections in `required` — so
the shape is constrained by the API, not merely requested in prose. The request carries no
tools (`"tools": []` — no function calling, no web search, no file search), no
`previous_response_id`, and `"store": false`, so nothing persists and no unrelated context
enters. A Structured-Outputs **refusal** arrives as a `refusal` content item and is turned
into a typed error, never mistaken for narrative.

Rejected outright, discarding the whole response: **any digit or percent sign in the
model's prose** (Western, Arabic-Indic or Extended Arabic-Indic — status
`RAW_NUMBER_IN_PROSE`), any unknown fact id, any fact cited from a section that may not
use it (`FACT_REFERENCE_INVALID`), any direct order (`اشترِ الآن`, `بيع فورًا`,
`ادخل بكل السيولة`, `ضاعف مركزك`), any certainty claim, and any markup.

### Numeric validation — exact, field-aware, no tolerance (defence in depth)

The composed section — model prose **plus** the application's rendered fact lines — is
re-checked against the allow-list below, so even a rendering bug cannot put an untraceable
number on screen.

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

* `tests/test_ai_narrative_ollama.py` — the local provider with `urlopen` mocked:
  loopback-only endpoint policy, no API key, no tools, `stream:false` + `format` schema,
  reasoning trace never read, unreachable server / missing model / timeout → fallback,
  health states, cache behaviour, honest `Local AI` labelling.
* `tests/test_ai_narrative_fact_binding.py` — semantic binding: risk/reward cannot be
  written as a price, target cannot be cited from the technical read, RSI cannot be a
  target, stop cannot headline the positive scenario, volume ≠ turnover, remaining room ≠
  daily change, digits in any script rejected, renderer inserts the exact typed values in
  registry order, volume facts withheld when unsafe, PNG carries only rendered numbers.
* `tests/test_ai_narrative_provider_v2.py` — the guarded path end to end (mocked provider).
* `tests/test_ai_narrative_numbers.py` — numeric exactness, including the low-price
  boundary cases (1.84 vs 1.85/1.89, 97.80 vs 97.85, 91.00 vs 91.05, 15.37% vs 15.42%).
* `tests/test_ai_narrative_openai_adapter.py` — the Responses adapter with
  `urllib.request.urlopen` mocked: endpoint, strict schema, no tools, refusal handling,
  status-only error mapping, and the key never appearing in output or logs.

No paid API call is made by the automated suite, and no socket is opened.
