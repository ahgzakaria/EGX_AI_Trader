# TradingView Daily Data Provider — Capability Audit & Shadow Validation Report

**Status:** ✅ Compliant infrastructure built and unit-tested (synthetic data).
**Production routing:** ❌ **Not activated. Not recommended.** Everything ships
`enabled: false`, `shadow_mode: true`. `core/data_provider.py` is unchanged.
**Real TradingView data used:** **None.** This environment has no TradingView
account, no user-exported CSV, and no live webhook. Every reconciliation/shadow
report below is honestly schema-only, not fabricated.

---

## What was built (all disabled, isolated, additive)

| Phase | Deliverable |
|---|---|
| A | [TRADINGVIEW_PROVIDER_ARCHITECTURE_AUDIT.md](TRADINGVIEW_PROVIDER_ARCHITECTURE_AUDIT.md) |
| B | [TRADINGVIEW_ACCESS_CAPABILITY_REPORT.md](TRADINGVIEW_ACCESS_CAPABILITY_REPORT.md) — researched against current official TradingView docs |
| C | [TRADINGVIEW_TEST_BASKET_AND_SYMBOL_MAP.md](TRADINGVIEW_TEST_BASKET_AND_SYMBOL_MAP.md) — 23-symbol basket, unverified candidate `EGX:` mapping |
| D | [scripts/tradingview_confirmed_daily_alert.pine](../../../scripts/tradingview_confirmed_daily_alert.pine) + [providers/tradingview_webhook_receiver.py](../../../providers/tradingview_webhook_receiver.py) — isolated, not deployed |
| E/F | [providers/tradingview_csv_provider.py](../../../providers/tradingview_csv_provider.py) — manual-export-only, strict validation |
| G/H | [services/tradingview_reconciliation.py](../../../services/tradingview_reconciliation.py) → 4 reports (schema-only, real logic proven with synthetic data) |
| I/J | [providers/tradingview_completed_daily_bridge.py](../../../providers/tradingview_completed_daily_bridge.py), [services/tradingview_daily_shadow.py](../../../services/tradingview_daily_shadow.py) → `reports/tradingview_shadow_decision_comparison.csv` |
| K | `config/settings.json → tradingview` block, all off/none by default |
| L | "TradingView Research (Experimental, Disabled)" panel in [dashboard/home.py](../../../dashboard/home.py) |

**Test coverage added:** 40 new tests (15 webhook receiver, 12 CSV provider,
7 reconciliation-math, 6 completed-daily bridge) — all synthetic data, proving
the plumbing is correct. **Full suite: 223 passed, 0 failed** (183 prior +
40 new). No pre-existing test changed or was skipped.

---

## The 10 questions, answered honestly

**1. Is there a compliant method to obtain TradingView EGX data?**
Yes, two: **manual CSV chart export** and **official Pine Script alert
webhooks**. Both are documented by TradingView itself (see Phase B report with
sources). No general market-data API exists for retail/consumer use — the
Charting Library / Datafeed API / Broker API are developer integration specs,
not TradingView data sources, and licensed real-data access requires a
commercial exchange/vendor agreement outside this codebase's reach.

**2. Is it manual CSV, official webhook, licensed API, or unavailable?**
**Manual CSV** (fully manual, Pro+/Premium plan) and **official webhook**
(semi-automated: manual one-time alert setup per symbol, then automatic
delivery). **Licensed API: unavailable** to this project. **A pure
"unattended, bulk, 265-symbol" route does not exist** under any compliant method.

**3. How many of the 265 symbols are supported?**
**Unknown and not verifiable from this environment.** A 23-symbol candidate
basket with a mechanical `EGX:<CODE>` mapping was designed (Phase C), but
**zero symbols have been confirmed to actually resolve on TradingView** — that
requires your TradingView account and symbol search, which this environment
does not have. Numerically, webhook alert quotas only cover 265 symbols on
Premium (400) or Ultimate (1,000) plans, and even then require 265
individually-configured alerts (one alert = one chart/symbol in TradingView's
model).

