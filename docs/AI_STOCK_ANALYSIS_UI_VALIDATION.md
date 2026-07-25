# AI Stock Analysis — UI & Analysis Card Validation

**Branch** `claude/ai-stock-analysis-ui` · **Base** `6fb53cd56c9c514f6b997cd8bf0d79b1b2b50e4b`
**Worktree** `D:\EGX_AI_Trader_AI_UI_Claude`

This document records what the UI layer does, how each requirement was verified, and what
is deliberately left for Core integration.

---

## 1. Scope and layering

The UI is Layer 3 (presentation) and Layer 4 (exported card) of
`core/ai_stock_analysis_contract.py`. It renders already-computed typed evidence and
already-written narrative. It performs **no** calculation, **no** provider access, **no**
indicator computation and **no** universe scan.

| File | Role |
|---|---|
| `dashboard/ai_stock_analysis.py` | The Streamlit page (`show_ai_stock_analysis`) |
| `dashboard/ai_stock_analysis_components.py` | Evidence → display mapping, labels, card composition |
| `core/analysis_card_generator.py` | Arabic text engine, font discovery, PNG card renderer |
| `tests/test_ai_stock_analysis_ui.py` | Page/mapping contract tests (53) |
| `tests/test_analysis_card_generator.py` | Card + Arabic engine tests (32) |

Files **not** touched: `app.py`, every `core/ai_*` module, `core/ai_stock_analysis_contract.py`,
`docs/AI_STOCK_ANALYSIS_CONTRACT.md`, `tests/fixtures/ai_stock_analysis_evidence.json`,
`core/data_provider.py`, `core/research_router.py`, `requirements.txt`, `settings.json`,
strategy files.

---

## 2. Requirement → verification map

### Gating and isolation

| Requirement | How it is met | Test |
|---|---|---|
| No analysis on initial load | The runner is only invoked inside `if pressed:` | `test_no_analysis_runs_before_the_button_is_pressed` |
| One symbol per request | `normalize_symbol` rejects every collection type | `test_analysis_requests_exactly_one_symbol`, `test_a_collection_of_symbols_is_rejected` |
| No full-universe scan | AST scan of the page + components for scanner/universe identifiers | `test_no_full_universe_scan_is_reachable_from_the_page` |
| No provider call from the UI | AST scan for provider/router/HTTP identifiers | `test_no_provider_call_from_the_ui` |
| No network I/O | Sockets are poisoned, then an analysis is run | `test_fixture_analysis_opens_no_socket` |
| No Yahoo call or comparison | AST scan across page, components and card | `test_no_yahoo_call_or_comparison_anywhere_in_the_ui` |
| Corrected fixture loads | Fixture is rebuilt into typed contract dataclasses | `test_corrected_fixture_loads_into_typed_contract_objects`, `test_fixture_matches_the_published_contract_version` |

The AST-based scans deliberately ignore comments and docstrings, so prose *about* not
scanning the universe cannot satisfy or trip the check — only real identifiers count.

### Price summary

All twelve typed `PriceSummary` fields render (Last/Close, change amount, change percent,
open, high, low, previous close, volume, turnover, bid, ask, spread).

* A missing value is always `—`; `test_missing_price_values_render_as_an_em_dash_never_zero`
  strips the whole `PriceSummary` and asserts every rendered cell is an em dash.
* A genuine `0.0` is **not** collapsed into an em dash —
  `test_a_genuine_zero_is_not_confused_with_a_missing_value`.
* The four data modes (Live Quote / Last Completed Session / Cache Mode / Live Data
  Unavailable) are distinct — `test_the_four_data_modes_are_distinguishable`.

### Market phase and auction separation

`resolve_market_phase` (Core) is used unchanged. Boundaries verified against a real EGX
trading day:

| Cairo time | Phase |
|---|---|
| 09:59:59 | `PRE_SESSION` |
| 10:00:00 – 14:14:59 | `CONTINUOUS` |
| 14:15:00 – 14:24:59 | `CLOSING_AUCTION` |
| **14:25:00 and later** | **`CLOSED`** |

Tests: `test_market_phase_at_1425_is_closed`, `test_continuous_and_auction_boundaries`.

Auction separation is enforced in one place — `include_live_in_session_range(phase)`, true
only for `CONTINUOUS`. During the auction the page renames the live block to *بيانات مزاد
الإغلاق / Closing-auction snapshot*, prints an explicit separation notice, keeps the live
print off the chart, and `build_card_chart` sets `last=None` so an auction print is never
drawn inside a continuous-session range
(`test_auction_data_is_kept_out_of_the_continuous_session_range`).

### Technical overview

* SMA and EMA are separate groups with separate rows and distinct values —
  `test_sma_and_ema_render_in_separate_rows` asserts the value lists differ.
* MACD, MACD Signal and MACD Histogram each render from their own typed field —
  `test_macd_fields_render_explicitly`.
