# Analysis Views — Freshness Integration Audit

Where Stock Details, Watchlist and AI Analysis obtain a decision, and which of
those paths bypassed the shared freshness contracts.

**Research only.** No indicator, score, threshold or decision rule changed. No
collector process touched, no production scan run.

---

## 1. The two shared contracts

| Contract | Module | Answers |
|---|---|---|
| Per-symbol daily candle | `core/daily_data_guard.py` | may this symbol produce a *current decision*? |
| Rubix quote + overlay | `core/rubix_quote_freshness.py` | is this quote live, and what may it do? |

`core/scanner.py` applies both. Everything below is a path that reaches a user
**without** necessarily going through the scanner.

## 2. Stock Details — `dashboard/stock_details.py`

| Step | Where | Notes |
|---|---|---|
| daily history load | **none** | the page does not load history |
| indicators | **none** | not computed here |
| `decision_service.evaluate` | **none** | not called here |
| decision source | a `stock` **row dict** passed in | produced by an earlier scan |
| BUY/WATCH/AVOID render | `stock["Signal"]`, lines 41, 106, 203–211 | rendered directly from the row |
| target memory | `st.session_state["_stock_target_observations"]` | keyed by symbol only |

**Finding.** Stock Details is a *renderer*, so it inherits the scanner's gate
for rows produced after that gate existed. Its exposure is **temporal**: a row
computed while 2026-07-30 was current stays in session state and keeps
rendering as a current BUY after the expected session advances to 2026-08-03.
Nothing in the page re-checks the candle date against the current expectation.

## 3. Watchlist — `dashboard/watchlist.py`

| Step | Where | Notes |
|---|---|---|
| decision source | `scan_symbols(symbols)` line 37 | the real scanner, so the daily gate applies |
| cache | `st.session_state["watchlist_results"]` line 39 | same temporal exposure as Stock Details |
| badges | `frame["Signal"] == "BUY"/"WATCH"/"AVOID"` lines 56–58 | counted from whatever rows exist |

**Finding.** Because the scanner now *excludes* stale symbols before the
decision engine, a stale Watchlist symbol produces **no row at all** — so it
silently disappears from the user's own list rather than being shown as "data
update required". The gate is safe but the presentation is wrong: the brief
requires the symbol to stay, with its decision withheld.

## 4. AI Analysis — `dashboard/ai_stock_analysis.py`

| Step | Where | Notes |
|---|---|---|
| engine entry | `_default_runner` → `core.ai_stock_analysis_service.analyze_symbol` line 128 | external model/API work happens beyond here |
| invocation | `run_analysis(symbol, runner=None)` line 136 | called on the user's Analyze action |
| cached bundle | `st.session_state[STATE_BUNDLE]` / `STATE_SYMBOL` / `STATE_CARD` | discarded only when the **symbol** changes (`_discard_stale_analysis`) |

**Findings.**

1. **No freshness gate before the engine.** `run_analysis` calls the runner
   directly, so a symbol whose latest candle is 2026-07-30 consumes an external
   request and returns a current-looking advisory.
2. **Cache identity is symbol-only.** `_discard_stale_analysis` compares the
   selected symbol against `STATE_SYMBOL`. An analysis produced from 2026-07-30
   data is therefore restored unchanged under a 2026-08-03 heading once the
   expected session advances — the symbol did not change, so nothing is
   discarded.

## 5. Rubix display

`dashboard/stock_details.py:404` reads `metadata.get("live_quote_freshness")`,
the legacy string. The typed taxonomy from
`core/rubix_quote_freshness.py` was applied in the scanner and Dashboard but not
in these three views, so a previous-session quote could still surface here
under the old generic wording.

## 6. Summary of exposures

| Path | Daily gate | Rubix taxonomy | Cache identity |
|---|---|---|---|
| Dashboard scan table | ✅ scanner | ✅ | n/a |
| Stock Details | ⚠️ inherited only; stale after session advances | ❌ legacy string | ❌ symbol-only |
| Watchlist | ✅ scanner, but stale symbols vanish | ❌ | ❌ symbol-only |
| AI Analysis | ❌ **none** | ❌ | ❌ symbol-only |

The AI path is the most exposed: it is the only one that both skips the gate
entirely and spends an external request doing so.

## 7. What replaces this

One shared adapter, `services/analysis_freshness_service.py`, composes the two
existing contracts into an `AnalysisFreshnessContext` carrying the daily
verdict, the Rubix verdict, the overlay permission, both price sources, whether
current analysis is allowed, and a `cache_identity` fingerprint that changes
whenever any of those change.

No page reimplements a freshness rule; each asks the adapter and renders the
answer. The cache identity is what fixes the temporal exposure: a decision or
AI result computed for an older candle can no longer be restored as current,
because its identity no longer matches.
