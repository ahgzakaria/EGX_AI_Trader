# Mansa API — Real Capability Probe (EGX)

**All findings below come from real, live API calls made with a real free-tier
key (`MANSA_API_KEY`, masked everywhere — never printed in full).** No data is
fabricated or assumed. Probe date: 2026-07-20.

## Summary verdict

**Mansa API genuinely covers EGX and is a real, working, well-documented REST
API — but the free tier only exposes live quotes (30-minute freshness, no
volume), not historical daily OHLCV.** The endpoint this project actually
needs — `/api/v1/markets/exchanges/EGYPT/stocks/{ticker}/history` — returned
`HTTP 403 TIER_REQUIRED` on the free key: *"This endpoint requires a
professional tier key or higher. Your current tier is 'standard' (100
req/day)."* Per the pricing page, that almost certainly maps to the **Pro
plan ($49/month, 10,000 req/day)** — the OpenAPI spec calls it "Professional
tier" and the public pricing page's equivalent named tier is "PRO"; the exact
naming match is not 100% certain since Mansa's docs and public pricing page
use slightly different labels, but no cheaper tier plausibly matches.

## What was confirmed for free (real requests, real responses)

| Check | Result |
|---|---|
| API reachable | ✅ Yes — `/health` returned `"status":"operational"` |
| Credentials valid | ✅ Yes — Bearer auth accepted |
| Egypt exchange present | ✅ Yes — `code: "EGYPT"`, `name: "Egyptian Exchange (EGX)"`, `status: "live"` |
| Egypt trading hours/timezone | ✅ **Exact match to this project's engine**: `"10:00-14:30"`, `"Africa/Cairo"` |
| Egypt currency | ✅ `"EGP"` |
| Stocks tracked for Egypt | **224** (vs. this project's 265-symbol universe — coverage check below) |
| 20-symbol task basket resolved | ✅ **20/20 (100%)** via `/stocks/{ticker}` — COMI, SWDY, TMGH, EAST, FWRY, PHDC, TAQA, VALU, ORAS, ORHD, EFID, ETEL, ABUK, AMOC, JUFO, RAYA, HRHO, ADIB, HELI, GBCO all returned HTTP 200 with a real price |
| Symbol format | Mostly bare EGX codes (e.g. `COMI`, `SWDY`) — matches `to_egx_code()` already in this project. **One irregularity observed**: `ACTF` appeared in the stock list as `"ACTF.CA"` with a `"change_pct_stale": true` flag — symbol-format consistency across all 224 needs a full pass, not assumed clean. |
| Live quote fields | `ticker, name, price, change, change_pct, volume, market_cap, shares_outstanding, sector, logo_url, last_updated` |
| **Volume on live quotes** | ❌ **Always `null`** in every response observed (single-stock, list, search) |
| Quote freshness | `"data_freshness":"30_minutes"` — snapshot-based, **not comparable to Rubix's sub-minute live tick feed** |
| EGX 30 index daily history | ✅ **Works on free tier** — real daily closes 2026-06-01 through 2026-07-20 (34 points), but **volume is `null` on every point** |
| **Per-stock daily OHLCV history** | ❌ **BLOCKED on free tier** — `HTTP 403`, `error.code: "TIER_REQUIRED"`, `required_tier: "professional"` |
| Documented historical depth (per provider docs, not yet independently verified) | "Egypt and Botswana from 1995" for the per-stock history endpoint |
| Adjusted vs. unadjusted | Per-stock history schema (from docs, not yet tested) includes both `close` and `adj_close` fields — cannot confirm real EGX behavior until Pro tier is available |
| Rate limit | 100 requests/day on free tier, resets at midnight UTC (per docs) |
| Request-credit usage | Consumed ~25 calls during this real probe (well within the 100/day free limit) |

## The one real gap in this probe

I could not test the actual daily-OHLCV `/history` endpoint's real response —
freshness, adjusted-close behavior, corporate-action handling, or agreement
with Yahoo/Rubix — because the free key is rejected before returning any data.
**No historical OHLCV claim can honestly be made until a Pro-tier key is
tested.** Everything about that endpoint above (schema, 1995 depth) is from
the *documentation*, not a verified live response — same caveat this project
has applied to every other provider so far.

## Decision point — this needs your approval, not mine

Unlocking the one endpoint that actually matters for Swing/Daily (per-stock
daily history) costs **$49/month (Pro plan)** on a recurring subscription.
I have not purchased anything and will not — that is a real money commitment
and requires your explicit go-ahead. Free-tier evidence so far is
**genuinely promising** (100% basket coverage, exact Cairo session-hour match,
real live API) but **cannot answer the core freshness/reconciliation question**
without that upgrade.

**Options:**
1. You upgrade to Pro ($49/mo) yourself (same flow as the free key — enter the
   email tied to the key, pay by card, key upgrades automatically per the
   pricing FAQ) and I run the real Phase 1–9 comparison against the actual
   historical endpoint.
2. Stop here: free tier proves Mansa API could be a **live-quote supplement**
   (like a second Rubix-style overlay) but not a historical-data replacement
   for Yahoo without paying.
3. Continue researching other options instead.
