# Portfolio Exit Assistant

**Added 2026-08-31.** The page at `/portfolio` ("محفظتي"), backed by the
`holdings/` package.

Every other workspace in this project answers *what should I buy?* This one
answers the question that decides whether any of that mattered: what to do with
what is already owned.

---

## 1. What it does

1. Records every buy and sell and derives the average price from them.
2. Prices each holding live, and states where the price came from.
3. Builds an exit plan — stop, partial target, final target — from levels the
   daily engine already defines.
4. Revises that plan as the market changes, and issues one recommendation per
   position with the reason and what it is worth after costs.
5. Logs every recommendation with its evidence, so the rules can be judged
   later on what they did.

It places no orders and never will. The output is a sentence and a number.

---

## 2. Decisions, and why they went that way

### Average cost, not FIFO
A partial sale realizes profit against the weighted average of everything held.
This matches an EGX broker statement and was the user's explicit choice. FIFO
would report a different realized figure for the same account on the same day.

### Entry fees enter the cost basis; slippage does not
The user records the price actually executed, so the slippage already happened;
charging a modelled figure on top would charge it twice. Commission and the flat
order fee never appear in a fill price, so they belong in the basis. Slippage and
spread are forward-looking execution risk, charged by the exit estimate instead.

### Breakeven is on screen beside the average
These are different numbers and the gap is a real loss. On a 1,000-share lot
bought at 10.00 with the fee schedule in `config/settings.json`, the average is
10.0219 and the price that returns that money after exit fees is 10.0623.
"I'm up 0.3%" at 10.05 is a loss, and no broker screen says so.

### A stock dividend or split changes the share count, never the basis
This is the failure that silently corrupts a hand-kept average: 100 shares at
10.00 after a 10% bonus is 110 at 9.091, and a reader still comparing against
10.00 believes they are losing when nothing was lost. Recorded as a factor —
shares held afterwards per share held before — because that is the form a holder
reads straight off the announcement.

### Transactions are stored; averages are derived
An average kept as a stored number drifts the moment a partial sale, a bonus
issue or a corrected entry touches it, and nothing in the file says which of the
two numbers is wrong. `data/portfolio.db` is the only database here no provider
can rebuild, so it is also backed up by `services/backup_manager.py`.

### Levels come from existing engines, never from new multiples
| Level | Definition | Source |
|---|---|---|
| Stop | `support − 0.30 × ATR` | `strategy/entry.py` |
| Partial target | 20-bar high excluding today | `strategy/support.py` |
| Final target | `resistance + 2 × ATR` | `strategy/entry.py` |
| Take 50% at the first target, then stop to breakeven, trail on EMA20, close after 20 sessions | measured policy | `config/settings.json:backtest` |

A held position is therefore stopped where a new signal in the same stock would
be stopped, and managed by the policy the strategy was backtested under.

### A stop never widens
Each revision may only move the stop up. A stop that retreats in front of a
falling price is the mechanism by which a small loss becomes the loss that
matters, and it always arrives with a reason that sounds good at the time.

### Absence is stated, not filled in
When a position has run above every level the daily engine can name, the final
target is left empty and the remainder is managed by the trailing stop. An empty
target with a trailing stop is a real plan; an invented round number is a guess
wearing a plan's clothes.

### Plans are versioned, never overwritten
"Why did it want 12.00 last week and 11.40 today?" has to stay answerable. A new
version is written only when a level actually changes.

---

## 3. The rules, in the order they fire

| # | Rule | Action | Measured? |
|---|---|---|---|
| 1 | `NO_PRICE` | WITHHELD | — |
| 2 | `STOP_BREACHED` | EXIT | yes |
| 3 | `TREND_BREAK` (price below the EMA the plan trails on) | EXIT | yes |
| 4 | `TARGET_FINAL_REACHED` | EXIT | yes |
| 5 | `TARGET_PARTIAL_REACHED` | TRIM 50% | yes |
| 6 | `TIME_STOP` (20 sessions, first target never reached) | EXIT | yes |
| 7 | `LIQUIDITY_LEAVING` | EXIT **if already in profit**, else RAISE_STOP | **no** |
| 8 | `EXPANSION` (new high on 2× volume) | RAISE_STOP | **no** |
| 9 | `ON_PLAN` | HOLD | — |

### The constraint on rules 7 and 8

Nothing on this project has validated a liquidity reading as an exit signal. So
those rules may bank a profit that already exists, and may tighten a stop. They
may **never** sell into a loss.

The reason is arithmetic. A round trip on EGX costs 0.4638% in fees plus the
spread, against an average intraday move of 0.078%. This project already retired
an entire workspace over that ratio — see the note in `app.py` and
`tests/test_app_navigation.py`. A rule that pays that toll on a hunch loses
money slowly while looking busy.

`LIQUIDITY_LEAVING` also requires **both** the stock and its sector to be below
their own norms. A quiet stock inside a busy sector is a stock nobody traded
today; money leaving the sector around a stock still being traded is not money
leaving the stock.

