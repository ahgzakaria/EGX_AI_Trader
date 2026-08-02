# AI Analysis Infographic — Visual Audit

Comparison of the two supplied Arabic stock-analysis references against our
current compact card, detailed card and AI Analysis page, ahead of the
infographic redesign.

Nothing was implemented for this document. Every figure quoted below was read
from a live `AnalysisPresentation` for **FWRY** and **RAYA** on session
`2026-07-30`.

**Scope note.** The references are used only as a standard for clarity,
hierarchy and Arabic readability. Their branding, app identity, wording, icon
set and any data category we cannot substantiate are explicitly out of scope.

---

## 1. Hierarchy

| | Reference | Ours (detailed card) | Verdict |
|---|---|---|---|
| First thing the eye lands on | Ticker + price, ~90px type, isolated in a dark header block | Ticker at ~46px sharing a row with an identity mark | **Weak** |
| Second | Change amount + % in a coloured pill | Change buried as row 4 of a 5-row label/value list | **Weak** |
| Third | Chart | Chart is a 150px sparkline mid-card | **Weak** |
| Recommendation | Own tile, colour-coded | Pill in the header — reasonable | OK |
| Scanning pattern | Distinct zones, each with a titled frame | One continuous ribbon of label/value rows | **Weak** |

Our card has no dominant element. Everything is 20–22px, so nothing leads. The
reference establishes a clear 4-level scale (≈90 / 30 / 20 / 14) and the reader
gets ticker → price → direction in well under a second.

## 2. Typography

- **Reference**: roughly 4 sizes, heavy weight contrast, generous line height.
- **Ours**: `_Theme.size()` produces 46 / 26 / 22 / 21 / 20 / 19 — six sizes
  clustered within 7px, which reads as a single flat size.
- Our floor is 15px at 1080 wide. On a phone that is small but legible; the
  reference's smallest body text is noticeably larger relative to the canvas.
- Our two-column row grid puts the value on the left and the label on the right
  with no visual separation beyond position, so a scan cannot tell rows apart.

## 3. Arabic and RTL quality

Good today:

- `shape_arabic` + `bidi_reorder` in `core/analysis_card_generator.py` produce
  correctly connected, correctly ordered Arabic in the PNG.
- `wrap_rtl` measures on the rendered form, so wrapping is accurate.
- The company name wraps to two lines instead of being cut.

Problems:

- `_draw_rows` calls `fit_rtl`, which **ellipsises** rather than wraps. On the
  rendered FWRY card, `جودة الاتجاه السابق`, `حجم التصحيح` and `دليل الارتداد`
  all truncate with `…`. The spec for this task forbids ellipsised main labels.
- Bilingual mode concatenates `عربي · English` into one string, which roughly
  doubles every label's length and is the direct cause of the truncation above.
- Mixed Arabic/Latin values (`EMA20 أعلى من EMA50 · EMA20 above EMA50`) are one
  run with no bidi isolation inside the PNG renderer, so the Latin tokens are
  reordered by the surrounding RTL context.

## 4. Spacing and panel balance

- Reference: every group sits in a rounded panel with a title, a rule and
  consistent internal padding. Panels are sized to their content.
- Ours: only the sparkline gets a panel. Sections are separated by a bold title
  and 14px of air, so the card reads as one long column.
- Our measured fit pass prevents overflow but produces the opposite failure: on
  RAYA the summary block absorbs whatever is left, and on symbols with short
  narratives a visible empty band appears above the footer.

## 5. Information density

The reference fits far more per screen because it uses panels and columns. Our
card is less dense **and** less readable, which is the worst combination.

Density gap by section:

| Reference block | Ours |
|---|---|
| 5 quick metric tiles across the top | none |
| Support panel (3 levels) beside Resistance panel (3 levels) | one flat 6-row list |
| Technical read: 4 bullets | none on the card |
| Scenarios: 3 colour-coded paths | 4 rows of raw numbers |
| Indicators panel: 3 rows | mixed into the trend list |

## 6. Chart clarity

- Reference: a real chart with a fill, a reference line, min/max callouts and a
  volume strip — it communicates shape, level and participation.
- Ours: a 150px close-only polyline with a single dot. It has no axis, no
  volume, no levels and no last-close callout, so it carries almost no
  information.
- The far better `core/analysis_chart.py` figure (candles, EMA20/50/200, volume
  subplot, level bands, collision-resolved labels) exists but is **page only**;
  the card never uses it. That is the single largest missed opportunity.

## 7. Colour usage

- Reference: green/red carry consistent, immediate meaning — price direction,
  support vs resistance, positive vs negative scenario.
