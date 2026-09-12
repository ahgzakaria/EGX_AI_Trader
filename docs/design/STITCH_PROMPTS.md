# Google Stitch — prompt pack for redesigning EGX AI Trader

**كيف تستعملها:**

1. الصق **البرومبت رقم 0** الأول لوحده في محادثة جديدة في Stitch. ده اللي بيعرّفه المنتج والقيود والنظام المطلوب. استنى لحد ما يطلّع نظام التصميم (ألوان، خطوط، مسافات، مكونات).
2. بعد كده خد **كل شاشة لوحدها**: الصق برومبت الشاشة + الـ screenshot بتاعها في نفس الرسالة. شاشة واحدة في المرة، متجمّعش.
3. لو Stitch خرج عن القيود (طلّع تصميم موبايل، أو تاب بار فوق، أو أنيميشن)، الصق **الجزء الأخير** (Correction prompt) وكمّل.
4. لما تخلص، ابعتلي اللي طلع منه وأنا أنفّذه.

الترتيب المقترح: الشِل (12) الأول لأنه بيظهر في كل صفحة، بعدين المحفظة (1) لأنها أهم صفحة، بعدين لوحة التداول (2) لأنها الأكبر والأصعب. الباقي بأي ترتيب.

---

## 0 · Master context prompt (paste this first, alone)

```
You are designing a private desktop trading terminal. Read every constraint
below before you draw anything — most of them will contradict your defaults.

PRODUCT
"EGX AI Trader" — a single-user research terminal for the Egyptian Exchange
(EGX). It is not a consumer fintech app and not a broker. It never places an
order and never touches money. It produces evidence that one human trader acts
on, by hand, in another application.

THE USER
One experienced Egyptian trader. Arabic is his first language; he reads English
financial and technical terms natively. He opens this on a Windows desktop at
1920px wide, every evening after the EGX session closes at 14:30 Cairo, and
reads it for ten to thirty minutes. He is not a new user and never will be. He
needs no onboarding, no tour, no encouragement, no celebration, and no
explanation of what a stop loss is. He needs density, precision, and the
ability to find the one row that matters among two hundred.

HARD TECHNICAL CONSTRAINT — this changes what you may design
The app is built in Streamlit 1.58 (Python), rendered in a desktop browser at a
maximum content width of 1640px. I can only implement what maps onto
Streamlit's primitives plus custom HTML and CSS.

I CAN build:
- Vertical page flow; horizontal columns with fixed ratios
- Custom HTML/CSS blocks: cards, badges, chips, rails, bars, strips, headers
- Metric tiles; data tables with per-column formatting, colour, in-cell bars
- Tabs, accordions, popovers, modal dialogs
- Buttons, text inputs, selects, toggles, sliders, date pickers, file upload
- Charts (Plotly / Altair) inside a frame I control
- A left sidebar containing vertical page navigation, which I can restyle but
  whose structure I do not own
- All static CSS: colour, spacing, radius, border, shadow, typography, hover

I CANNOT build — do not design these:
- Drag and drop; resizable or collapsible panels; reorderable columns
- Sticky or frozen table headers or columns; custom scrollbars inside tables
- A top horizontal navigation bar (navigation is in the left sidebar, always)
- Floating, fixed or overlaying elements; toasts that hover over content
- Animated transitions, parallax, scroll-triggered effects, micro-interactions
- Rich HTML tooltips on hover (plain-text tooltips only)
- Masonry layouts, or anything requiring real-time JavaScript

So design with layout, hierarchy, typography, colour, spacing and density.
Not with motion, and not with novel interaction.

DESIGN LANGUAGE
Dark trading terminal. These are the current tokens — keep the family, but
rebuild them into a real system:
  bg #0b1220 · bg-2 #0e1729 · surface #131c30 · surface-2 #17223b
  border #223049 · text #e6edf7 · muted #8ea1bd
  green #34d399 · amber #fbbf24 · red #f87171 · blue #60a5fa · gray #94a3b8
Type: IBM Plex Sans for prose. IBM Plex Mono with tabular numerals for every
number, everywhere, without exception — digits in a column must align.

The feeling is an instrument, not a product. Quiet, dense, high signal to noise.
No gradients. No glassmorphism. No hero sections. No illustrations. No emoji as
decoration. No pill-shaped everything. No delightful anything. Restraint is the
aesthetic. Think a Bloomberg terminal redrawn by someone with taste, not a
crypto dashboard.

BILINGUAL AND RTL — treat this as the hardest problem, not an afterthought
Every screen mixes Arabic and English inside the same block. The established
pattern is an Arabic headline with an English subtitle underneath it. Tickers
(COMI), all numbers, and status words (BUY / WATCH / AVOID / STALE / PASS) stay
Latin and left to right. Arabic prose is right to left. The page is LTR overall
with RTL islands inside it.

I need explicit, reusable rules for:
- how an Arabic title and its English caption stack and align
- a row of badges sitting beside Arabic text
- punctuation, parentheses and units at an RTL/LTR boundary
- Arabic column headers above left-to-right numeric columns
- when a label needs both languages and when one is enough
Give me rules I can apply to a screen you have never seen, not fixes for one.

SEVEN RULES THE DESIGN MUST BE ABLE TO EXPRESS
These are the product's identity. The previous version hid all of them, and
that is why it is being redesigned. A design that cannot show these has failed,
however good it looks.

1. Unknown is not zero. A cost that was never measured must look different from
   a cost of zero. I need a distinct visual role for "not measured".
2. Stale is not current. Data has an age. Every screen must say which trading
   session it is showing and whether that is the session it should be showing.
3. Provenance belongs to the number. Any important figure can name where it
   came from. I need a lightweight way to attach a source to a value without
   turning the screen into footnotes.
4. Advisory is not an instruction. Some panels are decision support only and
   must be visually incapable of being mistaken for an order to act.
5. Three states, never two. Every check is PASS, FAIL, or UNAVAILABLE. A check
   that could not run must never look like one that passed.
6. Failures are silent by nature, so the design must make them loud. A health
   strip sits above the navigation on every single screen.
7. Money already at risk outranks new ideas. The portfolio screen comes first
   and must feel heavier than the screens that suggest new trades.

DELIVERABLE 1 — the design system, before any individual screen
- Colour roles: surfaces, borders, three text tiers, and semantic states
  including a distinct "unknown / unmeasured" role that is not grey-as-disabled
- A type scale — page title, section title, caption, body, table text, numeric
  display — with Arabic and Latin sizes tuned so they sit on a shared baseline
- A 4px-based spacing scale, radii, border weights, and when elevation is used
- A component library, each with every state it needs:
  page header · section header · metric tile · status strip · badge (six tones,
  including unknown) · banner (info / warning / error / success) · card ·
  signal card showing stop, entry and target on one proportional rail ·
  data table (header, row, zebra, numeric alignment, in-cell bar, state chip) ·
  tabs · accordion · empty state · form block · sidebar nav item ·
  sidebar health panel · chart frame
- A density specification: table row height, the maximum number of columns
  before a table must be split, and the rule for choosing a table over cards

DELIVERABLE 2 — one screen at a time, after I send you its screenshot
For each: the redesigned layout at 1640px, the components it uses from the
system, and an explicit list of what you removed and why.

Confirm you have understood the constraints, then give me Deliverable 1.
Do not design any screen until I send you a screenshot.
```

