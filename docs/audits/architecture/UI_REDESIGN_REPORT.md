# EGX AI Trader — UI Redesign (V1: Expected Range Scalper reference page)

**UI/UX only.** No trading strategy, signal, score, threshold, range, TP/SL, provider,
database schema, or paper/production flag was changed. Formatting affects display
strings exclusively — underlying numeric values are untouched. This is the first
reference page per the required implementation sequence; other pages are **not**
redesigned yet (they inherit only the shared dark theme).

## Files changed (all presentation)

| File | Change |
|---|---|
| `dashboard/formatting.py` | **New** — compact number/volume/turnover/percent/frequency/price formatters, em-dash for missing, status→short-label + tone maps, scenario labels, range-zone. |
| `dashboard/ui.py` | Rewritten — dark trading-terminal theme + reusable components (status bar, metric card, status badge, range-position bar, section/empty states). |
| `dashboard/expected_range_scalper.py` | Rewritten — the redesigned page (below). |
| `.streamlit/config.toml` | **New** — dark base theme for native widgets. |
| `.claude/launch.json` | **New** — local preview launcher (dev only). |
| `tests/test_ui_formatting.py` | **New** — 16 formatter/label/row-builder/no-mutation tests. |

## Before → after (Expected Range Scalper)

| Area | Before | After |
|---|---|---|
| Theme | Light "data-science report" | Dark trading-terminal (navy/charcoal, off-white text, semantic status colours) |
| Status | Long caption of raw flags + 8 large metric cards | **Compact colored status bar** (EGX phase · History · Rubix · Bridge · Paper · Production) + **4 compact cards** (Tradable / Ready / Waiting / Rejected) + a small secondary metrics row |
| Table | One wide raw table, `head(40)`, raw floats (`4.4711`, `928858`), far-right scenario/exec columns | **Tabbed** (جاهز الآن·Ready / أفضل المرشحين·Best[default] / انتظار·Waiting / مرفوض·Rejected / كل الأسهم·All), **Top-20 default** with Top 20/50/All, reordered columns (Symbol→Rank→Score→Volume→Turnover→ADR→2%→Range→Live→**RangePos progress bar**→Scenario→Entry→Target→Stop→Status), human numbers (`928.9K`, `56.33M EGP`, `4.47%`, `100%`) |
| Statuses | Raw strings (`READY_LOWER_RANGE_BOUNCE`, `DATA_STALE`) repeated per row | Short readable labels (`Ready: Lower Bounce`, `Data Stale`, `No Chase`), full string in the drawer/tooltip, coloured badges in cards |
| Warning | Full-width yellow banner repeated everywhere | **Compact alert** — genuine-lag badge with expected-vs-latest session + expandable affected-symbol list (not repeated per row) |
| Details | Inline tabs below the table | **Stock side drawer** (`st.dialog`) opened by row-click or a Details button — Quick Decision / Liquidity / Volatility / Expected Range (with position bar) / All Scenarios / Data Quality — without leaving the scanner |
| Ready Now | (none) | Focused **opportunity cards** with scenario-specific wording, Entry/Target/Stop/Ask/Spread/Room, and explicit "Decision Support Only · Paper Mode · Production Disabled" |
| Missing values | `None` in Entry/Target/Stop | em-dash `—` |
| Language | English only | Arabic labels with English trading terms retained (tickers/statuses/strategy IDs never translated) |

## Verified rendering (real data)

The app launches and the page renders correctly. The compact status bar shows
`EGX CLOSED · History 2026-07-22 · Rubix Connected · Bridge Rubix-derived · Paper
Active · Production Disabled`. Against a real 265-symbol scan the table renders
formatted values (`14.1M`, `525.7M`, `123.36M EGP`, `6.56%`, `100%`, `9.51 – 10.00`)
and shows `—` for missing Entry/Target/Stop — exactly as designed.

## Performance

- The heavy historical scan stays behind `@st.cache_data(ttl=300)` and the explicit
  "Run pre-session scan" gate — cosmetic reruns never re-scan.
- The table renders only the selected slice (Top 20 by default); the drawer loads
  detail for the **one** selected symbol, not all 265 at once.
- Formatting is O(rows) string mapping; no extra Rubix polling was added.

## Unresolved / limitations