- Ours: the palette exists (`PALETTE` in `analysis_card_generator.py`) and is
  well chosen, but on the card almost everything renders in `text` or `muted`.
  Colour appears only in the recommendation pill and the Research-Only strip.
- Support and resistance are the same colour in our list; the reference makes
  them green and red respectively, which is instantly legible.

## 8. Visual anchors

The reference has four: the logo/monogram, the price block, the chart, and the
scenario colour bar. Ours has one weak anchor (the identity mark) and no
repeating visual rhythm, so there is nothing to navigate by.

---

## 9. Data findings — what we can and cannot honestly show

This is the part that constrains the design. Checked against a live model.

### Available and currently unused (should be surfaced)

| Field | FWRY value | Source |
|---|---|---|
| `price.turnover` | 68,655,911.19 | `PriceSummary.turnover` |
| `indicators.rsi_14` | 45.23 | `IndicatorSummary.rsi_14` |
| `indicators.sma_20` / `sma_50` | 19.0455 / 19.125 | `IndicatorSummary` |
| `indicators.macd_histogram` | −0.0135 | `IndicatorSummary` |

These are real, already computed, and would let the infographic reach the
reference's density **without inventing anything**. They are not currently
exposed on `AnalysisPresentation`.

### Present in the references, absent from our data — MUST be omitted

| Reference item | Status |
|---|---|
| عدد العمليات (transaction count) | Not in any contract — omit |
| الأخبار والتأثيرات (news) | No news source — omit |
| توزيعات الأرباح النقدية (dividends) | No dividend data — omit |
| Market cap / fundamentals | Not modelled — omit |
| Intraday "أداء السهم اليوم" curve | We hold **daily** candles; the intraday series is Rubix and is out of scope for this card. The chart must be labelled as completed daily sessions. |
| تقييم السهم as a 5-star rating | No documented star algorithm. A 5-level gauge is permissible **only** if mapped to `confidence` (0–100), which is documented. |

### Blocking defects found in `AnalysisPresentation` during this audit

These would make the infographic actively wrong, and must be fixed before or
during implementation:

1. **`positive_evidence` is not positive.** It is populated from
   `result.recommendation_reasons`, which is an **unsigned** list. For FWRY it
   contains `price below SMA20 and SMA50`, `RSI14 negative`, `MACD histogram
   negative` alongside `OBV rising`. Rendering that under **عوامل إيجابية**
   would tell the reader the opposite of the truth. The contract has no signed
   evidence field, so the infographic must either present these as neutral
   "أسباب التقييم" or a signed split must be sourced from the Core.

2. **`ema_alignment_ar` leaks a raw enum.** FWRY returns
   `EMA_ALIGNMENT_FAILED` because that code is absent from
   `PULLBACK_EMA_LABELS` and the lookup falls through to the raw value. Same
   class of defect as the `INVALID_PRIOR_UPTREND` leak fixed in `157756e`.

3. **`summary_en` is not English.** It carries the narrative headline, which is
   Arabic (`'FWRY: تجنّب — الإغلاق 18.77 جنيه، الثقة 59/100'`). English mode
   would render Arabic.

4. **Only two scenarios exist** (`breakout_continuation`, `support_breakdown`).
   The reference shows three. A neutral path can be *stated* from levels the
   Core already computed (between `support_1` and `resistance_1`) — that is a
   restatement, not a calculation — but it must be labelled as such and must not
   invent a trigger or target.

5. **Pullback is `NOT_APPLICABLE` on all five symbols sampled** (RAYA, FWRY,
   COMI, EGAL, SWDY). A HEALTHY/DEEP sample for visual validation may not be
   obtainable from live data today; if not, the state must be exercised with a
   constructed presentation model and that noted.

### Naming constraint

`close` is the **D-1 completed** close, never a live price. The reference labels
its number السعر الحالي. Ours must read:

> **آخر إغلاق مؤكد · Last Completed Close**

---

## 10. Summary of weaknesses to fix

1. No dominant price/ticker anchor.
2. Flat type scale — six sizes inside 7px.
3. Labels ellipsise instead of wrapping; bilingual mode makes it worse.
4. No panels; one undifferentiated column.
5. Sparkline carries almost no information while a good figure already exists.
6. Colour semantics unused — support and resistance look identical.
7. Real available fields (turnover, RSI, SMA, MACD) not surfaced.
8. `positive_evidence` is factually mislabelled — blocking.
9. `ema_alignment_ar` leaks a raw enum — blocking.
10. `summary_en` is Arabic — blocking for English mode.
