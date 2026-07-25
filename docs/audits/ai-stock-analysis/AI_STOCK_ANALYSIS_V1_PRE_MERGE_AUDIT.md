# AI Stock Analysis V1 — Final Pre-Merge Audit

Date: 2026-07-25
Branch: `codex/ai-stock-analysis-integration`
Merge status: **STOPPED BEFORE MAIN**

## Verdict

The integration is ready for review after the corrective commit. Trading, provider,
portfolio, risk, replay, forward-testing, and experiment-tracking behavior were not
changed by this audit.

## 1. Append-only history

`data/ai_analysis/history.jsonl` was absent before the integration work, is not tracked
in any Git revision, and is absent after this audit. Therefore its restored state is:

- State: **ABSENT**
- SHA-256: **N/A — no file exists**
- Byte-for-byte comparison: **identical absence**
- Git history for the path: **none**

The earlier `+0 -3` observation referred to three **untracked interactive preview
records**, not committed production records:

1. `COMI` — completed preview analysis used to validate the normal page state.
2. `ACRO` — blocked preview analysis with `HISTORY_INSUFFICIENT`, used to validate the
   safe insufficient-history state.
3. `COMI` — a repeated completed preview used for the final page/card screenshot.

They were removed because they were generated review data written during interactive
validation after the baseline, while the pre-integration production path did not exist.
No pre-existing production record was deleted, rewritten, or reordered. Because the
file was untracked, Git has no blob containing those temporary JSON lines; the records
above are identified by the executed preview cases rather than a Git object.

Corrective controls:

- `data/ai_analysis/` is ignored as runtime production data.
- The integration regression test passes an explicit temporary
  `AnalysisHistoryStore(tmp_path / "history.jsonl")`.
- The test snapshots the production path before and after two analyses and requires
  exact byte equality (or identical absence).
- The temporary history is required to contain two distinct appended lines.

## 2. `app.py` semantic diff

The complete diff from the pre-integration base `2bbea1c` contains exactly two retained
lines:

1. Import `show_ai_stock_analysis`.
2. Insert one `st.Page` entry named `AI Stock Analysis` with icon `🤖` at the start of
   the existing `RESEARCH & SYSTEM` group.

No existing page, page label, route callable, sidebar group, relative order, or session
state behavior was changed. There is no formatting churn or navigation restructuring.

## 3. Generated artifacts

The following tracked files are intentional **review evidence**, not runtime data:

- `reports/ai_stock_analysis_v1/01_initial_page.png`
- `reports/ai_stock_analysis_v1/02_completed_analysis.png`
- `reports/ai_stock_analysis_v1/03_scenario_section.png`
- `reports/ai_stock_analysis_v1/04_blocked_history_insufficient.png`
- `reports/ai_stock_analysis_v1/05_market_closed_state.png`
- `reports/ai_stock_analysis_v1/COMI_ai_analysis_1080x1350.png`

Normal card generation returns PNG bytes for the configured download/output flow; it
does not write into production history. Tests and this audit write generated PNGs only
to temporary locations. Ignored preview history is not part of the committed review
artifacts.

## 4. Narrative honesty

Operational narrative source: **Deterministic Fallback**.

- The page visibly reports the narrative source.
- Exported cards now visibly print `Narrative  Deterministic Fallback`.
- The page and card do not claim ChatGPT, external-AI, or model generation when no
  approved external provider was called.
- The title remains `AI Stock Analysis` because the contract supports an optional AI
  narrative layer.

A regression test records the text drawn on the PNG and verifies the deterministic
fallback label is present and prohibited external-provider claims are absent.

## 5. Operational provider contract

The integration tests reconfirm:

- EODHD / `CURRENT_RESEARCH_V2` supplies current daily/history evidence.
- Rubix read-only SQLite supplies the live quote and current-session intraday evidence.
- Rubix auction points remain separate from continuous-session points.
- Yahoo is neither an operational provider nor a comparison source.
- One request analyzes exactly one requested symbol.
- No full-universe scan is performed.

The provider-call regression test requires this exact sequence for `COMI`:

1. daily history: `COMI`
2. live quote: `COMI`
3. intraday series: `COMI`

No second symbol may reach a provider.

## 6. Validation

- AI Stock Analysis feature suite: **145 passed**
- Full repository suite: **756 passed, 5 skipped, 0 failed**
- Streamlit headless smoke: **PASS (`STREAMLIT_SMOKE_OK`)**
- Temporary PNG generation: **PASS**
  - dimensions: `1080 × 1350`
  - output: Windows temporary directory
  - narrative label visually verified
- `git diff --check`: **PASS**
- Production history before/after: **absent / absent**

## Corrective files

- `.gitignore`
- `core/analysis_card_generator.py`
- `tests/test_ai_stock_analysis_integration_v1.py`
- `tests/test_analysis_card_generator.py`
- `AI_STOCK_ANALYSIS_V1_PRE_MERGE_AUDIT.md`

The corrective commit hash is reported in the final handoff because a commit cannot
contain its own final Git hash.
