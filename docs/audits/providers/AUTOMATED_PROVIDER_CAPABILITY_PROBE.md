# Automated EGX Provider Capability Probe (Phase 1)

**Result: BLOCKED_NO_API_KEY for both providers.**
**No real API request was made to EODHD or Twelve Data in this session.**
**No capability, freshness, or reconciliation claim below is fabricated —
every field that would require a live response is marked BLOCKED.**

---

## Credential check performed

```
$ echo $EODHD_API_TOKEN        -> empty
$ echo $TWELVEDATA_API_KEY     -> empty
PS> $env:EODHD_API_TOKEN       -> not set
PS> $env:TWELVEDATA_API_KEY    -> not set
PS> [Environment]::GetEnvironmentVariable('EODHD_API_TOKEN','User')    -> null
PS> [Environment]::GetEnvironmentVariable('TWELVEDATA_API_KEY','User') -> null
```

No `.env` file, settings file, or any other credential store in the repo
contains either key (checked; none found — this project's own safety rules
already forbid storing keys in source or config, and none were found there
either).

## EODHD — BLOCKED_NO_API_KEY (this session)

| Field | Status |
|---|---|
| API reachable | **Not tested** — no token available this session |
| Credentials valid | **BLOCKED_NO_API_KEY** |
| Symbol resolved | Not tested |
| Official returned symbol | Not tested |
| Exchange/MIC | Not tested |
| Currency | Not tested |
| Available intervals | Not tested |
| Historical depth | Not tested (see prior-session note below) |
| Latest completed daily candle | Not tested |
| Latest timestamp | Not tested |
| Current forming candle distinguishable | Not tested |
| OHLCV fields available | Not tested |
| Adjusted vs unadjusted | Not tested |
| API plan restriction | Not tested |
| Request-credit usage | Not tested |
| Rate-limit headers/errors | Not tested |

**Important context — this is NOT a fresh unknown.** A prior session already
did real, credentialed EODHD testing and left a report:
[EODHD_PHASE1_REPORT.md](EODHD_PHASE1_REPORT.md) (dated 2026-07-18) and a
working adapter at [providers/eodhd_provider.py](../../../providers/eodhd_provider.py).
That report's real findings (quoted, not re-tested here):

- A **free-tier token** was used; the account dashboard showed **20/20 daily
  API calls consumed, "Not Active", no extra calls remaining** by the end of
  that session.
- Of 265 project symbols, **241 resolved** in EODHD's EGX symbol lookup, **167
  histories were returned** before the quota ran out, **98 were
  connection-limited** (quota exhaustion, not proven unsupported).
- **Free-tier historical depth was ~1 year** (earliest date observed
  2025-07-20) — **none of the 167 returned histories reached the engine's
  250-bar minimum**, so even fully mapped symbols could not feed Swing/Daily
  under the free plan.
- The report explicitly states: *"The API token supplied in chat should be
  rotated before production use."* — i.e. that token must be treated as
  already compromised/retired, not reused.

**Conclusion for EODHD today:** even if that same free token still existed,
it would need daily-quota reset and would still be architecturally
insufficient for 10-year history under free-tier limits. This session has no
token at all, so the honest status is **BLOCKED_NO_API_KEY**, and a **paid
plan token** is required before any further real testing is meaningful (a paid
plan may have entirely different depth/rate limits than what was observed).

## Twelve Data — BLOCKED_NO_API_KEY

| Field | Status |
|---|---|
| API reachable | **Not tested** — no key available |
| Credentials valid | **BLOCKED_NO_API_KEY** |
| Symbol resolved | Not tested |
| Official returned symbol | Not tested |
| Exchange/MIC | Not tested |
| Currency | Not tested |
| Available intervals | Not tested |
| Historical depth | Not tested |
| Latest completed daily candle | Not tested |
| Latest timestamp | Not tested |
| Current forming candle distinguishable | Not tested |
| OHLCV fields available | Not tested |
| Adjusted vs unadjusted | Not tested |
| API plan restriction | Not tested |
| Request-credit usage | Not tested |
| Rate-limit headers/errors | Not tested |