---

## 12 · The shell: sidebar, navigation, health strip *(do this one first)*

```
Screen: the application shell, visible on every page.

It contains, top to bottom, in a left sidebar:
- the product mark "EGX AI Trader"
- a health panel: one line answering "did this evening's automated run actually
  happen?", with a coloured dot, a status word, and two or three lines of
  monospace detail underneath (timestamps, what ran, what did not)
- vertical navigation, grouped under four bilingual headings:
  "المحفظة · PORTFOLIO" (1 item), "سوينج · SWING" (7 items),
  "تحليل · AI ANALYSIS" (1 item), "النظام · SYSTEM" (2 items)
- each nav item has an icon and a label

The health panel sits ABOVE the navigation on purpose. Every real failure this
system has had was a silence — a data collector that died at 08:00 and stayed
dead for sixteen hours, a job that ran too early and wrote nothing. None of
them announced themselves, and all of them were three clicks deep inside a page
nobody opens on a good day. This panel is the fix. It must be impossible to
ignore, while also not shouting on a normal day.

The current page is marked with a 2px rail on its leading edge rather than a
filled block, because a leading-edge rail is the same language the alerts and
the signal cards use elsewhere — one position always means "state".

Design:
1. The sidebar at rest, on a healthy day
2. The health panel in each state: ran and fine, ran with warnings, did not run
   at all, unknown
3. The nav item in all states: default, hover, current page, and a group heading
4. How a bilingual group heading is set (Arabic and Latin on one line)
5. The relationship between sidebar width, content width, and the 1640px cap

Tell me whether the health panel should be expandable, given that I cannot
build an overlay and expanding it would push the navigation down.
```

