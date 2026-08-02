# AI Analysis Infographic — Wireframe

Target: **1080 × 1920** (primary), **1350 × 2400** (HD, same layout scaled by
1.25). Arabic-first, dark, phone-readable, understandable in ~10 seconds.

Every value is read from `AnalysisPresentation`. Nothing below is computed by
the renderer.

---

## Grid

```
canvas 1080 × 1920
outer margin        40
column gutter       20
panel radius        18
panel padding       22
section gap         22
```

A 2-column grid (2 × 500) and a 3-column grid (3 × 326.6) both fit the 1000px
content width exactly. Every panel snaps to one of them.

## Type scale (at 1080; ×1.25 at 1350)

| Role | Size | Weight |
|---|---|---|
| Price | 84 | 800 |
| Ticker | 62 | 800 |
| Panel title | 26 | 700 |
| Tile value | 32 | 750 |
| Body / row value | 24 | 700 |
| Row label | 22 | 500 |
| Caption / footer | 18 | 500 |

Floor is **18px** — up from 15. Four clearly separated steps, not six clustered.

## Tokens

```
bg          #0b1220     page
surface     #131c30     panel
surface_2   #17223b     tile inside panel
border      #223049
text        #e6edf7
muted       #8ea1bd
green       #34d399     positive · support · bullish
red         #f87171     negative · invalidation · bearish
amber       #fbbf24     resistance · caution
blue        #60a5fa     breakout · neutral · informational
purple      #c084fc     research-only (pullback)
```

Semantic rule: **support is always green, resistance always amber, breakout
always blue, invalidation always red, anything research-only always purple.**

---

## Layout

```
┌──────────────────────────────────────────────────────────────┐ y=40
│ HEADER                                                       │
│  ┌────────────────────────┐   ┌───────────────────────────┐  │
│  │ [monogram]  FWRY       │   │  آخر إغلاق مؤكد            │  │
│  │  Fawry For Banking…    │   │  18.77 جنيه               │  │  h=300
│  │  تحليل 2026-08-02      │   │  ▼ -0.23 (-1.21%)         │  │
│  │  جلسة 2026-07-30 EODHD │   │  [ تجنب حاليًا ] ثقة 59%   │  │
│  └────────────────────────┘   └───────────────────────────┘  │
│   ticker 62 · name wraps 2 lines      price 84 · change pill │
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ QUICK METRICS — 5 tiles × 196w                               │  h=130
│ [أعلى 19.10] [أدنى 18.71] [الحجم 3.66M] [القيمة 68.66M]      │
│ [ATR 0.42]                                                   │
│  omit any tile whose value is None (no em-dash tiles)        │
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ MAIN CHART — الرسم اليومي · آخر 90 جلسة مكتملة                │
│  candles + EMA20/50 + volume strip                           │  h=520
│  last-close marker · support band · resistance · breakout    │
│  invalidation · pullback zone (only when applicable)         │
│  rendered by core/analysis_chart.py → static PNG             │
└──────────────────────────────────────────────────────────────┘
┌───────────────────────────────┬──────────────────────────────┐
│ مستويات الدعم        (green)  │ مستويات المقاومة    (amber)  │  h=230
│  دعم أول        18.28         │  مقاومة أولى      19.68      │
│  دعم ثانٍ        17.71        │  مقاومة ثانية     20.10      │
│  منطقة التصحيح  — (purple)    │  الاختراق         19.68 (blue)│
│                               │  الإبطال          18.28 (red) │
└───────────────────────────────┴──────────────────────────────┘
┌───────────────────────────────┬──────────────────────────────┐
│ نظرة فنية                     │ المؤشرات                     │
│  • الاتجاه: عرضي              │  الاتجاه        عرضي         │  h=300
│  • الزخم: سلبي                │  الزخم          سلبي         │
│  • الحجم 0.55× من المتوسط     │  EMA20/50/200  19.01/19.03/17.33│
│  • RSI 45.2 — منطقة محايدة    │  RSI 14         45.2         │
│  • السعر أسفل EMA20           │  ATR            0.42 (2.22%) │
│   3–5 deterministic lines     │  قوة الاتجاه    50           │
└───────────────────────────────┴──────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ السيناريوهات — 3 rows, colour bar on the leading edge        │
│ ▌إيجابي (green)  التفعيل 19.68 · الهدف 20.72 · الإبطال 18.28 │  h=300
│ ▌محايد  (blue)   التداول بين 18.28 و 19.68 حتى ظهور اتجاه    │
│ ▌سلبي   (red)    كسر 18.28 · المتابعة نحو 17.71              │
│  neutral row is a RESTATEMENT of support_1..resistance_1     │
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ تحليل جودة التصحيح — بحثي فقط        (purple frame)          │
│  الحالة · نسبة التصحيح · عمق ATR · منطقة الدعم               │  h=210
│  حجم التصحيح · دليل الارتداد · سبب عدم القابلية               │
│  [ Research Only — غير معتمد كإشارة دخول ]                   │
│  panel is purple-framed and visually SUBORDINATE to الأسعار   │
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ أسباب التقييم  (neutral — NOT "عوامل إيجابية")               │
│  • السعر أسفل SMA20 و SMA50   • RSI14 سلبي                   │  h=190
│  • ماكد سلبي                   • OBV صاعد                    │
│  see §Evidence below — this is why the label is neutral       │
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ الخلاصة — 2–3 lines + ما يجب متابعته                          │  h=180
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ FOOTER  EODHD · جلسة 2026-07-30                              │  h=110
│ [دعم قرار فقط] [بحثي/ورقي] [التنفيذ غير مفعّل]                │
│ تحليل تعليمي إرشادي — ليس توصية بالشراء أو البيع              │
└──────────────────────────────────────────────────────────────┘
```

