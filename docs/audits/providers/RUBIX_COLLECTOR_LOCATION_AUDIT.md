# Rubix Collector Location Audit (Phase 0)

**Result: collector source FOUND and AVAILABLE.** Root-cause code repair is
therefore possible (see the stability report). Discovery was read-only; no file
was edited during Phase 0.

## Location & identity

| Item | Value |
|---|---|
| Collector path | `C:\Users\ahgza\OneDrive\Documents\Scrapping\rubix_feed\` |
| Source code available | **Yes** — 10 Python modules |
| Language / runtime | Python (async, `websockets`), run via `D:\EGX_AI_Trader\venv\Scripts\python.exe` |
| Entry point | `python -m rubix_feed.cli` |
| Launch command | `... python.exe -m rubix_feed.cli --url wss://eg-feed3.mubashertrade.com/websocket/price --auth-frame-file <REDACTED> --database D:\EGX_AI_Trader\data\rubix_live_market.db --symbol EXCHANGE~SYMBOL (×265) --subscription-batch-size 100 --authenticated-session` |
| Working dir | project/venv context (module run) |
| Database output | `D:\EGX_AI_Trader\data\rubix_live_market.db` (WAL) — the same DB EGX AI Trader reads `mode=ro` |
| Upstream feed | `wss://eg-feed3.mubashertrade.com/websocket/price` (Mubasher) |
| Source files | `adapter.py` (connection/state machine), `protocol.py` (Mubasher message parse + heartbeat/subscription frames), `storage.py` (SQLite writer), `cli.py` (entry), `models.py`, `handshake_probe.py`, `validation_checks.py`, `security_scan.py`, `validation_report.py`, `__init__.py` |

## Authentication mechanism

- Auth is a **pre-exported authenticated-session frame** in a file
  (`--auth-frame-file`, located under `C:\secure-temp\...`). The adapter reads
  this frame and sends it as the first WebSocket message; it contains **no login
  code** and performs no credential entry. **Its contents are a credential and
  were NOT read or logged** during this audit — only its existence/path role.
- `cli.py` requires an explicit `--authenticated-session` flag confirming the
  frame came from the user's own current legal login. The adapter refuses any
  URL that is not Mubasher's `/websocket/price` endpoint.
- Auth expiry is only surfaced today as a `PermissionError`/timeout inside
  `_wait_for_auth` (which becomes a generic reconnect), not as an explicit
  "auth expired, user action required" signal (addressed in the fix/report).

## Supervisor / restart mechanism

- Supervisor process **PID 14824** (running) → launches collector **PID 30216**
  → worker child **PID 16036** (a Python `-m` parent/worker pair, i.e. one
  logical collector, not two independent duplicate sessions).
- Supervisor scripts live in `EGX_AI_Trader_RC1/scripts/`
  (`rubix_collector_supervisor.py`, `launch_rubix_production.py`).
- Supervisor writes `data/rubix_supervisor.pid.json` and
  `data/rubix_supervisor_health.json` (health snapshot: collector_status,
  auth status, symbols_received, latest timestamps).

## Log locations

- Structured collector telemetry is written into the DB `feed_metrics` table
  (not a text log): `connected, disconnect, reconnect_success, heartbeat_sent,
  heartbeat_received, subscription_batch_sent, stale_start/…`.
- Supervisor emits a rotating structured log (`StructuredLog`) per its config.

## Current runtime state (at audit time)

Collector is running (PIDs 30216/16036), 265 symbols subscribed, DB
`READ_ONLY_OK`. Market is **closed** (past midnight Cairo), so the feed is
idle/stale — this is expected and is not itself the bug.

## Conclusion

Source is present ⇒ this proceeds as a **collector root-cause investigation and
fix**, not supervisor-only mitigation. The state-machine analysis and the
specific defect are documented in
[RUBIX_FEED_CONNECTION_STABILITY_REPORT.md](RUBIX_FEED_CONNECTION_STABILITY_REPORT.md).
Because the market is closed, the fix can be unit-tested against a mock socket
now but **cannot be live-validated until the next EGX session** — and per the
task rules, success is not claimed without that live test.