---

## 1 · My Portfolio · المحفظة *(the most important screen)*

```
Screen: "محفظتي" — My Portfolio. The only screen with real money on it. Every
other screen answers "what should I buy?". This one answers "what happens to
the money I already committed?", which is where the result is actually decided.
It is first in the navigation for that reason and should feel like the heaviest
screen in the product.

Content, in current order:
1. Page header: Arabic title, English subtitle "Your real positions, and how to
   leave each one well", a PORTFOLIO badge
2. Two action buttons: refresh prices, rebuild exit plans
3. "ماذا أفعل الآن" / "Ordered by urgency, priced after costs" — a stack of
   action cards, the reason the page exists. Each card carries: ticker, a row of
   badges (action, urgency, price basis live-versus-stale, and sometimes
   "قاعدة غير مُقاسة بعد" = the rule behind this has not been validated yet),
   one line of Arabic reasoning, and a muted line with price, quantity and net
   EGP after costs. When nothing needs doing, a success message replaces them.
   Two special cases live here: positions withheld because no trustworthy price
   exists (no recommendation is ever built on a stale price), and positions with
   no exit plan at all, each stating its own reason.
4. "الملخص" / "What the account is worth, after the cost of leaving it"
5. "المراكز" / "Average, breakeven, and the plan for each" — the positions
   table: average cost, breakeven after fees, current price, stop, targets, and
   the money still at risk between here and the stop
6. "التركيز القطاعي" / "Four names in one sector is one big position"
7. "تسجيل وتعديل" — a six-tab data-entry block: invoice, file upload, trades,
   cash, corporate actions, records
8. Three accordions at the bottom: provenance, recommendation log, plan history

Design problems I want solved:
- The action cards are the point of the page but look like every other card.
  Urgency must be readable at a glance without relying on colour alone.
- Three kinds of negative state must not look alike: "I advise you to sell" (an
  action), "I cannot advise, the price is stale" (withheld), and "this holding
  has no plan at all" (a gap). Today all three are generic banners.
- The badge saying a recommendation's rule has never been validated is the most
  important badge in the product and the easiest to miss.
- Data entry for six kinds of record is stuffed into tabs at the bottom of the
  most-read screen. Tell me whether it belongs on this screen at all.
- Money at risk, unrealised profit, and net-after-cost are three different
  quantities that currently look identical.

Design the page at 1640px, plus the action card in every one of its states.
```

---

## 2 · Daily Dashboard · لوحة التداول اليومي *(the largest screen)*

```
Screen: "لوحة التداول اليومي" — the daily swing dashboard. The main scanning
screen: it runs a rule over about 230 EGX stocks and reports a decision for each.

Content:
1. Page header, a SWING badge, and a note that this screen is for multi-day
   trading only
2. A scan control area: a primary "run scan" button, and while a scan runs, a
   progress panel (completed of total, succeeded, skipped, failed, elapsed).
   A scan takes about five minutes and runs in the background, so the user
   navigates away and comes back — the screen must reattach to a running scan
   and never offer to start a second one.
3. A three-item status strip, always visible: which source the history came
   from, the latest completed candle date, and whether the live quote overlay is
   fresh, partial, stale or absent. This strip is the honest provenance line and
   must never be reduced.
4. Four context metrics: symbols analysed of total, market state, last completed
   session, live price source
5. A warning when some symbols could not be analysed at all
6. "أهم الفرص القابلة للمتابعة" / "Top actionable opportunities" — up to ten rows
7. "جدول السوق المختصر" / "Compact market table" — all ~209 rows, with a ticker
   search box, a decision filter (ALL / BUY / WATCH / AVOID) and a result count.
   Columns: ticker, company name, decision, market state, price, buy band (a
   range, not one price), stop loss, target 1, target 2, risk-reward,
   confidence, operational status.
8. An "البحث المتقدم" accordion holding signal and regime distribution charts,
   the full research table with developer metrics, a provenance expander, and a
   list of symbols excluded for stale data with the reason for each

Design problems I want solved:
- About half the rows are AVOID and are read once, never again. They carry the
  same visual weight as the handful of BUY rows.
- The entry is a band (low to high), not a price. Collapsing it to one number
  would invent precision the strategy never had — so the table needs a way to
  show a range in one cell without doubling the column count.
- Twelve columns is already at the edge of readable, and the research table
  behind the accordion has sixty. Give me a rule for what belongs in the primary
  table and what belongs behind a disclosure.
- The status strip, the four metrics and the warning banner are three stacked
  bars of information before the user reaches any content.
- A scan that is running, one that finished, and one that failed validation are
  three different page states and currently look similar.

Design: the page empty (never scanned), running, and loaded. Plus the table row
in each decision state.
```