**4. Is TradingView newer than Yahoo?**
**Cannot be determined.** No real TradingView data was ingested. The freshness
report ran successfully end-to-end and would answer this the moment real data
arrives, but currently shows **0/23 symbols with any TradingView data** (see
[reports/tradingview_freshness_audit.csv](../../../reports/tradingview_freshness_audit.csv)).

**5. By how many completed sessions?**
**N/A** — same reason as #4. `tradingview_newer_for: 0`, `yahoo_newer_for: 0`,
`tradingview_unavailable_for: 23` in the actual run output.

**6. Are Open/High/Low/Close/Volume trustworthy?**
**Cannot be assessed without real data.** The validator (Phase F) enforces
positive OHLC, correct ordering, non-negative volume, and daily-interval-only —
proven correct against synthetic edge cases (zero Low, negative volume,
ordering violations all correctly rejected in tests) — but no real TradingView
value has been checked yet.

**7. Does TradingView match Rubix Close and cumulative Volume?**
**Cannot be assessed.** The three-way reconciliation script
(`services/tradingview_reconciliation.py`) computes this automatically against
Rubix's best-effort daily aggregate (reusing the already-audited
`RubixDailyAggregator` from the prior Rubix bridge work) the moment real
TradingView rows exist — but today [reports/tradingview_rubix_reconciliation.csv](../../../reports/tradingview_rubix_reconciliation.csv)
is empty of real comparisons (23/23 "TradingView Available: No").

**8. How many Shadow decisions changed?**
**0 of 23** — necessarily, since sessions-appended is 0 for every symbol
(no real TradingView data to append). See
[reports/tradingview_shadow_decision_comparison.csv](../../../reports/tradingview_shadow_decision_comparison.csv).

**9. Did any strategy, indicator, threshold, replay, or backtest change?**
**No.** `core/data_provider.py` routing is byte-for-byte unchanged. All new
code lives in new, isolated modules (`providers/tradingview_*`,
`services/tradingview_*`) plus one additive, disabled config block and one
additive, collapsed-by-default dashboard panel. Full regression suite:
**223/223 passed**, including all frozen Classic/Breakout/Adaptive/walk-forward
tests, with no test modified.

**10. Is TradingView suitable as:**
- **Production provider** — ❌ Not currently; cannot even confirm symbol
  coverage or data quality without your TradingView account.
- **Completed-daily bridge** — ❌ Not currently; the bridge code exists and
  passes all synthetic acceptance tests, but has never ingested a real candle.
- **Manual backup** — ⚠️ **Plausible in principle** (CSV export is official
  and simple), but only as a genuinely manual, occasional, per-symbol action —
  not a repeatable pipeline, since TradingView's export has no bulk/scripted
  path.
- **Unavailable/unrecommended** — ✅ **This is the accurate verdict today**,
  not because TradingView is technically incapable, but because **no real
  access has been established or verified in this environment.**

---

## What would change this verdict

1. **You export a real CSV** for a handful of basket symbols (e.g. via
   TradingView's "Download chart data…") and drop them into a directory, then
   set `config/settings.json → tradingview.tradingview_csv_directory` to that
   path. Re-running `python -m services.tradingview_reconciliation` and
   `python -m services.tradingview_daily_shadow` will immediately produce real
   numbers using the exact code already built and tested.
2. **You configure a real Pine alert + webhook** (2FA required, per Phase B)
   for at least one symbol using [scripts/tradingview_confirmed_daily_alert.pine](../../../scripts/tradingview_confirmed_daily_alert.pine),
   deploy `providers/tradingview_webhook_receiver.py` behind your own HTTPS
   endpoint, and set `tradingview_webhook_enabled: true` +
   `tradingview_webhook_secret`.
3. Either path, run over **multiple completed EGX sessions** (per the
   acceptance gates — a single session is not enough), would let the
   reconciliation/freshness reports answer questions 3–8 for real.

Until then, per your own acceptance gates, **production activation is not
recommended and the bridge remains disabled in shadow mode.**