* When `volume_safe` is false the entire Volume & Liquidity group is withheld (Volume
  Ratio, OBV and Average Volume 20 are absent, not zeroed) and the supplied data-quality
  warning is shown — `test_volume_ratio_and_obv_are_suppressed_when_volume_is_unsafe`.
* No number is parsed out of narrative text or a recommendation reason; every value comes
  from a dedicated field.

### Key levels

Support 1/2, Resistance 1/2, Breakout and Invalidation are **selected and ordered**, never
computed. `selected_levels()` is the single selection point shared by the page and the card,
so the two can never disagree (`test_the_card_and_the_page_agree_on_which_levels_were_selected`).
Breakout/Invalidation prefer a dedicated `KeyLevel`, falling back to the primary scenario's
typed `trigger`/`stop` with the basis recorded as `scenario trigger` / `scenario stop`.

### Scenarios

Every displayed figure is a typed `ScenarioResult` field. Only these Arabic recommendation
labels exist anywhere in the UI:

> انتظار · مراقبة · قريب من التفعيل · جاهز بشروط · تجنب حاليًا · البيانات غير كافية

`test_only_the_allowed_arabic_recommendation_labels_exist` pins the set exactly;
`test_no_unconditional_buy_instruction_is_ever_rendered` and
`test_the_card_never_shows_an_unconditional_buy` assert no unconditional BUY wording exists
on the page or the card.

### Narrative and confidence

Narrative source is reported honestly as **AI Narrative**, **Deterministic Fallback**
(model equals the Core `FALLBACK_MODEL`) or **AI Unavailable** (no model / empty summary).
The UI never rewrites narrative numbers. Confidence is rendered exactly as supplied —
`test_confidence_overall_is_never_recomputed` feeds an `overall` that contradicts its own
component weights and asserts the contradictory value is what gets displayed. Canonical
components the engine did not supply render as `—`, never as 0.

### Provenance

`data_domain`, `provider`, `live_provider`, `yahoo_network_used`, `yahoo_seed_present`,
latest completed session, freshness and research version all render as supplied. Yahoo is
never shown as the current provider or as a comparison source; a frozen seed appears only
in the technical-details expander as **Frozen historical bootstrap seed**
(`test_yahoo_is_never_presented_as_a_provider`). A true `yahoo_network_used` is surfaced as
an **error**, not hidden (`test_a_yahoo_network_read_is_surfaced_as_an_error`).

### Performance

* Nothing runs before the button; the runner is called once per press.
* Results live in `st.session_state`, so reruns re-render without re-analysing.
* The card renders only when the button is pressed **and** the cache key
  (`symbol|evidence_version|evidence_hash|size|company`) changed —
  `test_card_is_not_regenerated_for_unchanged_evidence`.
* Analysis history is read only after the user ticks *Show history* (lazy).
* Empty states are compact single panels, not full-height voids.

---

## 3. The exported Arabic PNG card

`core/analysis_card_generator.py` renders an original 1080×1350 card (`POST`, required) and
an optional 1080×1920 card (`STORY`). It contains the EGX AI Trader identity mark (an
original rounded tile with three ascending bars — no third-party logo or layout was copied),
symbol and company name, date/time, market status, price and daily change, O/H/L/C, a
compact session-range chart with the supplied levels plotted on it, support/resistance,
trend and momentum, the selected scenario with trigger/target/stop, confidence, a concise
Arabic narrative, the data provider, the data timestamp, and the three mandatory badges:
**Decision Support Only**, **Research / Paper Mode**, **Production Disabled**.

Layout is measure-then-draw: block heights are computed first, the safety footer is pinned
to the bottom, and surplus copy is shed in priority order (secondary table rows, then
narrative lines) so a long narrative can never overrun the badges
(`test_a_very_long_narrative_never_overruns_the_card`).

### Arabic rendering without a new dependency

Pillow in this environment is built **without Raqm/HarfBuzz** (`PIL.features.check("raqm")`
is `False`), so `ImageDraw.text` alone would emit disconnected, left-to-right Arabic. Since
`requirements.txt` is off-limits, `arabic_reshaper`/`python-bidi` could not be added, so the
module carries its own small text engine:

* **`shape_arabic`** derives the full presentation-form table from `unicodedata` itself —
  every character in U+FB50–U+FEFF decomposes to its base letter with an
  `<isolated>`/`<final>`/`<initial>`/`<medial>` tag — so there is no hand-written table.
  Joining types (D/R/C/T/U) are derived from which forms each letter has. Mandatory
  lam-alef ligatures are handled; transparent marks never break a join.
* **`bidi_reorder`** implements Unicode rule L2 over coarse resolved levels, keeping Latin
  words and numbers left-to-right inside a right-to-left line. It is a pragmatic subset of
  the full bidirectional algorithm, documented as such — adequate for short card copy, not
  a general-purpose bidi library.