---

## 3 · Swing Breakout

```
Screen: "Swing Breakout" — one rule's current signals, shown as cards rather
than a table.

Content:
- Page header with a one-line statement of what the rule is
- A stack of signal cards. Each card draws stop, entry and target as three marks
  on a single horizontal rail at their true proportional distances, with a
  shaded band starting at the entry whose width is the round-trip trading cost.
  Under the rail: the move in percent, the cost in percent, and the net. The
  card is tinted by whether the net is positive.
- Critically: when the trading cost was never measured for that name, the band
  is not drawn and the cost is labelled UNKNOWN. It is never drawn as zero.
  Treating an unmeasured cost as zero once made thirteen losing signals look
  profitable. This distinction is the single most important thing on the screen.
- A card with fewer than two real price levels has no scale, so it is drawn
  without a rail rather than with a fake one.
- An accordion listing symbols that could not be read
- An accordion "على أي أساس · What this rests on" — the evidence behind the rule

Design problems I want solved:
- The rail is the best idea in the current product and the worst executed.
  Redesign it: three marks and a cost band on one axis, readable at a glance,
  and honest that the axis is not linear in money.
- "Cost unknown" reads as a missing value rather than as a warning.
- Cards do not scale: twenty of them is a scroll with no structure.
- A card with no rail and a card with a rail look like different components.

Design the signal card in five states: net positive, net negative, cost unknown,
insufficient levels (no rail), and a dense list of twelve of them.
```

---

## 4 · Breakout Watch · مراقبة الاختراق

```
Screen: "مراقبة الاختراق · Breakout Watch" — what is approaching a trigger, as
opposed to what has already fired. Same rule and same thresholds as the
Confirmed Breakout screen; this one is the session before.

Content:
- Page header
- "عند الإغلاق يوم الزناد · At the close on the trigger day" — what the rule
  will require tomorrow, stated as conditions
- "قمع البوابات · Gate funnel" — a funnel showing how the universe narrows
  through each gate in order, ending in a handful of candidates. Typical
  numbers: hundreds in, a few at each stage, then 3 / 5 / 8 out.
- "المرشحون · Candidates" — the names that survived, each with how far it is
  from its trigger
- An empty state for a week with no candidates

Design problems I want solved:
- The funnel is the explanatory heart of the screen and is currently a list of
  numbers. It should make the narrowing visible without becoming an infographic.
- "Approaching" is a distance, and distance is the only thing a trader wants to
  rank by. It is currently just a column of numbers.
- This screen and Confirmed Breakout are deliberately adjacent and must read as
  a pair — the set-up and the confirmation — without being identical.

Design the funnel component and the candidate row. The funnel must work with six
stages and with three.
```

---

## 5 · Confirmed Breakout

```
Screen: "Confirmed Breakout" — signals from a validated rule that fired at
today's close. The most evidence-backed screen in the product.

Content:
- Page header
- An accordion for symbols that could not be read
- Three sections of results
- "كيف تُنفَّذ · How a signal is executed" — an accordion explaining exactly how
  a signal becomes an order, by hand
- "على أي أساس · What this rests on" — an accordion holding the measured
  evidence behind the rule
- An empty state, which is the normal state on most days

Non-negotiable: this rule is DECISION SUPPORT ONLY. It is not an instruction to
trade. The design must make it structurally impossible to mistake a signal card
here for an order ticket — and must do that without a disclaimer banner the user
will stop seeing after a week.

Design problems I want solved:
- The empty state is the most common state and currently feels like a failure.
  A day with no signals is a normal, correct outcome and should read that way.
- "How it is executed" and "what it rests on" are the two things that make this
  screen trustworthy, and both are collapsed, at the bottom.
- Three result sections look like three tables with different titles.

Design: the loaded state, the empty state, and your solution for making
"decision support, not an order" permanently legible.
```

---

## 6 · Sector Liquidity · السيولة القطاعية

