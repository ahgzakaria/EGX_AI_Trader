# EODHD Intraday EGX Entitlement and Coverage Audit

**Date:** 2026-07-28 (Africa/Cairo)

**Status:** AUDIT COMPLETE — no dataset, ranking, strategy or provider route implemented

**Branch:** `fix/scalping-historical-volatility-selection`

**Decision:** **B — current account is not entitled to EODHD intraday**

## Executive verdict

The configured EODHD credential is valid, paid and able to retrieve EGX symbol
metadata and daily OHLCV. It is **not entitled to the intraday endpoint**:

- `GET /api/intraday/COMI.EGX` returned HTTP **403** for `interval=1m`,
  `interval=5m` and `interval=1h`;
- the same 5-minute request made through the repository's
  `EODHDClient.get_json(...)` was classified as `EODHDAuthFailed` with HTTP 403;
- a bounded `GET /api/eod/COMI.EGX` control returned HTTP 200 and seven daily
  records, proving that the result is neither a missing credential nor a general
  EGX/account failure.

No further symbols were queried for intraday data after this entitlement result.
Consequently, EODHD intraday depth, bar quality and Rubix overlap cannot honestly
be measured under the current subscription.

The approved architecture must therefore remain, for now:

```text
daily research / corporate-action validation -> EODHD daily
live current session                         -> Rubix
historical intraday                          -> completed Rubix sessions
readiness                                    -> blocked until >=20 valid sessions
Yahoo operational access                     -> prohibited
```

The 20-session requirement **cannot be met immediately**. The earlier Rubix audit
found zero of 265 symbols with 20 completed, structurally valid sessions.

## 1. Exact client and endpoints tested

The repository has two relevant layers:

- `providers/eodhd_client.py:EODHDClient` is the token-safe, cache-first low-level
  client. Its typed methods cover `user()`, `exchange_symbols()`, `search()` and
  `eod()`. It has **no typed intraday method**.
- `providers/eodhd_provider.py:EODHDProvider.load_history()` deliberately rejects
  every interval except daily. That is a Phase-1 adapter limitation, not evidence
  about the remote account.

The audit therefore tested the remote endpoint both directly and through the
low-level generic method:

```text
GET https://eodhd.com/api/user
GET https://eodhd.com/api/exchange-symbol-list/EGX
GET https://eodhd.com/api/eod/COMI.EGX
GET https://eodhd.com/api/intraday/COMI.EGX

EODHDClient.get_json(
    "intraday/COMI.EGX",
    {"interval": "5m", "from": ..., "to": ...},
    force=True,
    deadline_seconds=12,
    max_attempts=1,
)
```

The exact-method probe used a temporary cache directory, an eight-second attempt
timeout, a twelve-second total deadline and a one-live-call budget. No API response
was persisted in the repository. The credential was loaded from the existing
gitignored main-workspace `.env`; only the repository's masked fingerprint
`sha256:5c071d053d` was exposed.

## 2. Bounded live-probe evidence

| Probe | Bound | Result |
|---|---|---|
| Account `/user` | one request | HTTP 200; paid monthly subscription; daily limit 100,000 |
| Current EGX listing | one request | HTTP 200; 239 unique codes; 36,181 bytes; response SHA-256 prefix `8e9d07af651d9c44` |
| COMI 1m | 2026-07-15 through 2026-07-29 UTC | HTTP 403; 9-byte response; SHA-256 prefix `78342a0905a72ce4` |
| COMI 5m | same 14-day window | HTTP 403; identical response |
| COMI 1h | same 14-day window | HTTP 403; identical response |
| Repository-client 5m control | 2026-07-16 through 2026-07-17 UTC | `EODHDAuthFailed`, HTTP 403 |
| COMI daily control | 2026-07-16 through 2026-07-28 | HTTP 200; seven rows, 2026-07-16 through 2026-07-27 |

Direct intraday denials completed in 0.578–0.594 seconds. Successful account and
daily calls completed in 0.609–0.672 seconds; the current exchange list completed
in 1.813 seconds. No timeout or rate-limit response occurred.

