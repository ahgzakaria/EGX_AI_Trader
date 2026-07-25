# EODHD Shadow Validation Report

**Date:** 2026-07-23 · **Mode:** `EODHD_SHADOW` · **Active historical provider: Yahoo
(unchanged)** · **Live intraday provider: Rubix (unchanged)** · production disabled.
This is a read-only shadow evaluation — no provider was switched and no strategy,
score, threshold, indicator, TP/SL, paper flag, production flag, or execution behavior
was changed. **515 tests pass.**

---

## What was built (shadow-only)
- `core/environment.py` — one central `.env` loader (`override=False`, idempotent,
  cwd-independent), `is_eodhd_configured()`, `masked_eodhd_token_status()`. Wired once
  at `app.py` startup and callable from CLI scripts. `python-dotenv` added.
- `providers/eodhd_client.py` — cache-first EODHD HTTP client: persistent disk cache
  (reruns don't consume budget), timeouts, bounded retries+backoff, per-process budget
  guard, total token redaction (never in cache keys, logs, or errors).
- `providers/eodhd_historical_provider.py` — canonical `Date, Open, High, Low, Close,
  Adj Close, Volume`; ascending unique dates; numeric validation; **no zero-price
  substitution; no fabricated rows**; non-negative volume; raw/adjusted-close distinct;
  provenance metadata.
- `providers/eodhd_symbol_map.py` + `scripts/audit_eodhd_symbol_mapping.py` — mapping
  verified against the real EODHD list (never suffix-replacement alone).
- `providers/provider_mode.py` + `config/settings.json` `provider_mode` — the four
  modes; set to `EODHD_SHADOW`; `active_historical_provider()` still returns `yahoo`.
- `providers/eodhd_shadow.py` + `scripts/eodhd_shadow_validation.py` — the comparison.

## The 13 questions

**1. Does `.env` loading work from app and CLI?**
Yes. Loaded centrally in `app.py` startup and available to CLI scripts via
`load_project_environment()`. Verified live: the token resolves from `.env` (the
project previously only read `os.getenv`, so `.env` was not auto-loaded — now it is,
centrally, once). OS environment variables still win (`override=False`).

**2. Did the token ever appear in output?**
No. Only a masked status is ever shown — `len=23, sha256:5c071d053d…`. The token is
redacted from every request error and is excluded from cache keys. A subprocess test
asserts the token never appears in stdout/stderr.

**3. EGX mapping coverage out of the internal universe.**
Internal universe **265**; EODHD EGX list **239**. **VERIFIED_EXACT: 225 (84.9%)**;
NON_EQUITY 0; DUPLICATE 0; **NOT_FOUND: 40**; extra EODHD symbols not in the universe:
14. Report: `reports/eodhd_symbol_mapping.csv`.

**4. Symbols requiring manual review.**
- **40 NOT_FOUND** internal symbols (no EODHD EGX code): ACRO, ADRI, AIFI, AIHC, ALEX,
  AMPI, APPC, BIDI, BIGP, DCRC, DIFC, EGX30ETF, EITP, ELWA, ESAC, ESRS, FCMD, FIRE,
  FNAR, GOCO, GTHE, HCFI, IBCT, INEG, IRAX, MISR, MKIT, MMAT, NBKE, NCGC, PACH, RKAZ,
  RMTV, SMPP, SNFI, SUCE, TORA, UASG, UPMS, VERT (likely delisted, renamed, or a
  different EODHD code — resolve before relying on EODHD for them).
- **ORAS** — mapped by code but the price series diverge ~10× (see Q6/Q7): treat as
  **MANUAL_REVIEW**, do not trust interchangeably yet.

**5. EODHD vs Yahoo freshness.**
Identical: for all five pilot symbols the latest completed session is **2026-07-22** on
both EODHD and Yahoo — consistent with the EGX calendar (07-23 is the Revolution Day
holiday). No freshness disadvantage from EODHD.

**6. OHLC differences.**
- **COMI, SWDY, FWRY, TMGH (recent 30-session & 1-year):** mean close difference
  **0.02%–0.4%**, frequently bit-identical (e.g. COMI 2026-07-22 close 140.00 on both).
  EODHD OHLC matches Yahoo for the windows the strategy actually uses.
- **COMI maximum history:** large divergence only in **deep history** (2010: EODHD
  55.94 vs Yahoo 5.86, ≈9.5×) — a **stock-split adjustment-convention difference** in
  the raw `close` field (EODHD keeps historical unadjusted prices; Yahoo back-adjusts).
  Recent history is identical. Use `Adj Close` for long-range cross-source comparison.
- **ORAS:** EODHD ≈ **713 EGP** vs Yahoo ≈ **71.05** (≈10×) even recently → an
  instrument/scale/currency mismatch (Yahoo `ORAS.CA` appears stale/mis-scaled).

**7. Volume differences.**
Recent volumes largely agree (COMI 2026-07-22: 3,972,917 on both). Deep-history and
window-edge rows differ. **ORAS Yahoo volume is 0** across the window while EODHD shows
real volume — another signal that Yahoo's ORAS series is unreliable and EODHD is likely
the better source there, pending mapping review.

**8. Adjusted-close differences.**
For the recent windows `Adj Close == Close` on both (no corporate actions in-window).
In COMI deep history the `adj_ratio_gap` is non-zero (~0.64), confirming the split-
adjustment convention difference. The provider preserves raw vs adjusted close so this
is measurable rather than hidden.

**9. API calls used and cache hits.**
Full pilot: **11 live EODHD calls** first run (`user`, `exchange-symbol-list/EGX`, and
`eod/*` per symbol/window), then **0 live / 11 cache hits** on rerun. Persistent disk
cache (`data/eodhd_cache/`) means Streamlit reruns cost nothing. Daily limit is 100,000
— negligible usage.

**10. Is EODHD safe to make primary?**
**Not yet — recommend staying in shadow.** For the liquid blue chips (COMI, SWDY, FWRY,
TMGH) EODHD matches Yahoo on recent data with identical freshness and is a strong
candidate. But two classes of issue must be resolved first: (a) the **40 unmapped
internal symbols**, and (b) **ORAS-type instrument/scale mismatches** (dual listings /
currency). A promotion should be gated on a clean full-universe mapping + a widened
multi-symbol OHLC agreement pass, not this 5-symbol pilot alone.

**11. Should Yahoo remain temporary fallback?**
Yes — Yahoo should remain the active provider now and, after any future promotion,
remain the fallback for a probation period. Note the pilot also found Yahoo weaknesses
(ORAS: 10× off, zero volume), so the eventual target may be **EODHD primary with Yahoo
fallback**, but only after full-universe validation.

**12. Was Rubix unchanged?**
Yes. Rubix remains the live intraday provider; none of its code, config, or the live
path was touched. `scanner_provider`/`dashboard_provider`/`forward_testing_provider`
stay `rubix`.

**13. Full regression result.**
**515 passed** (494 prior + 21 new: env loader, symbol mapping, canonicalization, client
token-safety/cache-key, provider modes). No prior test changed behavior.

---

## Artifacts
- `reports/eodhd_symbol_mapping.csv` (+ `_summary.json`) — full 265-symbol mapping.
- `reports/eodhd_shadow/{SYMBOL}_{window}.csv` — per-row EODHD vs Yahoo diffs.
- `reports/eodhd_shadow/shadow_summary.csv` / `.json` — per-window summary + API/cache
  counts + provider mode.

## Safety confirmation
`.env` gitignored; token never printed/logged/returned (masked fingerprint only);
Yahoo remains the active historical provider; Rubix unchanged; no strategy / score /
threshold / indicator / TP-SL / paper / production / execution change;
`production_enabled=false`. **Stopping after the five-symbol shadow validation — the
active historical provider will not be switched until you review this report.**
