# TradingView Access Capability Report (Phase B)

**Method:** Web research against current official TradingView support
documentation and pricing pages only (sources listed at the end). **No
scraping, no private API, no browser-session extraction was performed to
produce this report** — it documents what TradingView itself publishes about
its own compliant access methods.

**Bottom line:** Two compliant methods exist and are officially documented —
**manual CSV chart export** and **Pine Script alert webhooks**. Neither is a
bulk, unattended, "give me 265 EGX daily bars" API. Every automated route
requires you to personally hold and configure a paid plan; nothing here can be
activated or verified without you.

---

## A. Manual TradingView CSV export

| Question | Answer | Source |
|---|---|---|
| Available method? | Yes — "Download chart data…" from the Supercharts toolbar dropdown | [Official: how to export chart data](https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/) |
| Plan requirement | Reported as **Pro+/Premium tier and above** (not on Free) | [Official export blog post](https://www.tradingview.com/blog/en/export-chart-data-in-csv-14395/); secondary confirmation via [backtestbase guide](https://www.backtestbase.com/education/tradingview-export-guide) |
| Contents | OHLC + volume + any indicators currently plotted on the chart | Official export blog post |
| Historical depth | **Whatever is currently loaded/visible on the chart** — you must manually scroll/drag the x-axis left to pull more history into view *before* exporting; there is no "give me N years" bulk parameter | Official support page (confirmed via direct fetch) |
| Forming vs. completed bar | **Not addressed** in official docs — must be assumed to include the live forming bar exactly like the visible chart does, so a human step (or explicit last-row check) is required to exclude it | Not disclosed by TradingView |
| Automation status | **Manual only.** A UI menu click; TradingView does not document a scripted/bulk export | Official support page |
| Covering 265 symbols | **Not practical as a recurring bulk operation** — each symbol requires opening its chart and manually exporting; there is no batch export across a watchlist in official docs |

## B. Official Pine Script alert webhooks

| Question | Answer | Source |
|---|---|---|
| Available method? | Yes — Pine `alertcondition()`/`alert()` can POST to a user-supplied HTTPS webhook URL | [Official: how to configure webhook alerts](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/) |
| Security requirement | **Webhook alerts require 2-Factor Authentication enabled on the account** | Official support page (confirmed via direct fetch) |
| Payload fields | Official placeholders: `{{open}}`, `{{high}}`, `{{low}}`, `{{close}}`, `{{volume}}`, `{{time}}`, `{{exchange}}`, plus `{{plot_0..19}}` / `{{plot("Name")}}` for custom series | [Pine alerts FAQ](https://www.tradingview.com/pine-script-docs/faq/alerts/), [variable-in-alert guide](https://www.tradingview.com/support/solutions/43000531021-how-to-use-a-variable-value-in-alert/) |
| Confirmed-bar delivery | Achievable in Pine via `barstate.isconfirmed` gating an `alert()` call, combined with an alert configured to trigger "Once Per Bar Close" — this is standard, documented Pine practice, not an unofficial trick | Pine Script language reference (`barstate.isconfirmed`) |
| Transport constraints | Only ports **80/443**; POST body must be valid JSON for `application/json` content-type; **requests are cancelled after 3 seconds**; TradingView publishes **4 fixed source IPs** for allowlisting | Official support page (confirmed via direct fetch) |
| Active-alert limits by plan | **Free 3, Essential 20, Plus 100, Premium 400, Ultimate 1,000** (price + technical alerts tracked separately; Essential/Plus alerts expire ~2 months, Premium/Ultimate do not) | [TradingView pricing](https://www.tradingview.com/pricing/) via aggregated 2026 plan-comparison sources |
| Covering 265 symbols | **Numerically feasible only on Premium (400) or Ultimate (1,000)** — and even then, each symbol typically needs its **own alert instance** (one alert = one chart/symbol context in standard Pine usage), so this is 265 individually-configured alerts, not one multi-symbol alert | Derived from official alert-model docs; plan limits are third-party-aggregated, not from a single official page — **verify directly against your account before relying on this** |
| Unattended collection | Officially supported **in principle** (webhook fires automatically once configured) but requires an already-running HTTPS receiver on your side, which is Phase D of this task | — |

## C. Licensed / official market-data API

| Question | Answer | Source |
|---|---|---|
| Does a consumer/retail data API exist? | **No.** TradingView offers three developer-facing APIs — the free self-hosted **Charting Library**, the **Datafeed API** (a *spec* for feeding your own data *into* their charting widget), and the **Broker REST API** (for brokerages integrating with TradingView) | [Advanced Charts API reference](https://www.tradingview.com/charting-library-docs/latest/api/); aggregated analysis via [financialtechwiz](https://www.financialtechwiz.com/post/tradingview-api/), [pineify](https://pineify.app/resources/blog/does-tradingview-have-an-api-comprehensive-guide-to-tradingviews-api-offerings) |
| Is the Datafeed API a TradingView data source? | **No — explicitly not.** Per the task's own compliance rule and confirmed by research: the Datafeed API is the *interface your application implements* to supply **your own** data into TradingView's chart widget. It does not give you TradingView's market data. |
| Any path to licensed real data? | Only through a **market-data agreement with an exchange/vendor**, layered on top of a Charting Library license (i.e., an enterprise commercial relationship, not something available to configure from this codebase) | Aggregated research; not independently verified against an official contract page |
| Conclusion | **Unavailable** as an automated bulk data source for this project | — |

## EGX-specific coverage (uncertain — flagged, not assumed)

- TradingView does list at least the **EGX 30 index** (`EGX:EGX30`) confirming Egyptian Exchange symbols exist on the platform.
- Whether **all 265** of this project's individual EGX tickers exist on TradingView, and whether their data is **real-time or delayed** for your account tier, is **not confirmed by this research** — TradingView's own docs point to "contact support" / the Market Data purchase page for exchange-specific entitlement, and this depends on your account, which I cannot inspect.
- **This must be verified manually, symbol by symbol, before Phase C can proceed with real data.**

---

## Answering the Phase B checklist directly

| Item | Finding |
|---|---|
| Available method | CSV export (manual) and Pine webhook alerts (semi-automated) — both official |
| Authentication requirements | CSV: normal login + Pro+/Premium plan. Webhook: 2FA mandatory |
| Manual vs. automated | CSV = fully manual per chart. Webhook = automated *after* manual one-time alert setup per symbol |
| Historical depth | CSV: only what's scrolled into view, no documented bulk history parameter. Webhook: none — it only emits new bars going forward, not backfill |
| EGX availability | Confirmed at least for the index; **individual-stock coverage unverified** |
| Delay status | Not documented for EGX specifically; depends on account entitlement |
| Exchange entitlement requirements | Real-time EGX data may require a purchased data package; unconfirmed for this account |
| Rate / alert limits | CSV: none documented (manual). Webhook: alert-count capped by plan (see table); 265 symbols only realistic on Premium/Ultimate |
| Cover all 265 symbols? | **CSV: impractical as a repeatable process (one manual export per symbol, per refresh). Webhook: numerically possible only on Premium/Ultimate, and only with 265 separately configured alerts.** |
| Unattended collection officially supported? | **No** for CSV (inherently manual). **Partially** for webhook (delivery is automatic once alerts exist, but alert creation and any backfill are not) |

## Stop condition per task instructions

Per the task's own rule — *"If access is unclear, stop before implementation"* —
this report **does** treat EGX-specific entitlement and per-account alert
capacity as unclear, and does **not** assume they resolve favorably. Phases D
and E below are built as **inert, disabled, testable-only-with-synthetic-data**
components so the architecture exists and is proven safe, but **no real
TradingView ingestion has occurred or can occur without you personally**:
providing exported CSV files, or configuring and firing real webhook alerts
from your own TradingView account.

---

## Sources

- [How to export chart data — TradingView](https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/)
- [You can now export & download data into a CSV file — TradingView Blog](https://www.tradingview.com/blog/en/export-chart-data-in-csv-14395/)
- [TradingView Export Guide: Plans, CSV & XLSX Strategy Data](https://www.backtestbase.com/education/tradingview-export-guide)
- [How to configure webhook alerts — TradingView](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/)
- [Alerts (Pine Script FAQ) — TradingView](https://www.tradingview.com/pine-script-docs/faq/alerts/)
- [How to use a variable value in alert — TradingView](https://www.tradingview.com/support/solutions/43000531021-how-to-use-a-variable-value-in-alert/)
- [TradingView Subscriptions: Pricing and Features](https://www.tradingview.com/pricing/)
- [API Reference | Advanced Charts Documentation](https://www.tradingview.com/charting-library-docs/latest/api/)
- [Does TradingView Have an API? — Pineify Blog](https://pineify.app/resources/blog/does-tradingview-have-an-api-comprehensive-guide-to-tradingviews-api-offerings)
- [TradingView API in 2026 — financialtechwiz](https://www.financialtechwiz.com/post/tradingview-api/)
- [EGX 30 (Egyptian Exchange) Charts and Quotes — TradingView](https://www.tradingview.com/symbols/EGX-EGX30/)
- [How to purchase additional market data — TradingView](https://www.tradingview.com/support/solutions/43000471705-how-to-purchase-additional-market-data/)
