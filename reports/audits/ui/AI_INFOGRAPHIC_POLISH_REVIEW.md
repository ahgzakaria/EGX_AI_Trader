# AI Infographic — Final Visual Polish Review

Samples: `reports/audits/ui/ai_card_redesign_samples/polished/`

Generation was offline from the local EODHD completed-daily dataset, with live
quotes disabled and the deterministic fallback narrative pinned. Rubix, Yahoo,
production databases and external AI providers were not used. Every final PNG
listed below was opened and inspected at its original resolution.

## Visual review

| Sample | Duplicate displayed levels | Scenario rows | Pullback rows | Ellipsis in title / summary / evidence | Bottom dead space | Company clipped | Enum leak | Arabic readability | Remaining weakness |
|---|---:|---:|---:|---|---:|---|---|---|---|
| `EGX_AI_ACAP_2026-07-30_AR_1080x2400.png` | none; 9.38 resistance/breakout combined | 4 | 13 extended | none | ~80–100 px above footer | no | none | clear; technical abbreviations remain familiar | long support-confluence evidence is complete but visually dense |
| `EGX_AI_FWRY_2026-07-30_AR_1080x1920.png` | none; all four displayed values distinct | 4 | 6 primary | none | ~0–10 px | no | none | clear at phone scale | compact evidence/summary use the 18 px readability floor |
| `EGX_AI_RAYA_2026-07-30_AR_1080x1920.png` | none; all four displayed values distinct | 4 | 6 primary | none | ~0–10 px | no | none | clear and balanced | compact evidence/summary are intentionally secondary |
| `EGX_AI_SIPC_2026-07-30_AR_1080x1920.png` | none; 4.29 resistance/breakout combined | 4 | 6 primary | none | ~0–10 px | no (two full lines) | none | clear; no English sentence in Arabic evidence | long English legal company name remains visually prominent |
| `EGX_AI_FWRY_2026-07-30_EN_1080x1920.png` | none; all four displayed values distinct | 4 | 6 primary | none | ~0–10 px | no | none | n/a; English is complete | compact evidence is the densest English block |
| `EGX_AI_RAYA_2026-07-30_BILINGUAL_1080x1920.png` | none; all four displayed values distinct | 4 | 6 primary | none | ~0–10 px | no | none | readable, with complete Arabic and English text | bilingual mode is necessarily the densest and uses 18 px row text |

## Confirmed refinements

- Pullback is titled **جودة التصحيح** with a separate **بحثي فقط** badge.
- The primary Pullback hierarchy contains state, percentage, ATR depth, support
  zone, volume behaviour, and either reversal evidence or the exact
  failure/invalidation reason. Secondary diagnostics appear only in Extended.
- Long Pullback values and primary labels wrap at full width; no important label
  or value is shortened with an ellipsis.
- Scenarios are capped at four typed rows: positive, waiting, negative and
  diagnostic reward/risk. Duplicate scenario invalidation rows were removed.
- Resistance and breakout are combined only when their independently stored
  prices are equal at the two-decimal display precision. Resistance 2 is omitted
  only when it is equal at that same precision.
- Summary is a deterministic three-line digest: conclusion, risk and next
  condition. It reads typed presentation fields and does not mutate the source
  narrative.
- Assessment evidence remains a closed-vocabulary mapping, is capped at four,
  and contains no complete English sentence in Arabic mode.
- Recommendation and confidence now share one strong strip; the values and tone
  are unchanged.
- Compact card, chart height, header hierarchy, icons, colour roles and the
  underlying presentation density remain unchanged. Only fixed-canvas row text
  is reduced when needed: 23 px for primary AR/EN rows and the existing 18 px
  floor for bilingual/compact secondary text.

## Data and byte integrity

Sample generation recorded the source identity in memory and asserted both of
these invariants for every render:

1. preview bytes are identical to download bytes;
2. `AnalysisPresentation.identity()` is unchanged before and after rendering.

Therefore the visual polish does not change strategy, recommendation,
confidence, Pullback calculations, scenario calculations, or any stored level.