- **Screenshot not captured:** the environment's browser pane cannot composite
  frames, so a pixel PNG could not be produced. Rendering was verified via the page's
  accessibility text (status bar, header, gated state) and by exercising the exact
  table/card data path against a real scan in Python. A visual screenshot needs the
  browser pane displayed on the user's side.
- Streamlit `st.dataframe` cannot render HTML badges inside cells, so the in-table
  Status column uses the short **text** label; coloured badges appear in the Ready-Now
  cards and the drawer. Sortable numeric columns (Rank, Score, RangePos) stay numeric;
  compact-formatted columns (volume/turnover) are display strings (lexical sort).
- Only the Expected Range Scalper page is redesigned; other pages inherit the dark
  theme but keep their current structure pending review (as instructed).

## Final safety confirmation

- No strategy / scoring / threshold / provider / schema / paper-or-production flag
  changed. ERS flags remain `paper_enabled=True`, `production_enabled=False`,
  `automatic_execution=False`, `broker_orders_enabled=False`, TP +2% / SL −2%.
- Formatting never alters underlying numeric values (asserted by
  `test_row_builder_does_not_mutate_universe`).
- **403 tests pass** (387 prior + 16 new UI tests); the Expected Range page loads
  successfully.

**Stopping here for review** — the other pages will be redesigned only after this
reference page is approved.

---

## V2 polish pass (Expected Range Scalper only)

Focused polish applied on top of V1 — still UI-only, no logic/score/threshold/flag
change; 414 tests pass.

1. **Card semantics clarified + >265 overlap resolved.** Cards now use unique-symbol
   counts with a nature tag each (symbol·historical / symbol·live): **Tradable (170) +
   Rejected/low-liquidity (95) = 265** exactly. Ready/Waiting are shown as *live
   sub-states of the tradable set* (no longer summed into the partition), with an
   explicit reconciliation caption: "of tradable: Ready X · Waiting Y · No-room/stale Z".
2. **DATA_STALE noise cut.** The two staleness types are separated: a per-row **⚠**
   prefix appears only for the **24 genuinely history-lagged** symbols, while the
   **201 market-wide stale-live-quote** symbols are disclosed **once** in the global
   panel — not repeated per row.
3. **Coloured status + score.** The table now colours the Status text by tone
   (green Ready / amber Waiting / red Data issue / gray No-Chase / blue info) via a
   pandas Styler, and colours Score by quality (≥80 green, ≥60 amber, else gray); the
   full technical status stays in the tooltip/drawer.
4. **Table readability.** Symbol and Status are **pinned** (frozen); Best Scenario
   widened; numeric columns set to small widths with shortened labels (Avg Vol,
   Turnover, ADR%, 2%, Range) to keep Entry/Target/Stop visible and avoid horizontal
   scroll on the Top-20 default.
5. **Bilingual consistency.** Cards use an Arabic primary label with a smaller English
   secondary line; tabs/sections use a single "Arabic · English" pattern.
6. **Header hierarchy.** Larger page title, clearer subtitle, smaller SCALPING V3 badge,
   more top padding.
7. **Filter UX.** Placeholder "Search symbol… · ابحث عن سهم"; primary filters (score,
   ADR, **max spread**, **data quality**, show-count) visible in the row; **min
   turnover / min volume** in a "More filters" popover with reset.

New V2 tests: card partition (Tradable+Rejected=universe), clean state partition, and
history-stale-vs-live-advisory distinction.

---

## V2.1 polish pass (Expected Range Scalper only)

Still UI-only, no logic/count/flag change; 416 tests pass.

1. **Clipped title fixed** — `block-container` top padding raised to 3.4rem and the
   Streamlit header made transparent, so the page title always clears the toolbar.
2. **Session-phase-aware live state** — a new auction-aware `_session_phase()` drives
   the Ready metric label: **Continuous → "جاهز الآن / Ready Now"** (green, actionable);
   **Auction → "مزاد — لا دخول جديد / Auction — No New Entries"**; **Closed/Pre-open →
   "جاهز عند آخر فحص / Ready at Last Scan"** (blue, *not* currently actionable). The
   Ready-Now view shows a matching banner and never presents old signals as live.