```
Screen: sector money flow, measured from real exchange turnover rather than
estimated. It answers "where did the market's money actually go today, and is
that changing?"

Content, as sections:
1. Page header
2. The current session's sector shares
3. "Strength" — a measurement per sector
4. "Rotation history" — how shares moved across recent sessions
5. A forecast section
6. A "المصدر · Data provenance" accordion
7. Several distinct empty states, each with its own cause: no complete session
   stored, no session passed the coverage check, not enough history for
   rotation, not enough history for a forecast

Important: a sector share is one symbol's turnover divided by the market's, so
the numbers are only meaningful when the session is complete. A session that
failed its coverage check is excluded entirely rather than shown partial.

Design problems I want solved:
- Five sections of the same shape with different titles.
- Share, strength, rotation and forecast are four different kinds of quantity
  presented identically. A forecast in particular must not look like a
  measurement.
- The screen has four separate empty states, and they are the most likely thing
  the user will see early on. They all currently look like errors.
- There are about ten sectors — few enough that a chart is optional. Tell me
  whether this screen should be charts, a table, or both, and why.

Design the page, plus a comparison of two treatments for sector share.
```

---

## 7 · Watchlist · قائمة المتابعة

```
Screen: the user's own hand-picked list of symbols, run through the same live
scanner as the main dashboard. It is deliberately separate from Breakout Watch:
that list is produced by a rule and discarded weekly; this one is the user's and
persists.

Content:
- A "Data Update Required" section when the data is behind
- Page header
- A metric showing how many symbols are tracked, and a primary "Scan Watchlist"
  button
- Before a scan: the plain list of symbols with company names
- After a scan: "Current Opportunities" — results in the same shape as the
  dashboard's table
- Empty states: no symbols in the list, no results returned

Design problems I want solved:
- The screen has two completely different lives — list management before a scan,
  results after — and one layout serving both.
- Adding and removing symbols is not obviously possible from this screen.
- It duplicates the dashboard's table with no visual signal that this is a
  subset the user chose himself.

Design both states, and tell me where symbol management belongs.
```

---

## 8 · Stock Details · تفاصيل السهم

```
Screen: everything known about one symbol, reached from any table. The deepest
and densest screen in the product.

It is organised as four tabs:
1. Overview — "Snapshot & Price", "Price Levels"
2. Decision — "Decision Summary" (the strategy decides; AI is advisory and can
   never overrule it), "Key Reasons", "Frozen Decision Values" (the original
   values kept exactly as recorded), "Gate Results" (the exact decision path,
   gate by gate, each PASS / FAIL / UNAVAILABLE), "Confidence Breakdown"
   (contribution per component), "Strategy Module Scores"
3. Sizing — "Risk Inputs", explicitly a calculator that does not place an order
4. Chart — price, volume, "Latest Indicators"

Above everything, when the record is historical rather than live, a banner:
"HISTORICAL SNAPSHOT — not a current decision".

Design problems I want solved:
- Thirteen sections across four tabs, all the same shape.
- "Gate Results" is the most valuable panel in the product — it shows precisely
  why a decision came out as it did, gate by gate — and it is buried in the
  middle of a tab. Three states per gate, never two: a gate that could not run
  must never look like one that passed.
- Frozen values and live values sit side by side and are easy to confuse. Frozen
  evidence is never relabelled as live, and the design must carry that.
- The sizing tab computes position sizes and must not look like a trade ticket.
- The historical banner is the only thing separating "this is what I think now"
  from "this is what I thought in March", and it is a plain banner.

Design: the Decision tab in full, the Gate Results component in all three
states, and your treatment for frozen-versus-live values.
```

---

## 9 · AI Analysis · التحليل

