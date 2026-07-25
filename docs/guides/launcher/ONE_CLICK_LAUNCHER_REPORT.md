# EGX AI Trader One-Click Launcher Report

Date: 2026-07-13 (Africa/Cairo)

## Outcome

EGX AI Trader can now be started from one double-click without opening VS Code
or manually managing multiple PowerShell windows. The launcher owns the
TickerChart collector and Streamlit child processes, displays their operational
state, opens the Dashboard, and cleans up both process trees on Stop or launcher
exit.

The current saved Streamer URL was supplied explicitly by the user from DevTools.
The user must refresh it when TickerChart assigns a new `wss://.../streamhubws/`
or `wss://.../ws/` URL from the already authenticated
TickerChart Network > WS view and paste it into the launcher before pressing
Start. No browser/session extraction is implemented.

## Files added

- `scripts/launch_egx_ai_trader.py`
- `scripts/run_tickerchart_collector.py`
- `scripts/start_egx_ai_trader.bat`
- `config/launcher_settings.json`
- `tests/test_one_click_launcher.py`
- `ONE_CLICK_LAUNCHER_REPORT.md`

## Files modified

- `core/data_provider.py`: accepts a launcher-provided live freshness window
  from `TICKERCHART_STALE_AFTER_MINUTES`; backtest routing remains unchanged.
- `requirements.txt`: adds the adapter's declared supported dependency range,
  `websockets>=12,<16`.

No strategy, AI, walk-forward, backtest, ranking, portfolio, indicator, entry,
exit, position-sizing, or risk calculation was modified.

## Launcher UI

The local Tkinter UI contains:

- TickerChart Streamer URL input;
- saved adapter path with Browse;
- saved SQLite database path with Browse;
- Streamlit port;
- browser auto-open preference;
- Start;
- Stop;
- Open Dashboard;
- actual provider and fallback status;
- collector and Streamlit PID/alive status;
- last received quote time;
- database freshness;
- redacted log preview.

The UI performs blocking collector/Streamlit startup work on a background
thread. Tk remains responsive while waiting for database creation and a new
quote.

## Exact double-click instructions

1. Sign into TickerChart normally.
2. In the browser developer tools, open Network > WS.
3. Copy the current TickerChart `wss://.../streamhubws/` (or `/ws/`) streamer URL.
4. Double-click:

   `D:\EGX_AI_Trader\scripts\start_egx_ai_trader.bat`

5. Paste the URL into **Streamer URL**.
6. Confirm the pre-filled adapter/database paths.
7. Press **Start**.

The BAT file switches to the project directory and starts the launcher with the
existing `venv\Scripts\pythonw.exe`, so no console window or VS Code is needed.
It may be pinned or used as the target of a normal Windows desktop shortcut.

## Startup workflow

1. Validate `app.py`, `data/symbols.csv`, and the existing virtual environment.
2. Validate `adapter.py`, `protocol.py`, and `storage.py` in the saved adapter
   directory.
3. Run the external collector's real `--help` preflight using the project venv
   to prove its dependencies/imports work.
4. Validate the SQLite destination and create its parent directory.
5. Validate and save only whitelisted launcher configuration.
6. Convert every symbol in `data/symbols.csv` through the centralized
   TickerChart symbol mapper.
7. Start the external read-only collector as a hidden child process.
8. During an open EGX session, wait up to 45 seconds for a recent quote received
   **after the collector process started**. Outside regular session hours, a
   new adapter `connect_success` permits the application to start in the
   truthful `TICKERCHART_WAITING_FOR_SESSION` state; it does not claim that
   TickerChart data is active.
9. Start Streamlit as a second hidden child process.
10. Wait for the local Streamlit port to accept connections.
11. Open the Dashboard when auto-open is enabled.

TickerChart is labelled active only when the collector process is alive and the
database contains a session-valid quote. Closed-market time no longer makes the
last completed-session quote stale merely because five wall-clock minutes have
passed. Its UI label remains **delayed 15 minutes**.

## Failure and fallback behavior

If the collector exits, or no qualifying quote arrives while EGX is open, the
launcher presents exactly three choices. Before open, after close, or on the
regular weekend, a newly connected collector starts the Dashboard and waits for
the next quote instead of presenting a false failure:

- **Yes / Continue with Yahoo fallback**: stop the failed collector and launch
  Streamlit without the TickerChart database environment variable. A recent
  snapshot from a dead process cannot be reused accidentally.
- **No / Retry collector**: clean up the prior process and start a fresh attempt.
- **Cancel**: stop all child processes and return to the idle launcher.

The provider and fallback reason remain visible in the launcher. The existing
Dashboard provider disclosure remains intact.

Current real unavailable-provider smoke result:

| Symbol | Effective provider | Fallback | Reason |
|---|---|---|---|
| COMI.CA | Yahoo | Yes | TICKERCHART_DB_PATH is not configured |
| SWDY.CA | Yahoo | Yes | TICKERCHART_DB_PATH is not configured |
| FWRY.CA | Yahoo | Yes | TICKERCHART_DB_PATH is not configured |

The application remained stable and returned valid Yahoo history for all three.

## Production WebSocket observation

Chrome DevTools showed the actual signed-session endpoint as:

`wss://delayedkse2.tickerchart.net/streamhubws/`

This corrected the earlier audit assumption that every streamer path ends in
`/ws/`. The launcher now accepts only the two observed/approved exact paths:
`/ws/` and `/streamhubws/`, still restricted to TickerChart domains.

The external adapter itself retained its old narrow validator. Rather than copy
or edit its protocol implementation, `scripts/run_tickerchart_collector.py`
loads the existing adapter and replaces only that URL validator with the
launcher validator. Protocol decoding, subscriptions, storage, reconnects, and
all market-data behavior remain in the original adapter.