No prior Twelve Data work exists in this repository at all — this would be a
first-time integration.

---

## Test basket (unresolved — blocked)

```
COMI SWDY TMGH EAST FWRY PHDC TAQA VALU ORAS ORHD
EFID ETEL ABUK AMOC JUFO RAYA HRHO ADIB HELI GBCO
```

Candidate symbol formats to try once keys exist (per each provider's public
documentation, **not yet verified against a live response**):

- **EODHD:** `<CODE>.EGX` (e.g. `SWDY.EGX`) — this format is already confirmed
  working in the prior EODHD_PHASE1_REPORT.md session (real, credentialed test).
- **Twelve Data:** `symbol=<CODE>&exchange=XCAI`, or resolve via Twelve Data's
  official `/symbol_search` endpoint first, per the task's own instruction not
  to assume a mapping. **Unverified — no request has been made.**

---

## What is built vs. deliberately not built

Per this task's own instruction — *"implement only the minimal secure adapter
structure needed... do not build another large unused framework before real
access is confirmed"* — the following boundary was kept:

**Built (safe, minimal, reusable once a key exists):**
- This probe report.
- A minimal `providers/twelvedata_provider.py` structure: reads
  `TWELVEDATA_API_KEY` from the environment only, reports
  `BLOCKED_NO_API_KEY` via `health()`, and refuses to run `load_history()`
  without a real key (raises immediately, never fabricates a response).
- Exact commands below to set both environment variables.

**Deliberately NOT built in this pass** (would be the "large unused
framework" the task warns against, and every one of these downstream phases
requires real data to be meaningful, per *"Do not use synthetic data for the
final verdict"*):
- `reports/automated_provider_symbol_coverage.csv` (Phase 3)
- `reports/automated_provider_freshness.csv` (Phase 4)
- `reports/eodhd_yahoo_reconciliation.csv`, `twelvedata_yahoo_reconciliation.csv`,
  `eodhd_twelvedata_reconciliation.csv`, `api_provider_rubix_reconciliation.csv` (Phase 5)
- Full application-owned incremental cache wiring for these two providers (Phase 7)
- `AUTOMATED_PROVIDER_OPERATIONAL_COST.md` (Phase 8) — cost/credit math needs
  a real plan's documented limits *and* real observed per-request costs; a
  rough desk estimate from public pricing pages could be produced on request,
  but would not satisfy *"Use actual observed API-credit costs."*
- `reports/eodhd_shadow_decisions.csv`, `twelvedata_shadow_decisions.csv` (Phase 9)
- The Phase 10 scorecard and Phase 11 production-architecture wiring — scoring
  providers on data no request has confirmed would itself be a fabrication.

---

## Exact Windows commands to set the required environment variables

**PowerShell — persistent (User scope, survives reboot; open a new terminal
after running this for it to take effect):**

```powershell
[Environment]::SetEnvironmentVariable("EODHD_API_TOKEN", "your-eodhd-token-here", "User")
[Environment]::SetEnvironmentVariable("TWELVEDATA_API_KEY", "your-twelvedata-key-here", "User")
```

**PowerShell — current session only (this terminal, until closed):**

```powershell
$env:EODHD_API_TOKEN = "your-eodhd-token-here"
$env:TWELVEDATA_API_KEY = "your-twelvedata-key-here"
```

**cmd.exe — persistent (User scope):**

```cmd
setx EODHD_API_TOKEN "your-eodhd-token-here"
setx TWELVEDATA_API_KEY "your-twelvedata-key-here"
```

**cmd.exe — current session only:**

```cmd
set EODHD_API_TOKEN=your-eodhd-token-here
set TWELVEDATA_API_KEY=your-twelvedata-key-here
```

After setting either variable, **restart the terminal/session** so the new
process environment picks it up, then this probe can be re-run for a real
result.

---

## Verdict

**Both providers are BLOCKED_NO_API_KEY.** No claim about EGX coverage,
freshness, OHLCV accuracy, cost feasibility, or shadow-decision impact can be
made for either provider in this session. Per the task's own acceptance gates,
**work stops here** pending real credentials. See the accompanying message for
what is needed from you to continue.
