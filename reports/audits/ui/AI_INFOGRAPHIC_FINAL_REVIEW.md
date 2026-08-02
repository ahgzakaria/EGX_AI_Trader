# AI Infographic — Final Review

Samples: `reports/audits/ui/ai_card_redesign_samples/final/`

## Sample findings

| File | Dims | Overflow | Name clipped | Label ellipsis | Raw enum | English leak (AR) | Chart collision | Dead space |
|---|---|---|---|---|---|---|---|---|
| `FWRY_ar_1080x1920.png` | 1080×1920 | none | no | none in primary labels | none | none | resolved | ~0 |
| `RAYA_ar_1080x1920.png` | 1080×1920 | none | no | none | none | none | resolved | ~0 |
| `SIPC_ar_1080x1920.png` | 1080×1920 | none | no (51 chars) | none | none | none | resolved | ~0 |
| `FWRY_en_1080x1920.png` | 1080×1920 | none | no | none | none | n/a | resolved | ~0 |
| `RAYA_bilingual_1080x1920.png` | 1080×1920 | none | no | none | none | n/a | resolved | ~0 |
| `COMI_ar_1350x2400.png` | 1350×2400 | none | no | none | none | none | resolved | ~0 |
| `FWRY_ar_1080x2400ext.png` | 1080×2400 | none | no | 3 secondary diagnostic labels | none | none | resolved | **~0** (was ~350px) |

## Extended dead space — resolved

Filled with content that had been reduced from the primary card, all of it
already in the model:

* complete indicator table (cap lifted),
* full pullback diagnostics (prior trend, structure, swing high, impulse low,
  impulse retracement, confluence, reversal evidence),
* scenario invalidation levels and reward/risk,
* full assessment evidence and a longer summary,
* a **Source & Method** provenance block (EODHD, completed session, price basis,
  data status, D-1 methodology, research-only note).

Nothing invented: no news, dividends, market cap, transaction count, live price,
Rubix intraday or star ratings.

## Mini-chart collisions — resolved

The card's mini-chart now calls `resolve_label_lanes` from
`core/analysis_chart.py` — the same deterministic solver the page chart uses, not
a second algorithm. Lines stay on their true prices; only labels move into the
annotation lane. On FWRY the cluster 18.04 / 18.28 / 18.77 / 19.01 / 19.03
separates cleanly and 18.77 (last close) is now legible.

Only the strongest levels are plotted: last close, support 1, resistance 1,
breakout, invalidation, plus the pullback zone band.

## Remaining weaknesses

1. **Three secondary labels ellipsise on the Extended card** — "سبب عدم…",
   "جودة الاتجاه…", "الهيكل…". Primary labels are unaffected. Their values are
   long localized phrases competing with the value column.
2. **Summary still truncates** on the Extended card at 6 bullet lines.
3. **Pullback HEALTHY/DEEP remains unsampled** — every live symbol returns
   `NOT_APPLICABLE`, so those visual states are still unverified against real data.
4. **Two pre-existing tests fail in suite order** — see the task report; not a
   rendering defect.