```
Screen: per-symbol narrative research, one symbol at a time. The only screen
whose main output is prose rather than numbers, and the most Arabic-heavy.

Content, in order:
1. "اختيار السهم" / "Symbol Selection — one symbol per analysis"
2. "ملخص السعر" / "Price Summary — typed evidence fields"
3. A live pricing block, which switches to "بيانات مزاد الإغلاق" (closing
   auction data) once the session has closed
4. "الرسم البياني" with two tabs: daily candles, and live intraday
5. "النظرة الفنية" / "Technical Overview"
6. "المستويات الرئيسية" / "Key Levels — as supplied, never recalculated"
7. "السيناريوهات" / "Scenario Cards" — several possible paths
8. "تحليل جودة التصحيح" / "Pullback Health Analysis"
9. The narrative itself — long-form Arabic prose — plus a technical-details
   accordion
10. "تفصيل الثقة" / "Confidence Breakdown", versioned by method
11. "المخاطر وجودة البيانات" / "Risk & Data-Quality Warnings"
12. "بطاقة التحليل" — a shareable analysis card
13. "سجل التحليلات" / "Analysis History — loaded on demand"

Every field here is supplied by an evidence engine and is never recalculated for
display. "As supplied, never recalculated" is a promise the layout has to keep.

Design problems I want solved:
- Thirteen sections in one unbroken column. The user reads perhaps four.
- This is where Arabic prose is longest, and Arabic line length, line height and
  measure are currently inherited from a layout designed for Latin numbers. I
  want a real Arabic reading treatment: measure, leading, and where English
  technical terms sit inside an Arabic paragraph.
- Scenario cards are parallel possibilities and should be comparable side by
  side, not stacked.
- The analysis card at the end is meant to be screenshotted and shared, so it
  has to survive outside the app, on its own, carrying its own provenance.

Design: the full page structure, the Arabic prose block with proper typography,
the scenario cards as a comparable set, and the shareable analysis card.
```

---

## 10 · System Health · صحة النظام

```
Screen: diagnostics. The screen nobody opens on a good day, which is exactly why
the health strip in the sidebar exists.

Content:
1. Page header
2. A summary of whether each automated job ran, and when
3. "التشخيصات الفنية" / "تفاصيل للمراجعة عند وجود مشكلة" — technical detail
4. An "Advanced Diagnostics" accordion
5. A "Provider Diagnostics" accordion — one row per data source with its state
6. "أدوات البحث القديمة" / "Legacy / Research Only" — retired pages reachable
   only from here, including a whole retired strategy, run history, run
   comparison, and a replay tool that re-runs a recorded session against the
   current engine and writes nothing back

Design problems I want solved:
- This is the diagnostic screen for a system whose characteristic failure is
  silence. It should be readable in ten seconds by someone who is worried; the
  current layout requires reading.
- "Everything is fine" and "one job did not run" must be distinguishable from
  across the room.
- Retired research tools share the screen with live diagnostics. They are kept
  deliberately, but they are not the same category of thing.

Design: the healthy state, the degraded state, and the provider diagnostics row.
```

---

## 11 · Settings & Backtest

```
Screen: configuration plus the backtesting laboratory. Two different jobs on one
screen, in four tabs: Strategy, Backtest, AI, Tools.

Content:
- Page header with a "RESEARCH CONTROL" badge
- An accordion "أي بوابة تقرر فعلًا؟ · Which gate actually decides" — it exists
  because a setting once read as ON while the check behind it could not run at
  all, for years. This accordion is the answer to "is this switch real?"
- Strategy tab: many numeric thresholds and toggles, grouped
- Backtest tab: run controls, then results — headline statistics, an equity
  curve chart, a drawdown chart, a "Professional Metrics" accordion, and a
  "Rejected Trades Breakdown" accordion
- AI tab: model controls
- Tools tab

Design problems I want solved:
- A long flat list of numeric inputs with no grouping and no sense of which ones
  matter. Some of these settings change what the strategy does; most do not.
- A switch that is ON but whose check cannot run is the defect that shaped this
  whole product. The settings UI must be able to show a setting's real state —
  on, off, or on-but-inert — not merely its stored value.
- Backtest results are a report living inside a settings tab.
- Equity curve and drawdown are the same x-axis drawn twice, unaligned.

Design: the Strategy tab with real grouping, a settings row that can express
"on but inert", and the Backtest results as a report.
```

---

## Correction prompt — paste this if Stitch drifts

```
Stop. You have broken one or more of the constraints. Re-read these and redo the
last screen:

- Desktop only, 1640px content width. No mobile, no tablet, no responsive
  breakpoints.
- Navigation is a left sidebar. Never a top bar, never tabs across the top of
  the app, never a bottom bar.
- No animation, no transitions, no hover-revealed panels, no overlays, no
  floating elements, no drag and drop, no sticky headers.
- No hero sections, no gradients, no illustrations, no decorative icons, no
  onboarding, no empty-state encouragement, no celebration of results.
- Every number is monospace with tabular figures.
- Arabic and English coexist in the same block. Do not translate the interface
  into one language.
- Three states, never two: PASS / FAIL / UNAVAILABLE. Unknown is never zero and
  never blank.
- Dark theme only.

Show me the corrected screen and list what you changed.
```
