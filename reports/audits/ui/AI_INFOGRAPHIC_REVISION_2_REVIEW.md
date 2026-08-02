# AI Infographic — Revision 2 Review

Visual-hierarchy and readability revision. No analysis value changed.

Samples: the shipped set lives in
`reports/audits/ui/ai_card_redesign_samples/final/`.

The revision-1 and revision-2 PNG sets were intermediate working output and are
not tracked; the measured before/after figures below are the record of what
changed between them.

---

## Typography — before / after (at 1080 wide)

| Role | Rev 1 | Rev 2 | Change |
|---|---|---|---|
| Price | 84 | **108** | +29% |
| Ticker | 62 | **78** | +26% |
| Section title | 25 | **32** | +28% |
| Tile value | 31 | **38** | +23% |
| Body / bullet | 22 | **27** | +23% |
| Row label | 21 | **26** | +24% |
| Caption | 18 | **21** | +17% |
| Footer | 18 | 18 | floor |
| Arabic line height | ×1.30 | **×1.42** | +9% |
| Panel padding | 18 | **22** | +22% |

Smallest body text on the card is now **27px**; the absolute floor (footer only)
is 18px. Nothing primary requires zooming at typical phone width.

## Chart

| | Rev 1 | Rev 2 |
|---|---|---|
| Panel height | 268px | **352px** (+31%) |
| Share of page | 14% | **18%** |
| In-chart labels | 6 | **4** (last close, support 1, resistance 1 / breakout, invalidation) |

Secondary levels moved to the support/resistance panels, as instructed.

## Header

Rebuilt as two blocks inside one panel:

* **Price block (left)** — آخر إغلاق مؤكد, 18.77 EGP at 108px, change, then the
  recommendation badge and confidence directly beneath it.
* **Identity block (right)** — monogram, ticker at 78px, company name wrapping to
  two lines.

Provider/session metadata is demoted to a quiet 18px strip below the header.

The badge was moved into the price block specifically because it previously sat
in the identity lane and collided with a two-line company name.

## Sections — what moved

Primary **1080×1920** now carries the strongest sections only:

1. Header · 2. Quick metrics (**4 max**) · 3. Chart · 4. Support | Resistance ·
5. Indicators | Scenarios · 6. Pullback Health | Technical Read · 7. Footer

Moved to the new **Extended 1080×2400**: Assessment Evidence, Summary.

Panel caps: technical 3–4 bullets, indicators 5 rows, scenarios 3 paths,
pullback 4 rows, evidence 4 reasons, summary 3 lines.

A structural fix underneath: `Panel.measure` was adding a section gap after every
child, double-spacing `Row` lists and inflating each panel by ~50px. Rows now
carry only their own leading.

## Arabic assessment evidence

`recommendation_reasons` is produced by one function over a **closed vocabulary**
(`core/ai_analysis_evidence.py`), so `ASSESSMENT_REASON_AR` is an exact-match
table over that vocabulary — not keyword replacement. An unrecognised phrase is
**dropped**, never machine-mangled.

Coverage on FWRY: **9 / 9**. Sample output:

* السعر أدنى SMA20 وSMA50
* السعر أدنى EMA20
* مؤشر RSI سلبي
* هيستوجرام MACD سلبي

Technical abbreviations stay in Latin script inside the Arabic sentence, as
specified.

## Sample findings

| File | Dims | Price prominence | Chart % | Min body | Panels | Overflow | Name clipped | English leak (AR) | Enum |
|---|---|---|---|---|---|---|---|---|---|
| `FWRY_ar_1080x1920.png` | 1080×1920 | 108px, dominant | 18% | 27px | 6 | none | no (2 lines) | none | none |
| `RAYA_ar_1080x1920.png` | 1080×1920 | 108px | 18% | 27px | 6 | none | no | none | none |
| `SIPC_ar_1080x1920.png` | 1080×1920 | 108px | 18% | 27px | 6 | none | no (51 chars) | none | none |
| `FWRY_en_1080x1920.png` | 1080×1920 | 108px | 18% | 27px | 6 | none | no | n/a | none |
| `RAYA_bilingual_1080x1920.png` | 1080×1920 | 108px | 18% | 27px | 6 | none | no | n/a | none |
| `COMI_ar_1350x2400.png` | 1350×2400 | 135px | 18% | 34px | 6 | none | no | none | none |
| `FWRY_ar_1080x2400ext.png` | 1080×2400 | 108px | 15% | 27px | 8 | none | no | none | none |

## Remaining weaknesses

1. **Extended 1080×2400 has ~350px of empty space at the bottom.** Content ends
   around y=1750. It should carry the full indicator set, complete pullback
   diagnostics and extended scenarios to justify its height. Currently it is the
   primary card plus two panels. **This is the most visible outstanding issue.**
2. **Summary still ellipsises** at 3 lines on the extended card.
3. **Confidence text sits close to the recommendation badge** in the price block
   — no overlap measured, but the spacing is tight in English mode.
4. **Pullback HEALTHY/DEEP still unsampled** — every live symbol returns
   `NOT_APPLICABLE`, so that visual state remains unverified.
5. **Chart labels 18.28 / 18.04** remain close together in the mini-chart; the
   page chart's collision solver is not applied to the card's own mini-chart.