Account usage was 214 before the data probes and 217 afterward. The two exchange
list requests plus the daily control consumed three units in total; four forbidden
intraday attempts consumed no observable daily units. The live response headers
reported `X-RateLimit-Limit: 1200`, which is the measured per-minute request limit
for this account.

## 3. Subscription entitlement and intervals

| Item | Account result | Provider documentation | Audit conclusion |
|---|---|---|---|
| 1 minute | HTTP 403 | Supported by the endpoint but ticker/exchange dependent outside the US | Not entitled; EGX availability unproved |
| 5 minutes | HTTP 403 | Supported; other exchanges documented from October 2020 | Not entitled; EGX depth unproved |
| 1 hour | HTTP 403 | Supported; other exchanges documented from October 2020 | Not entitled; EGX depth unproved |
| Other intervals | Not called | Endpoint enum contains only `1m`, `5m`, `1h` | No other interval is available through this endpoint |

Official endpoint documentation currently states maximum **request windows**, not
guaranteed per-symbol depth:

- 1m: 120 days per request;
- 5m: 600 days per request;
- 1h: 7,200 days per request;
- no `from`/`to`: latest 120 days;
- non-US 5m and 1h: generally documented from October 2020;
- non-US 1m: varies by ticker and exchange and is not guaranteed.

These are documentation ceilings only. They are **not measured EGX depth**.
Earliest and latest EODHD intraday timestamps per symbol remain unavailable because
the account cannot retrieve even one intraday response.

The required personal plan is listed as **EOD+Intraday — All World Extended** at
USD 29.99/month or USD 299.90/year as of this audit. The `/user` payload confirms
paid/monthly status but does not identify the purchased product by name, so an
incremental upgrade price cannot be stated from account evidence.

References:

- https://eodhd.com/financial-apis/intraday-historical-data-api
- https://eodhd.com/financial-apis/api-limits
- https://eodhd.com/pricing

## 4. Exact 265-symbol mapping coverage

The live `exchange-symbol-list/EGX` response contained 239 unique codes. Exact
base-code matching against the 265-symbol project universe produced:

| Mapping result | Count | Percent |
|---|---:|---:|
| Exact current EODHD listing | 225 | 84.9% |
| Missing from current EODHD listing | 40 | 15.1% |
| Project universe | 265 | 100.0% |

The 40 missing codes are:

```text
ACRO ADRI AIFI AIHC ALEX AMPI APPC BIDI BIGP DCRC DIFC EGX30ETF EITP ELWA
ESAC ESRS FCMD FIRE FNAR GOCO GTHE HCFI IBCT INEG IRAX MISR MKIT MMAT NBKE
NCGC PACH RKAZ RMTV SMPP SNFI SUCE TORA UASG UPMS VERT
```

This is exact **listing/mapping coverage**, not intraday bar coverage. Under the
current entitlement, accessible EODHD intraday coverage is effectively zero of 265.
Even after an upgrade, all 225 mapped symbols would still need bounded data-bearing
checks; the 40 absent symbols must not be created by blind `.EGX` suffixing or
silently sourced from another provider.

## 5. Requested data-quality findings

| Requested property | Result |
|---|---|
| Earliest/latest timestamp per symbol | Not measurable: endpoint forbidden |
| Timestamp timezone | Schema documents Unix UTC, UTC `datetime`, and usually `gmtoffset=0`; no EGX row was available to verify |
| Cairo-session alignment | Not measurable from real EODHD intraday bars |
| 10:00–14:15 continuous session | Not measurable |
| 14:15–14:25 auction separation | Not proven; schema has no session-phase or auction marker |
| OHLCV completeness | Not measurable |
| Missing-bar rate | Not measurable |
| Trade count | Not present in documented intraday schema |
| Traded value | Not present in documented intraday schema; could only be derived approximately from bars, which is not approved |
| Bid/ask spread | Not present in documented intraday schema |
| Intraday raw/adjusted mode | Schema exposes only OHLCV and no adjusted field or adjustment parameter; empirical behavior is unproved |
| Splits/dividends in intraday bars | Not measurable |
| Batching | Endpoint path is one ticker; no bulk intraday endpoint is documented |
| Timeout behavior | No timeout observed; direct bounded calls returned within 1.813 seconds |