Heights total ≈ 2470 against 1840 usable, so the layout is **section-budgeted**:
header, chart, levels, scenarios and footer are fixed; نظرة فنية / المؤشرات,
التصحيح, أسباب التقييم and الخلاصة share the remainder and drop their lowest-
priority rows first. Priority order (highest kept last):

1. header · 2. chart · 3. levels · 4. scenarios · 5. pullback ·
6. indicators · 7. technical read · 8. summary · 9. reasons

## Renderer decision

**Pillow, extending the existing `_Canvas`.** Reasons, having checked
`requirements.txt`:

- The Arabic shaping and bidi reordering in `core/analysis_card_generator.py`
  (`shape_arabic`, `bidi_reorder`, `display_text`) is already correct and
  proven, and every alternative would have to reproduce it.
- HTML→PNG needs a headless browser or `imgkit`/`weasyprint`; **kaleido is not
  installed** (confirmed — `full_figure_for_development` raised on it), so even
  Plotly cannot rasterise today. Adding a browser engine for one card is a large
  dependency for no functional gain.
- SVG→PNG needs `cairosvg`, also absent, and would still need shaped Arabic
  because SVG text is not shaped by the rasteriser.
- Pillow is already a dependency and already produces the compact card.

To avoid the "hand-position every string" failure mode the task warns about, the
work adds a small **panel/tile/row layout layer** above `_Canvas` — `Panel`,
`Tile`, `Row`, `Bullet` primitives that own their own padding and measure their
own height. Sections declare content; the layer places it.

**Chart**: reuse `core/analysis_chart.build_daily_figure` and rasterise. Kaleido
is unavailable, so the card draws its own candle+volume panel through the same
`ChartWindow`/`select_window` used by the page — same data, same levels, same
window — rather than a second independent chart. This is the one place where a
second renderer exists, and it is bound to the shared window selector so the two
cannot diverge in data.

## Evidence labelling — deliberate deviation from the references

The references show عوامل إيجابية / عوامل سلبية. Our contract has no signed
evidence: `recommendation_reasons` mixes directions (audit §9). Splitting it by
keyword inside the renderer would be **calculation in the presentation layer**
and could mislabel a fact.

The wireframe therefore uses a single neutral panel **أسباب التقييم · Assessment
Reasons**. A signed split can be added later if the Core exposes one.

## Fields omitted, and why

| Omitted | Reason |
|---|---|
| عدد العمليات | not in any contract |
| الأخبار | no news source |
| توزيعات الأرباح | no dividend data |
| star rating | no documented algorithm; confidence % shown instead |
| intraday curve | Rubix series, out of scope for a D-1 card |
| السعر الحالي label | we hold a completed close, not a live price |

## Language modes

- **AR** — Arabic labels only; numbers and tickers bidi-isolated.
- **EN** — English labels; requires the `summary_en` defect fixed first,
  otherwise the summary renders Arabic in English mode.
- **BILINGUAL** — Arabic label with the English on a **second, smaller line**
  inside the same cell, never `عربي · English` on one line. This removes the
  truncation the audit found.

## Acceptance

- No ellipsised label anywhere.
- No panel overlapping another; no blank band > 60px.
- Arabic connected, RTL correct, no single-character wrapping.
- No raw enum on the canvas.
- Every number traceable to an `AnalysisPresentation` field.
- Preview bytes identical to download bytes.