3. **No live-stale "warning" after close** — outside continuous trading the panel shows
   a neutral **"MARKET CLOSED — LAST SESSION SNAPSHOT"** chip; the red
   `LIVE_QUOTES_STALE` warning is reserved for an active continuous session only.
4. **History wording corrected** — the global cache **latest** and **expected** dates
   are disclosed separately (`History: latest 2026-07-22 · expected 2026-07-21`), and
   the dataset is **not** called stale when the global latest ≥ expected — it instead
   says "**24 symbols behind expected history**" (per-symbol, last range preserved).
5. **Count explanation moved** into a compact "How counts are calculated" expander
   instead of a long always-on caption.
6. **~15–20% tighter vertical rhythm** — smaller metric cards (padding/value size),
   hero and section margins reduced, so more table rows fit at 1600×900.
7. **Arabic-only tab labels** (جاهز الآن / أفضل المرشحين / انتظار / مرفوض / كل الأسهم)
   with the English mapping in a small secondary help caption above the tabs.

New V2.1 tests: phase-aware ready label, session-phase value.

**V2.1 hotfix:** the first V2.1 build raised `ValueError: truth value of a Series is
ambiguous` in `_stale_alert` (a `universe.get(col) or pd.Series(...)` fallback calls
`bool()` on a Series when the column exists). My pre-ship check only loaded the
*pre-scan* page, which never calls `_stale_alert`, so it slipped through. Fixed with
a column-safe `_max_date()` helper, and now guarded by two regression tests plus a
**full render-path smoke** (every post-scan render function exercised against a real
scan with Streamlit stubbed) — the check I should have run originally. 418 tests pass.

---

## Final pass: Expected Range polish + Scalping Dashboard redesign

UI-only; no strategy/score/threshold/scenario/flag change; **423 tests pass**; both
pages load live with no server errors.

### Part A — Expected Range final polish
1. **Badge repositioned** — the SCALPING V3 badge is vertically centred inside the
   header with safe right/top spacing (hero `align-items:center`, badge margin).
2. **Tab explanation line removed** — Arabic-only tab labels are self-explanatory; no
   permanent helper line replaces it.
3. **History status simplified** — separate badges: **"Global history through 22 Jul"**
   (green when up to date), and **"{N} symbols lagging"** / **"{M} no history"**
   separately; the whole dataset is never called stale when global latest ≥ expected;
   exact ISO dates remain in the details expander.
4. **Scenario / State / Data Quality split** — the single Status column became three:
   **Scenario** (Lower Bounce / Continuation / No Chase), **Scenario State** (Ready /
   Waiting / Consumed / —), **Data Quality** (Current / History Lag / Live Quote Stale
   / Wide Spread / No History). A data-quality issue no longer overwrites the scenario
   state; both are colour-coded, full codes stay in the drawer.
5. **Drawer validated** — a full render-path smoke opens the drawer for a real symbol
   (all 7 sections render) and handles a missing symbol/values (em dash); row→symbol
   mapping is taken from the filtered/limited view so it stays correct after filtering,
   Top-20/50/All changes and across tabs; opening the drawer reruns no scan.

### Part B — Scalping Dashboard (operational control-tower)
Rewrote `show_scalping_dashboard` (in `dashboard/scalping.py`) as an operational
overview of the active Expected Range system — the other scalping pages are untouched.
It **reuses** the cached `_run_scan`, `_build_rows`, `_session_phase`, the shared
components and the same reusable stock **drawer**.

- **Header** (compact, Arabic subtitle, SCALPING V3 badge) + **session status bar**
  (phase · Rubix · value-progression · Bridge · Paper · Production · last event).
- **Data & System Health** panel (Rubix, last event, Daily-Bridge finalization,
  pre-session snapshot, live monitor, outcome finalizer, scheduled tasks) with a note
  that a frozen Mubasher timestamp is not a fault while values progress.
- **Paper Forward Test** panel (complete/pilot/partial sessions, READY/matured/pending,
  target/stop-first, evidence status — never recommends production).
- **Session-aware primary cards** (Ready-now/last-scan, Waiting, Tradable Universe,
  Blocked) with unit tags (symbols · live/last-scan/historical) + a secondary metrics row.