The daily control returned fields `open`, `high`, `low`, `close`,
`adjusted_close` and `volume`. Official daily documentation defines OHLC as raw,
`adjusted_close` as split-and-dividend adjusted, and volume as split-adjusted.
Existing repository audits also found that EGX split/bonus handling must be
event-specific: daily corporate-action records cannot be applied as one universal
adjustment rule. That daily evidence must not be projected onto inaccessible
intraday bars.

The EODHD exchange page describes 10:00–14:15 as the regular EGX session. It does
not document the project's 14:15–14:25 closing-auction phase in the intraday bar
schema. Even with entitlement, auction separation must therefore be validated
against real timestamps and Rubix event/volume evidence before EODHD sessions are
accepted.

## 6. Seven-session Rubix overlap comparison

The intended overlap dates were:

```text
2026-07-16  2026-07-19  2026-07-21  2026-07-22
2026-07-26  2026-07-27  2026-07-28
```

No EODHD intraday payload was available, so the comparison was correctly stopped:

| Comparison | Status |
|---|---|
| OHLC parity | Not measurable |
| Timestamp parity | Not measurable |
| EODHD missing bars | Not measurable |
| Continuous-session high/low | Not measurable |
| Session range | Not measurable |
| Volume | Not measurable |
| Auction handling | Not measurable |

The successful COMI **daily** response is only an entitlement control. It cannot
substitute for the requested intraday overlap, determine missing-minute rates or
prove continuous/auction separation.

## 7. Rate limits, request cost and safe backfill scope

Measured current account limits:

- 100,000 API-call units per day;
- response-header request limit of 1,200 per minute;
- current successful lightweight probes used three units;
- no timeout was observed.

Official documentation states that a successful intraday request costs five API
units. A safe implementation should nevertheless throttle well below the response
header ceiling, use one attempt per symbol/window, enforce a total deadline and
checkpoint every response fingerprint and cutoff.

**Current safe intraday backfill scope: zero.** Do not retry the 265-symbol universe
under the current plan.

If the account is upgraded, the next audit should remain staged:

| Stage | Scope | Maximum successful requests | API units |
|---|---|---:|---:|
| Entitlement/shape | 5 mapped symbols × 1m/5m/1h, one short window | 15 | 75 |
| Seven-session overlap | 10 representative symbols × 1m/5m, one range per symbol/interval | 20 | 100 |
| Exact mapped coverage | 225 mapped symbols, one chosen interval/window | 225 | 1,125 |
| Twenty-session candidate backfill | 225 mapped symbols, one interval within one allowed window | 225 | 1,125 |

At 225 mapped symbols, twenty continuous sessions contain at most approximately
1,215,000 one-minute slots or 243,000 five-minute slots before removing
non-trading/missing bars. This is the largest reasonable first backfill only after
the first two stages pass. A documentation-based full 5m pull from October 2020
would require up to four 600-day windows per mapped symbol: 900 requests and 4,500
API units. It is not approved until actual EGX quality and depth are demonstrated.

## 8. Provenance policy for any later feature store

No feature store was created. Any later record must include:

- `source_provider`;
- `provider_symbol`;
- `interval`;
- `raw_adjusted_mode`;
- `source_data_fingerprint`;
- `data_cutoff`;
- `overlap_validation_status`;
- `metric_version`;
- per-symbol source coverage and accepted-session count.

Providers must not be silently mixed within a symbol's historical selection
window. EODHD may become the historical source only after a single-source minimum
of 20 accepted EODHD sessions and successful Rubix overlap validation. Until then,
Rubix is the historical intraday source and the readiness gate remains closed.

## 9. Final decision

This audit triggers **Decision B**:

1. Keep EODHD daily for approved daily research and corporate-action validation.
2. Keep Rubix for the live session.
3. Retain completed Rubix sessions as the only presently accessible approved
   historical intraday source.
4. Keep `HISTORICAL_DATA_INSUFFICIENT` until at least 20 valid single-source
   sessions exist.
5. Do not implement the final ranking.
6. Do not use Yahoo operationally.
7. Do not merge or push this branch.