An actual read-only connection test produced:

- HTTP `101 Switching Protocols`;
- collector state `OPEN`;
- subscriptions for `COMI.EGY`, `SWDY.EGY`, and `FWRY.EGY`;
- successful reconnect after the adapter heartbeat timeout.

The test was performed while EGX was closed. The quote stream sent no new QO
frames after subscription, so SQLite correctly remained without usable market
data. The launcher now reports `TICKERCHART_WAITING_FOR_SESSION`; Scanner data
temporarily comes from Yahoo and is explicitly labelled fallback until the
first TickerChart quote is stored. The launcher never claims TickerChart active
merely because the WebSocket handshake succeeded. The test collector was
stopped cleanly.

## Session-aware freshness correction

Raw minute-age is only meaningful while EGX is open. Operational freshness now
uses Cairo time and the regular Sunday-Thursday, 10:00-14:30 session:

- during the open session, the configured minute threshold remains enforced;
- before open, after close, and on Friday/Saturday, a quote from the latest
  expected completed session remains usable;
- a connected collector with zero quotes is `WAITING_FOR_SESSION`, never
  `ACTIVE`;
- Yahoo daily fallback age is disclosed in completed EGX sessions, not hidden
  behind its successful download timestamp.

The calendar helper intentionally does not invent exchange holidays. If an
official holiday occurs, the alive collector/waiting state remains visible and
the prior Yahoo daily candle is clearly marked with its session lag.

## Stop and orphan-process protection

Stop performs the following order:

1. terminate Streamlit and wait for graceful completion;
2. terminate the TickerChart collector and wait;
3. use Windows `taskkill /T /F` only when a process refuses to terminate;
4. close output streams and log pumps;
5. preserve SQLite and log files.

Both children are also assigned to a Windows Job Object configured with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. If the launcher is closed or crashes, the
operating system closes the job and terminates remaining child processes.

## Configuration persistence

`config/launcher_settings.json` is written atomically and permits only:

- adapter path;
- database path;
- streamer URL;
- Streamlit port;
- browser auto-open preference.

Unknown keys are discarded. Tests prove that password, cookie, and token fields
cannot persist through the configuration API.

The provided file currently contains the known local adapter/database paths,
port 8501, browser auto-open enabled, and the current explicitly supplied
`delayedkse2.tickerchart.net/streamhubws/` Streamer URL.

## Security decisions

- No cookies, credentials, browser storage, local storage, authentication
  frames, passwords, or tokens are read or exported.
- Only `wss://` is allowed.
- Hosts must be exactly `tickerchart.net`, `tickerchart.com`, or a subdomain of
  one of those domains. Lookalike suffixes are rejected.
- The path must be exactly `/ws/` or the production-observed `/streamhubws/`.
- Embedded URL usernames/passwords are rejected.
- Query keys commonly used for credentials/tokens (`token`, `auth`, `session`,
  `signature`, `key`, etc.) are rejected and therefore cannot be saved.
- Any permitted query parameters are removed before URLs are written to
  persisted logs or the log preview.
- Child stdout/stderr is continuously drained through a redacting log pump,
  preventing pipe deadlocks and unredacted query-string persistence.
- The adapter database is preserved and read by EGX AI Trader using the
  previously implemented read-only provider interface.

## Dependency validation

The external adapter declares `websockets>=12,<16`. The project environment
initially contained 16.0, outside that supported range. The production
requirements now declare the same adapter constraint and the existing venv was
synchronized to `websockets 15.0.1`.

- Adapter CLI preflight: passed.
- `pip check`: no broken requirements.
- Streamlit after dependency synchronization: health endpoint returned `ok`.

## Tests and validation

- Targeted launcher/provider tests: **26 passed**.
- Full project suite: **68 passed**.
- Python syntax/import compilation: passed.
- BAT/Tk UI smoke test: passed and closed automatically.
- Adapter CLI preflight: passed.
- Streamlit smoke test: passed on temporary ports; servers were stopped.
- Collector unavailable behavior: passed.
- Yahoo fallback on COMI/SWDY/FWRY: passed.
- URL validation, hostile host rejection, and sensitive query rejection: passed.
- Path/project/adapter validation: passed.
- Atomic configuration whitelist/persistence: passed.
- Child startup/PID/alive reporting: passed.
- Recent/stale/missing database detection: passed.
- Startup timeout and current-process quote requirement: passed.
- Fallback/retry/cancel selection: passed.
- Normal and forced process-tree cleanup: passed.
- Persisted log URL redaction: passed.

## Trading regression validation

Phase 5 metrics reproduced exactly after implementation:

| Mode | Trades | Net profit | Return | Profit Factor | Maximum Drawdown |
|---|---:|---:|---:|---:|---:|
| Strategy Only | 730 | 65,811.22 | 65.81% | 1.28 | 17.91% |
| AI Ranking Only | 736 | 71,618.87 | 71.62% | 1.30 | 16.48% |

Coverage remained 2020-08-06 through 2026-06-08. No validated trading metric
changed.

## Optional EXE

An EXE was not built because PyInstaller is not currently installed and the BAT
launcher already provides the requested one-double-click workflow without
introducing another packaging/runtime dependency. The launcher remains isolated
and can be packaged later without bundling the Streamlit project.

## Known limitation

TickerChart assigns the streamer URL dynamically. The user must still copy that
URL manually from the authenticated Network > WS view and paste it into the
launcher. This is intentional: automating discovery would require browser/session
access that the security boundary explicitly prohibits.
