# Rubix Production Launcher V2 — CURRENT_RESEARCH_V2 Alignment

The Rubix Production Launcher (`scripts/launch_rubix_production.py`, the target of
`scripts/start_rubix_production.bat`) now matches the operational architecture: **EODHD**
current research, **Rubix** live intraday, **frozen Yahoo snapshot for legacy backtests
only**. No operational Yahoo button or fallback remains. **612 tests pass; the launcher
self-check passes and the dashboard opens with no console errors.**

## What changed
- **New:** `services/research_launcher_status.py` — secret-free Current Research (EODHD),
  Market, and Safety status. The EODHD token is never returned, logged, or displayed —
  only configured/missing plus a Valid/Unknown/Invalid authentication label.
- **`scripts/launch_rubix_production.py`:**
  - Buttons: **Start Rubix & App**, **Start Research Only**, **Stop**, **Refresh Status**,
    **Open Dashboard**. Removed **Start Yahoo Only**.
  - Removed `start_yahoo_only` / `_finish_yahoo_start` / `_apply_yahoo_start_result` and the
    "Provider = Yahoo fallback" / "Fallback = Yes/No" status fields.
  - Grouped operational panel: **Current Research** (Provider EODHD · Token · Authentication
    · Latest Session · Freshness · Cache), **Live Intraday** (Rubix · Supervisor · Collector
    · Authentication · Coverage · Latest Quote · Quote Freshness · Value Progression),
    **Application** (Streamlit · Dashboard URL · PID · Health), **Market** (Session · Phase ·
    Next Session), **Safety** (Paper · Production · Broker Execution).
  - Legacy label shown only as a footnote: "Frozen Yahoo Snapshot — backtest reproduction
    only, not operational."
- **Tests:** updated `test_rubix_auth_assistant.py` UI contract; added
  `tests/test_launcher_current_research_v2.py` (12 cases).

## The 12 confirmations
1. **Start Yahoo Only removed?** **Yes** — button, method, and all "Start Yahoo Only" text
   are gone (`test_no_start_yahoo_only_button_or_method`).
2. **Yahoo fallback removed?** **Yes** — no `provider=Yahoo` / `fallback` status field; the
   only remaining "Yahoo" strings are disclaimers ("Yahoo is not used") and the legacy
   backtest label (`test_no_yahoo_fallback_status_field`).
3. **Research Only uses EODHD?** **Yes** — Start Research Only loads the environment, verifies
   `EODHD_API_TOKEN`, starts Streamlit only, and shows "current research uses EODHD; live
   data unavailable; intraday opportunities disabled." It never starts Rubix and never uses
   Yahoo (`test_research_only_flow_does_not_start_rubix`).
4. **Rubix remains the live provider?** **Yes** — Start Rubix & App starts exactly one
   supervisor + one collector + Streamlit; the Live Intraday panel is Rubix-only.
5. **Legacy Yahoo snapshots untouched?** **Yes** — no snapshot files were changed; Yahoo
   survives only as the frozen legacy-backtest reference, labelled non-operational.
6. **No Yahoo network call in launcher startup?** **Yes** — startup status is cache-only and
   offline by default; a test injects an exploding `YahooProvider` and asserts the launcher
   status path never constructs it (`test_launcher_startup_status_makes_no_yahoo_call`).
7. **Duplicate-process protection active?** **Yes** — the hardened OS-level single-instance
   lock and `supervisor_status` guard remain; a recorded live supervisor refuses a second
   launch (`test_duplicate_supervisor_is_refused`), and Streamlit refuses an unowned occupied
   port.
8. **Holiday behavior correct?** **Yes** — the central EGX calendar drives the Market panel;
   on a weekend/holiday it shows Market Closed with the next session, Research Only is allowed,
   and a connected+authenticated+stale collector is treated as expected, not a fault
   (`test_holiday_marks_market_closed_and_needs_no_rubix`).
9. **Stop does not kill unrelated processes?** **Yes** — Stop stops only the launcher's own
   supervisor + collector + Streamlit via targeted `taskkill /PID <pid> /T`. There is no
   `taskkill /F python.exe`; the Rubix matcher never matches bare `python.exe` or Streamlit
   (`test_no_broad_taskkill_of_python`, `test_rubix_process_matcher_never_matches_bare_python`,
   `test_stop_only_touches_launcher_owned_processes`).
10. **Production remains disabled?** **Yes** — Safety panel reads `paper_mode=On`,
    `production=Disabled`, `broker=Disabled`; `test_safety_status_reports_production_disabled`
    asserts all three flags false.
11. **All tests pass?** **Yes** — 612 passed (32 launcher-focused).
12. **Launcher and dashboard both open?** **Yes** — `launch_rubix_production.py --check`
    reports VENV/LAUNCHER/SUPERVISOR/APP OK; the Streamlit dashboard renders ("EGX AI Trader
    — Swing/Daily Market Dashboard") with no console errors.

## EODHD failure behavior (Part 10)
- **Token missing:** Research Only shows "EODHD token missing — configure EODHD_API_TOKEN in
  .env", the dashboard may open in diagnostics mode, current analysis shows DATA_UNAVAILABLE,
  and Yahoo is never used.
- **EODHD unavailable but cache current:** the Current Research panel reports Cache = Current
  and data mode CACHE_MODE.
- **EODHD and cache unavailable:** freshness/cache report DATA_UNAVAILABLE; no Yahoo start.

## Rubix failure behavior (Part 11)
On a Rubix failure the launcher offers **Continue with Research Only (EODHD)** — never a Yahoo
fallback — and never implies live quotes exist.

## Honest scope note
This task targeted the **Rubix Production Launcher** (`launch_rubix_production.py`). A separate
one-click launcher, `scripts/launch_egx_ai_trader.py` (run by `start_egx_ai_trader.bat`), still
contains a Yahoo research-only path and its own `failure_choice(...)=="fallback"` test. I did
**not** modify it here to stay within the stated scope and avoid breaking its contract. If you
want the same Yahoo removal applied there, that is a clean, well-scoped follow-up.

## Safety
EODHD token never printed or logged; Rubix auth-frame handling unchanged (contents never
displayed/persisted); no strategy, threshold, or provider-routing change; production,
automatic execution, and broker orders remain disabled.
