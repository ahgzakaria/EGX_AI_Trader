# Rubix Authentication Assistant — Implementation Report

## Scope and outcome

The Rubix collector remains an independent external process and authentication
remains manual. The production launcher now provides a guided, one-window
workflow, validates the selected temporary frame safely, runs a preflight check,
and displays operational states in language suitable for a non-programmer.

No strategy, AI, walk-forward, ranking, indicator, portfolio, risk, replay,
backtest, forward-testing, scalping, provider, SQLite schema, or experiment
tracking calculation was changed.

## Files changed

- `services/rubix_auth_assistant.py` — secret-free validation, preflight, and
  allowlisted preference persistence.
- `scripts/launch_rubix_production.py` — Authentication Assistant UI, Self
  Check, operational status cards, friendly errors, safe preferences, preferred
  port/theme, and optional delete-on-stop behavior.
- `tests/test_rubix_auth_assistant.py` — synthetic security, validation,
  preflight, persistence, and UI-contract tests.
- `tests/test_rubix_production_rollout.py` — existing launcher validation fixture
  updated to use a synthetic JSON authentication envelope.
- `AUTHENTICATION_ASSISTANT_GUIDE.md` — daily workflow, screenshot
  placeholders, mistakes, troubleshooting, and security FAQ.
- `RUBIX_AUTHENTICATION_ASSISTANT_REPORT.md` — this report.

## Assistant workflow

1. The user opens and signs in to the official Rubix website manually.
2. The launcher explains how to locate `/websocket/price` in DevTools.
3. The user copies only the outbound authentication message into a temporary
   JSON/text file.
4. Browse selects the file; its visible state becomes `VALID`, `EXPIRED`,
   `INVALID`, or `MISSING`.
5. Self Check verifies Python, the project virtual environment, SQLite, the
   database path, authentication file, age, port/configuration, collector, and
   Streamlit.
6. Start Rubix launches the existing independent supervisor and waits for
   acknowledged authentication plus fresh quotes.
7. The monitor shows Rubix, authentication, collector, database, freshness,
   coverage, latest quote, provider, fallback, market, and overall health.
8. Yahoo-only fallback remains explicit and is labelled research-only/non-live.

## Validation logic

The selected file must exist, be readable, non-empty, no larger than 1 MB, no
older than 15 minutes, and contain a recognized price-authentication envelope.
The validator supports the adapter's documented Rubix delimiter-based format
(`0x02` tag/value and `0x1c` field separators) as well as compatible JSON
envelopes. Heartbeats, metadata/quote frames, subscription messages, unknown
structures, invalid timestamps, and malformed data are rejected with friendly
messages.

Validation reads the JSON only into local memory. Returned state contains only
safe metadata: status, message, path, filename, age, size, and structure class.
It never contains a payload or field value.

## Security review

- Authentication remains manual.
- No login automation exists.
- No cookies, browser storage, credentials, or sessions are read.
- No tokens are extracted or intercepted.
- No Selenium, Playwright, DevTools automation, browser extension, packet
  interception, or browser control exists.
- No authentication bypass or session replay exists.
- Authentication contents are not printed, logged, copied, archived, or saved
  in launcher settings.
- Preferences use an explicit allowlist: adapter path, database path, last auth
  folder, window geometry, theme, port, and non-secret recent launch outcomes.
- Optional temporary-file deletion is OFF by default and occurs only after the
  collector is stopped, preserving reconnect behavior.

## Tests and validation

- Targeted authentication/launcher tests: **23 passed**.
- Full regression suite: **135 passed**.
- Static compile/import check: **passed**.
- Native Windows launcher UI smoke: **RUBIX_LAUNCHER_UI_SMOKE_OK**.
- Streamlit smoke test: **STREAMLIT_SMOKE_OK**.

All tests use synthetic authentication values. No real authentication material
was read or written during validation.

## Regression status

Trading and market-data provider calculations were not modified. The complete
test suite remains green, so the frozen engine behavior and existing provider
contract are unchanged by this usability-only phase.

## Final conclusion

1. Authentication is still manual.
2. No login automation exists.
3. No cookies are read.
4. No tokens are extracted.
5. No browser automation exists.
6. No authentication bypass exists.
7. The daily process is now simpler, clearer, and safer.