During an open session the sector reading comes from
`sector_flow.intraday.forecast_rest_of_day` — today's opening window, read from
MubasherTrade PRO's own minute store, blended with the previous completed
session. Measured on 20 complete sessions (2026-08-18 to 2026-09-15) it has the
lowest error (MAE 0.0167 against 0.0183 for the previous session alone and
0.0210 for the opening window alone) and the best rank correlation, but names
the top three sectors less often than the previous session alone (70% against
77%). Before 10:00, after 14:30, on a closed day, or when today's opening window
was not observed, it falls back to the completed-session sector strength. The stock's
own relative volume is always measured on completed daily bars, because today's
partial volume is not comparable to a full session's average until the session
ends.

### How they will be judged

`holdings/store.py` logs every actionable recommendation once per session with
its price, its plan version, and the evidence that produced it. After enough
sessions the unmeasured rules can be scored on what they actually did, the way
the 48 live ORB signals were. Until then the page labels them
"قاعدة غير مُقاسة بعد" on the card itself.

---

## 4. Price selection

* **Live** only when a quote reports itself `FRESH`. There has been no live
  quote since the Rubix feed was retired on 2026-09-10, so every price is the
  completed close below.
* Otherwise the **last completed session's close**, labelled as such, with the
  reason the live quote was refused.
* Neither available → **no price**, and the rules withhold. A stale price shown
  as current is how a plan gets executed against a market that has moved.

### The quote read, and the local copy that no longer exists

Both provider paths asked the 22-million-row `quotes` table for
`MAX(received_at)`, a column with no index. Measured on 2026-08-31:

| Path | Cost |
|---|---|
| `quote_overlay`, per symbol | 10–30s |
| `load_latest_quote_overlays` (batch, before the fix), 2 symbols | **1,432s** |
| indexed reads, 3 symbols | **0.70s** |

This page therefore carried its own reader, `holdings/quotes.py`. Later the same
day the provider itself was fixed the same way (commit `1b5a470`) — same
per-ticker indexed queries, same receipt-time definition, and it credits this
module for it. **The local copy was then deleted**: a second definition of "a
fresh quote" living in a page is exactly the kind of duplicate that drifts.
Measured on the real 21-position portfolio afterwards: provider batch **3.3s**,
local reader 0.5s, and **zero field differences** between them. The 2.8s gap is
the provider's whole-universe ticker-normalization check, which is worth its
cost and is not something a portfolio page should re-implement to avoid.

What the fix changed, in both places: the receipt time is taken from the newest
quote row rather than as an independent maximum. That can only err toward
calling a quote **stale** — a quote wrongly called stale withholds a
recommendation; the opposite acts on a price that no longer exists.

The rule that survives all of it, for any future query over that table: filter
by `ticker=?` and read only indexed columns, or take what you need off the
newest row. Never aggregate `received_at`.

### Daily data is loaded once per session, not once per visit

With 21 real positions, reloading every daily frame on every page visit cost
**57 seconds of spinner**, and Streamlit reruns the page on every navigation —
so leaving the page and coming back paid it again.

A plan is now built from candles once per completed session per symbol and
stored with everything a later visit needs: its levels, the completed close,
the relative volume, the 20-day-high flag, the holding age, and a fingerprint
of the position it describes. A visit reuses that plan when the calendar's
completed session still matches the one it was built for **and** the holding is
unchanged; otherwise it reloads. Measured on the same 21 positions:

| | Cost |
|---|---|
| First visit of the session (builds 21 plans) | 57.4s |
| Every visit after it | **0.05s**, 20 of 21 plans reused |
| Forced rebuild (the ♻ button) | 32.6s |

The reuse key is `built_for_session` — what the calendar expected when the plan
was built — and deliberately **not** the plan's own `session_date`, which is the
session its candles end on. Those differ whenever the provider has not published
today's candle yet, and keying on that would make every plan permanently
unreusable. The trade is stated on the page: if a missing candle is published
later in the same session, the stored plan keeps the older levels until the next
session or until ♻ is pressed.

The 21st position (GOUR) has no plan at all: 131 daily bars against the 250 the
indicators require. The page says exactly that beside it rather than leaving a
blank row.

---

## 5. Getting positions in

### Broker e-invoices (the daily path)

Thndr e-invoices are PDFs carrying real embedded text — fonts, ToUnicode maps,
text operators. `holdings/invoices.py` reads them with `pypdf`: no OCR, no
vision model, no network. The numbers are read, not recognised.

One page is one invoice. Quantity, fee total and grand total are taken from the
document; **the price is the stated total divided by the quantity**, not the
printed average, which the invoice rounds to two decimals (3,600 shares at a
printed 24.65 against a stated 88,735.25 differ by 4.75 EGP).

Three safeguards:

* **The arithmetic must reconcile.** A purchase costs `value + fees`; a sale
  returns `value − fees`. A parse that gets a digit wrong breaks that identity
  and the page is refused with the reason. A wrong number that still reconciles
  is essentially impossible.