- **Ready Opportunities** panel (scenario-specific; "Last Observed Opportunities" after
  close; empty state shows top blockers), **Watchlist** (closest-to-ready), **Top
  Candidates** overview table (row-click opens the drawer), and a **Blockers summary**
  aggregating unique-symbol reasons with counts and %.
- **Performance**: the cheap health + paper panels render immediately; the expensive
  scan is gated behind a Load/refresh button and reuses the shared cache — no extra
  Rubix polling, no duplicate scan, drawer details lazy-loaded.

Metric-count semantics: every card/table discloses its unit (unique symbols vs
scenarios vs paper signals); Ready/Waiting are live sub-states of the tradable set;
Blockers are a clean unique-symbol partition (verified: blockers + ready = universe).

**Screenshot status:** still not capturable here (the browser pane can't composite),
so both pages were verified via live page-text + a full render-path smoke against real
data + tests. No strategy, scoring, threshold, provider, schema, or paper/production
flag changed; no automatic execution enabled.

---

## Scalping Dashboard — session-state semantics fix

UI-only; **424 tests pass**; verified live + render-path smoke across every phase.

1. **Market-closed live quotes** — `_dash_classify(rows, phase)` is session-aware. A
   tradable symbol with a stale LIVE quote is an **active blocker only during
   continuous trading**; after close it is counted as **"Last-session quotes"** and
   **excluded from active data blockers** (original quote timestamps preserved in the
   drawer/status bar). Verified on real data: CONTINUOUS → `live_quote_stale=166,
   last_session_quotes=0`; CLOSED/AUCTION → `live_quote_stale=0, last_session_quotes=166`.
2. **Pre-session snapshot vs current history** — the health panel now shows **Current
   history health** derived live from the Rubix overlay/Daily Bridge (`CURRENT` when
   the latest completed session is available locally), **separately** from the latest
   pre-session **snapshot** which is disclosed as historical context: *"session
   2026-07-22 · status HISTORY_STALE · class PILOT_SESSION · created … (historical —
   current history health above is CURRENT)"*. An old pilot snapshot's status is never
   presented as a current system-health failure.
3. **Blocker panel title is phase-aware** — Continuous: *"Why no opportunities now?"*
   (active blockers); Auction: *"No new entries — closing auction"*; After close:
   *"Last Scan Classification"* (not described as currently actionable).
4. **Count reconciliation** — one unique-symbol partition (sums to the universe in
   every phase) with **Ready · Waiting · Rejected/low-liquidity · Active data blockers
   · Last-session quotes** shown separately; market-closed quote age is never combined
   with genuine active-session data failures.
5. **Visual validation** — both continuous-simulated and actual market-closed semantics
   exercised via the render-path smoke and confirmed on the live page text. Screenshot
   still not capturable in this environment (browser pane cannot composite frames).

Session-aware summary of behaviour:
- **Continuous (10:00–14:15):** live readiness + true `Live quote stale` blockers; card
  reads *Ready Now*.
- **Auction (14:15–14:25):** *No new entries — closing auction*; stale live quotes →
  last-session count.
- **Closed / pre-open:** *MARKET CLOSED — LAST SESSION SNAPSHOT*; ready card reads
  *Ready at Last Scan*; blockers become *Last Scan Classification*; stale live quotes →
  *Last-session quotes* (informational, not a failure); current history health stays
  `CURRENT` when the completed session is locally available.

---

## Scalping Dashboard V1.1 — table semantics + compactness

UI-only; **430 tests pass**; render-path smoke passes in both phases (incl. an
injected zero-price symbol) with no server errors.

1. **Market-closed table Data Quality** — `data_quality_label` is now session-aware:
   Continuous → Current / Live Quote Stale / Live Data Missing; Auction → Auction
   Snapshot; **Closed → Last Session Snapshot** (valid quote) or **Last Price
   Unavailable** (no valid quote). `Live Quote Stale` no longer appears merely because
   time passed after close. Original quote timestamps stay in the drawer.
2. **Zero-price protection** — new `is_valid_price()` / `fmt_live_price()`: a
   None/NaN/zero Last renders as an em dash (never `0.00`) and is marked *Last Price
   Unavailable*. Invalid-price symbols are excluded from the Watchlist's closest-to-ready
   ranking and moved to a compact **Data Unavailable** reference row (historical Trigger
   kept, price/room shown as em dash) — never classified Ready/Waiting off a fake zero.
3. **Compact empty opportunity state** — the large dashed panel became a ~90px
   horizontal card (icon + title + session-aware subtitle + top reasons + "See the
   Watchlist below"); no spinner after the scan completes.
4. **Concise System Health** — one compact line *"Latest snapshot: 22 Jul ·
   PILOT_SESSION · Historical status: HISTORY_STALE (not current — …)"*; the snapshot
   creation time, exact dates, full status codes, scheduled-task list and the frozen
   `market_timestamp`/value-progression explanation moved into a **System Details**
   expander (removed from the always-visible panel).
5. **Top-candidate rank clarity** — the Top Candidates table excludes Ready/Waiting
   symbols (shown in their own sections) while **preserving the real global Rank** (never
   renumbered) and adds the help line *"Ranks are global; Ready and Waiting symbols may
   be shown in the sections above."*
6. **Coloured table badges** — State and Data Quality cells are tone-coloured via a
   Styler (green Ready/Current, amber Waiting/Live-stale, red Last-Price-Unavailable,
   blue Last-Session-Snapshot, gray No-Chase); no raw technical strings.

New V1.1 tests: market-closed→Last Session Snapshot, active-session→Live Quote Stale,
zero-Last→em dash, invalid price excluded from closest-to-ready, global rank preserved
after filtering. No strategy/scenario/score/threshold/provider/db/flag/TP-SL/scheduled-
task change; no quote-age thresholds or live-session rejection logic touched.

---

## Scalping Dashboard micro-polish + Opportunities V1 (2026-07-23)

### Part A — Scalping Dashboard micro-polish (3 items)

1. **Score format.** Top Candidates (and Ready/Watchlist) scores no longer render as
   `90.000000`. New shared `fmt_score()` prints `90` / `88` / `87`, and only falls back
   to one decimal when genuinely fractional (e.g. `84.9`). The underlying score is
   unchanged — display string only.
2. **Data-quality label width.** The truncated `Last Session Snapshot` cell now shows the
   compact `Last Snapshot` (new `dq_compact()` map). The full label + original quote
   timestamp remain in the stock drawer's Data Quality section; a column tooltip points
   there. Informational (blue) tone unchanged.
3. **Data Unavailable wording.** The watchlist reference section no longer says
   "Last Observed" when the displayed price is an em dash. It now reads
   *"no valid last price available; shown for historical trigger reference only"* — never
   implying an observed price exists.

Files: `dashboard/formatting.py` (`fmt_score`, `dq_compact`), `dashboard/scalping.py`
(`_dash_ready_panel`, `_dash_watchlist_panel`, `_dash_top_candidates`).

### Part B — Opportunities page redesign (V1)

**New file:** `dashboard/opportunities.py` (`show_opportunities`). Nav rewired in
`app.py` (Opportunities → `show_opportunities`). The previous isolated-scanner page
function `show_live_opportunities` is **preserved** in `dashboard/scalping.py` (still
importable, simply out of navigation) — no logic was deleted.

**Page purpose & hierarchy.** A focused scenario decision-support workspace — *not* a
copy of the Dashboard or the Expected Range Scanner. It answers: what is actionable now,
what is closest, what is missing for confirmation, why an opportunity was invalidated,
and what was recorded in paper mode. Header + compact badges (Session Phase, Rubix, Paper,
Production Disabled, Last Refresh). Arabic-primary tabs:
جاهز الآن · قريب من التفعيل · تم إلغاؤه · محظور · كل التقييمات.

- **Ready** — genuinely READY activations only, as a focused table with scenario-specific
  labels (Lower Range Bounce / Dip and Reclaim / Trend Continuation / Breakout Retest /
  Gap-Up Continuation / Gap-Down Recovery), activation time, Last/Bid/Ask/Spread%, Entry,
  Target +2%, Stop −2%, remaining room, Range Position, Score, quote age, Data Quality and
  paper-signal status. Decision Support / Paper / Production badges. Never a generic BUY.
- **Near Ready** — waiting symbols ranked by *distance to trigger from the current valid
  price*. Zero/missing prices are excluded from ranking and moved to a separate
  **Data Unavailable** reference section; distance is never computed from an invalid Last,
  and a historical Trigger alone is not treated as near-ready evidence.
- **Invalidated** — durable state transitions (from the paper-state store) where a
  previously watching/ready scenario became invalid: previous state, invalidated-at,
  reason (range consumed / spread widened / quote became stale / session phase ended / …),
  last valid price, range position, activation cycle, paper UUID. The original signal is
  never rewritten.
- **Blocked** — unique-symbol aggregation (each symbol counted once) by reason with
  count + percentage + expandable affected-symbol list. Mirrors the dashboard classifier
  so counts reconcile.
- **All Evaluations** — searchable/filterable table (symbol, scenario, state, min score,
  max spread, data quality, valid-price-only, paper-recorded) with Top 20 / Top 50 / All.

**Reusable drawer.** Reuses the approved `_render_drawer` (Liquidity / Volatility /
Expected Ranges / All Scenarios / Data Quality) and prepends an **Opportunity Summary**
(current scenario, state, missing confirmation, activation time, Entry/Target/Stop,
remaining room, quote freshness, paper record status) and a **State Transition Timeline**
(only real transitions — unchanged evaluations are suppressed). Selecting any row opens
the correct symbol.

### Session-aware semantics
- **Continuous** — Ready Now / Near Ready / active blockers; a stale live quote is a
  genuine active blocker.
- **Auction** — "no NEW entries" notice; recorded opportunities shown for review only.
- **Closed** — first tab reframes to *آخر فرص مسجلة (Last Observed Opportunities)*, blockers
  become "Last Scan Classification", quote age alone is **not** an active fault, and the
  data-quality label uses **Last Snapshot** rather than Live Quote Stale.

### Paper-state presentation
Per-symbol paper status is read from the immutable signals CSV + matured outcomes:
Signal Recorded / Outcome Pending / Target First / Stop First / Neither / No Paper Signal,
plus the SignalUUID and activation cycle. Original signals and outcomes stay visually
separated; production is never recommended.

### Performance
The page reuses the **same cached scan** (`_run_scan`, shared `_ers_scan` gate) as the
Dashboard and Scanner — a tab click never reruns the historical universe scan. Durable
paper state (signals/outcomes CSV + transitions) is loaded through `@st.cache_data`
(120 s TTL). Selected-symbol details and the transition timeline are lazy-loaded only in
the drawer (never all histories at once). Evaluation tables are limited (Top 20/50/All).
No polling frequency or scheduled task changed.

### Tests
`tests/test_ui_formatting.py` grew from 35 → **50** tests: `fmt_score`, `dq_compact`,
scenario full labels, paper-outcome labels, invalidation labels, Ready/Waiting/Invalid
separation, unique-symbol blocker aggregation + session-aware quote age, zero-price
exclusion from Near-Ready distance, transition-suppression predicate, quote-age
formatting, activation-cycle/UUID helpers, filtered-row-opens-correct-symbol, and a
no-mutation guard on `_live_extras`. Full regression: **445 passed**. A headless
render-path smoke drove `show_opportunities` (all five tabs + drawer + timeline) against
the **real 265-symbol scan** in CONTINUOUS / AUCTION / CLOSED with no exceptions.

### Screenshot status
Same environment limitation as prior passes: the in-app browser pane cannot composite
frames, so `computer{screenshot}` times out. Verified live instead via page-text —
Opportunities renders the header, badges, all five Arabic tabs, the compact Ready empty
state (real reasons: Live quote stale 165 · Low liquidity 95 · History lag 2), the
Near-Ready "Data Unavailable (3)" reference split, the Invalidated honest empty state
(session 2026-07-23), and the Blocked panel — and the Scalping Dashboard still loads
cleanly with the polished score/label/wording.

### Safety
UI/UX only. No strategy, scenario rule, scoring, threshold, ranking, Entry/Target/Stop,
TP +2% / SL −2%, Rubix collector, Daily Bridge, provider, database schema, scheduled
task, or paper/production flag was changed; no signal or outcome record was modified; no
automatic execution was added. The Opportunities page removes the old isolated-scanner
page's manual "Open Paper Trade" control from navigation (that function is preserved in
`dashboard/scalping.py`), keeping this workspace strictly decision-support.