Verified by `test_shaping_picks_contextual_forms`,
`test_shaping_produces_the_lam_alef_ligature`,
`test_right_joining_letters_never_connect_to_the_following_letter`,
`test_transparent_marks_do_not_break_a_join`,
`test_rtl_reordering_reverses_arabic_but_keeps_numbers_left_to_right` and
`test_signed_percentages_stay_intact_next_to_arabic`.

### Font discovery

Fonts are discovered on the system font path; **no font file is added or committed**
(`test_no_font_file_is_bundled_with_the_repository`). A candidate is accepted only if it has
a real glyph for **every** assigned Arabic presentation form. Measuring the glyph mask size
is not sufficient — a missing glyph still renders as the font's visible `.notdef` box — so
each form's bitmap is compared against the `.notdef` bitmap probed through an unassigned
private-use code point.

This check matters in practice: **Dubai** (`DUBAI-REGULAR.TTF`) is missing 52 presentation
forms and drew rows of empty boxes in the first render, so it is not a candidate. On this
machine the resolved family is **Segoe UI** (`segoeui.ttf` / `segoeuib.ttf`); Tahoma, Arial
and Noto Naskh Arabic are the fallbacks.

---

## 4. How to preview the page

`app.py` is intentionally untouched, so the page is not registered in navigation yet. To
view it, create a scratch entry point outside the repository (or a temporary file you do not
commit):

```python
import streamlit as st
from dashboard.ai_stock_analysis import show_ai_stock_analysis
from dashboard.ui import sidebar_brand

st.set_page_config(page_title="AI Stock Analysis", page_icon="🤖", layout="wide")
sidebar_brand()
show_ai_stock_analysis()
```

Then run:

```bash
streamlit run <that_file>.py
```

Screenshots in the delivery were captured this way against fixture data only.

---

## 5. Test results

```
tests/test_ai_stock_analysis_ui.py ......... 53 passed
tests/test_analysis_card_generator.py ...... 32 passed
```

Full suite: **741 passed, 3 failed, 7 skipped**.

The 3 failures are pre-existing on the untouched base commit `6fb53cd` and are unrelated to
this work (verified by stashing every change and re-running them):

* `test_adaptive_selector.py::test_frozen_classic_and_breakout_manifest_is_unchanged`
* `test_breakout_swing.py::test_frozen_engine_files_match_release_candidate`
* `test_market_data_providers.py::test_dashboard_route_falls_back_and_backtest_stays_on_yahoo`
  (a local Yahoo cache entry is absent in this environment)

---

## 6. Known limitations / follow-ups for Core integration

1. **The page runs on fixture data.** `dashboard.ai_stock_analysis.run_analysis` takes a
   `runner` and defaults to `fixture_analysis`, which rebuilds typed objects from
   `tests/fixtures/ai_stock_analysis_evidence.json`. Because `UiAnalysisBundle` mirrors
   `core.ai_stock_analysis_service.AnalysisResponse`, integration is a one-line swap to
   `analyze_symbol`. No provider wiring was done, per the stop condition.

2. **`trend` and `momentum` are not in the contract.** They are required page fields but
   `IndicatorSummary` carries no such field. Rather than omit them or invent numbers, the UI
   renders an ordinal *label* produced purely by comparing already-supplied values (close vs
   SMA 20/50/200; MACD-histogram sign and RSI-14 band), and always prints the comparison
   basis next to the label so a reader can audit it. No number is produced, and both fall
   back to `—` when inputs are missing. **Recommendation:** add typed `trend` and `momentum`
   fields to `IndicatorSummary` in Core and delete `trend_reading`/`momentum_reading`.

3. **Key-level timeframe and touch count are not in the contract.** `KeyLevel` has no
   `timeframe` or `touches`, so both columns render `—` rather than a guess. Level
   *strength* maps to the supplied `KeyLevel.confidence`. **Recommendation:** add these two
   fields to `KeyLevel` if they are to be shown for real.

4. **The chart shows one completed session.** The contract supplies no OHLC series — only
   the latest session plus levels and moving averages — so the interactive chart is a single
   candle with the supplied levels and SMA/EMA overlaid. It will become a full series as
   soon as Core exposes one; the UI must never load history itself.

5. **The confidence component list is aspirational.** `ConfidenceBreakdown.components` is an
   open tuple; the fixture supplies `trend`, `liquidity` and `momentum`. The page shows the
   full canonical list (trend, momentum, volume, liquidity, freshness, spread, history,
   scenario quality, target room) with `—` for anything the engine did not supply, plus any
   extra component under a non-canonical name.

6. **Bidi is a documented subset.** `bidi_reorder` handles the mixed Arabic/Latin/number copy
   this card produces. It has no explicit directional formatting codes, no paragraph
   splitting and only bracket mirroring. If card copy ever grows to arbitrary user text,
   replace it with a full UBA implementation.

7. **The page is not in navigation.** Registering it requires editing `app.py`, which is
   outside this worktree's ownership.
