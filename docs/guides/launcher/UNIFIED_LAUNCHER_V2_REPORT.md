# Unified Launcher V2 — Remaining Yahoo Operational Paths Removed

Every operational entry point now opens the **same** Rubix Production Launcher V2:
**EODHD** current research · **Rubix** live intraday · **frozen Yahoo snapshot for legacy
backtests only (no network)** · production disabled. No launcher starts Yahoo or offers a
Yahoo fallback. **616 tests pass;** both `.bat` entry points pass `--check` and resolve to
the V2 launcher; the dashboard opens.

## 1. Entry-point inventory (`reports/launcher_entry_point_inventory.csv`)

| entry_point | target_script | research | live | yahoo_operational_path | status |
|---|---|---|---|---|---|
| `start_rubix_production.bat` | `launch_rubix_production.py` | EODHD | Rubix | none | UNIFIED_V2 |
| `start_egx_ai_trader.bat` | `launch_rubix_production.py` | EODHD | Rubix | none | UNIFIED_V2 (repointed) |
| `launch_rubix_production.py` | itself | EODHD | Rubix | none | PRIMARY_V2 |
| `launch_egx_ai_trader.py` | `launch_rubix_production.py` | EODHD | Rubix | none | DEPRECATED_REDIRECT |

Before this task, `start_egx_ai_trader.bat` → `launch_egx_ai_trader.py`, a divergent
TickerChart GUI whose failure dialog offered "Continue with Yahoo fallback"
(`failure_choice(True) == "fallback"`) and whose status panel showed a Yahoo provider.

## 2. What changed
- **`scripts/start_egx_ai_trader.bat`** — repointed to `launch_rubix_production.py`; carries
  the same `--check` behavior and a deprecation banner. `--smoke-test` maps to the V2
  launcher's `--check`.
- **`scripts/launch_egx_ai_trader.py`** — the interactive TickerChart **+ Yahoo-fallback**
  `LauncherUI` class (330 lines), `failure_choice`, and `_ask_failure` were removed. `main()`
  is now a thin redirect to `scripts.launch_rubix_production.main()`. The TickerChart
  collector **utility** functions/classes (`validate_streamer_url`, `inspect_database`,
  `ProcessSupervisor`, `wait_for_recent_quote`, `LauncherError`, …) are kept because
  `run_tickerchart_collector.py` and `tickerchart_health.py` and their tests import them —
  they are not a Yahoo operational path.
- **Tests** — removed the obsolete `failure_choice(...) == "fallback"` test; added redirect +
  unification tests (`test_one_click_launcher.py`, `test_launcher_current_research_v2.py`).

There is **one** launcher implementation of startup, locking, Streamlit start, stop, and
status rendering (`launch_rubix_production.py`); the second entry point delegates to it, so
there are no longer two divergent startup code paths.

## 3. Consistent buttons
Both entry points now present the V2 launcher's buttons: **Start Rubix & App · Start
Research Only · Stop · Refresh Status · Open Dashboard**. The old TickerChart launcher's
single **Start** (with a Yahoo-fallback failure dialog) is gone.

## 5. Failure behavior (from the V2 launcher, applies to both entry points)
- **EODHD unavailable, cache current** → Cache Mode (current research served from validated
  cache).
- **EODHD unavailable, no cache** → Diagnostics Only; current research blocked
  (DATA_UNAVAILABLE); never Yahoo.
- **Rubix unavailable** → offer **Start Research Only (EODHD)**; never Yahoo.
- **Holiday/weekend** → Research Only allowed, Rubix not required, no stale-tick warning
  (central EGX calendar).

## 6. Tests
Added/updated:
- `test_one_click_launcher.py::test_one_click_launcher_redirects_to_unified_v2_launcher` —
  `failure_choice` and `LauncherUI` are gone; `main` redirects to the V2 launcher.
- `test_launcher_current_research_v2.py` — both `.bat` files reference
  `launch_rubix_production.py`; the one-click module has no Yahoo fallback dialog/choice; no
  launcher contains "Start Yahoo Only"; `launch_egx_ai_trader.main(["--check"])` delegates to
  the V2 `main` without starting Yahoo.
Retained: duplicate-process prevention, Stop-safety (targeted PID kills, no
`taskkill /F python.exe`), production-disabled, and the full TickerChart-utility suite.

## 7. Final verification
- `scripts\start_rubix_production.bat --check` → VENV/LAUNCHER/SUPERVISOR/APP OK.
- `scripts\start_egx_ai_trader.bat --check` → VENV/LAUNCHER/SUPERVISOR/APP OK (same V2 output).
- Both open the V2 launcher showing **Current Research: EODHD · Live Provider: Rubix ·
  Legacy: Frozen Yahoo Snapshot — Backtest Only · Production: Disabled**.
- Final source sweep: no "Start Yahoo Only", no "Continue with Yahoo fallback", no
  `failure_choice` in any launcher; the only remaining "Yahoo" strings are disclaimers
  ("no operational Yahoo provider or fallback") and the legacy backtest label.

## The 8 confirmations
1. **Both startup entry points use the unified V2 launcher?** **Yes** — both `.bat` files run
   `launch_rubix_production.py`, and `launch_egx_ai_trader.py` redirects to it.
2. **All Yahoo operational startup paths removed?** **Yes** — the Yahoo-fallback UI, dialog,
   and `failure_choice` are deleted; no operational Yahoo path remains in any launcher.
3. **EODHD is the only current-research provider?** **Yes.**
4. **Rubix remains live?** **Yes** — unchanged.
5. **Frozen Yahoo remains legacy-only?** **Yes** — backtest reproduction only, no network; no
   snapshot was modified.
6. **No Yahoo network call in startup?** **Yes** — startup status is cache-only/offline; a
   regression test injects an exploding `YahooProvider` and asserts the launcher never
   constructs it.
7. **All tests pass?** **Yes** — 616 passed.
8. **Production remains disabled?** **Yes** — `production_enabled=false`,
   `automatic_execution=false`, `broker_orders_enabled=false`.

## Honest notes
- **Verification method:** the launchers are Tk desktop GUIs, which I can't render in this
  environment, so I verified via `--check` on both `.bat` files, the headless redirect/contract
  tests, and (in the prior task) a live browser render of the dashboard the launcher opens. I
  did not visually screenshot the Tk window.
- **TickerChart utilities kept:** I deprecated the one-click launcher's *UI* but preserved its
  TickerChart collector helper functions, because other tooling imports them. TickerChart is a
  separate legacy feed integration, not a Yahoo path; removing that infrastructure was out of
  scope. If you want the TickerChart collector tooling retired too, that is a separate,
  clearly-scoped cleanup.