* **Re-importing is a no-op.** The broker's execution numbers
  (`N000260123610|…`) are stored on the row under a partial unique index, so
  uploading the day's file twice cannot double a position.
* **Nothing above the security header is read.** The account holder's name sits
  in the letterhead and never enters the database.

Instruments that are not EGX listings — Thndr Savings arrives as
`thndrsavings` with no ISIN and no fees — are identified as money-market funds
and recorded as **cash movements**, not positions: selling one returns cash to
the trading balance, buying one takes cash out. Recorded as a stock, a cash
fund would appear as a holding with an exit plan attached to it.

ISINs are resolved to tickers through the `ISIN` column of `data/sectors.csv`
(222 of 228 rows carry one). An unknown ISIN waits for the user to pick the
symbol; it is never guessed.

### The first-time spreadsheet

For holdings that predate the invoices, `holdings/imports.py` reads a CSV or
xlsx in either of two shapes, told apart by their columns (Arabic headings
accepted):

* **transactions** — `Date, Symbol, Side, Quantity, Price, Fees, Note`. A blank
  fee is modelled from the schedule; a stated **zero** stays zero.
* **opening positions** — `Symbol, Quantity, AveragePrice, Date`. This is the
  answer to "I bought this over several trades and cannot separate the
  commission": the broker's stated average already contains it, so the row is
  recorded with **zero** additional fees. Charging the schedule on top would
  bill the same commission twice and raise every breakeven on the page.

### Both stop at a preview

Every row is shown with a status — NEW, DUPLICATE, CONFLICT, UNRESOLVED,
NOT_EGX, INVALID — before anything is written. `CONFLICT` is the one that
matters in practice: today's sale invoice refers to a holding bought long
before the invoices being imported, so it is caught in the preview with the fix
("record the opening position first") instead of at write time with a database
error. Rows are written oldest-first, buys before sells, so the order of a file
never decides whether its import succeeds.

## 6. A fee-schedule discrepancy worth knowing

`config/settings.json` carries a fee schedule transcribed from a contract note
dated 2026-08-18. The 2026-08-30 invoices disagree with it in two places:

| Line | Settings | 2026-08-30 invoices |
|---|---|---|
| FRA services | 0.0069% | 0.009% on the sale, 0.005% on the purchase |
| Order fee | 4.00 EGP | 2.00 EGP per invoice |

The FRA line also differs **between the buy and the sell on the same day**, so
a single flat rate cannot reproduce both. This matters only for *modelled*
costs — the breakeven price and the net-after-cost figures on future exits.
Imported invoices carry their real fee totals and are unaffected.

Left as-is rather than changed: the settings value came from a specific
document, and picking between two measurements is the account holder's call.

## 7. Files

| File | Role |
|---|---|
| `holdings/book.py` | Average-cost accounting, breakeven, valuation. Pure. |
| `holdings/store.py` | SQLite storage; refuses impossible history. |
| `holdings/plan.py` | Exit levels from the existing engines. Pure. |
| `holdings/rules.py` | One recommendation per position. Pure. |
| `holdings/invoices.py` | Broker e-invoice parsing and reconciliation. Pure. |
| `holdings/imports.py` | Bulk entry: invoices and the first-time spreadsheet. |
| `holdings/assistant.py` | The only module that touches providers or a clock. |
| `dashboard/portfolio.py` | The page. |

Tests: `tests/test_holdings_{book,store,plan,rules,assistant,invoices,imports}.py`
— every expectation written as the arithmetic rather than as captured output.

---

## 8. Not done yet

* **Outcome scoring.** The log is being written; nothing reads it back yet. That
  is the next piece, and it is what turns rules 7 and 8 from plausible into
  measured or discarded.
* **Intraday relative volume for the stock itself.** It needs a time-of-day
  volume profile to be comparable, and that profile has not been measured here.
* **The first visit of each session costs ~57s** for 21 positions, because the
  daily path is loaded once per symbol to rebuild the plans. It is sequential;
  a thread pool would cut it, and the provider chain has not been checked for
  thread safety.

## 9. Something to decide, not a defect

On the first day of use with 21 real positions, **ten of them reported
`TREND_BREAK` at once** — the live price a fraction below the EMA20 the plan
trails on, in one case by 0.07%.

That is faithful to the measured policy: `backtesting/managers/exit_manager.py`
exits when the day's *low* touches the trailing stop, so an intraday touch is an
exit there too. It looks alarming here only because these positions were opened
before the policy was ever applied to them, so a whole portfolio is being judged
by it on the same morning.

Two honest options, and the choice is the account holder's:

1. **Accept it.** This is what the backtested policy says about the portfolio as
   it stands, and every card shows the net result of acting.
2. **Require a close below the EMA rather than a touch.** More forgiving of an
   intraday wick — and a deviation from what was measured, so it would need to
   be run through the backtest before it could be called an improvement.

Nothing was changed unilaterally.
