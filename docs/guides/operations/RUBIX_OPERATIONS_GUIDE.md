# Rubix Production Operations Guide

## Safety boundary

EGX AI Trader never connects to Rubix. The independent `rubix_feed` adapter writes SQLite; the application reads that database. The launcher and supervisor do not extract browser state, cookies, passwords, tokens, or local storage.

Rubix is the only provider permitted for operational/live actionability. Yahoo is a clearly labelled daily research fallback and must never be treated as live.

## Start

1. Open the authenticated Rubix session.
2. Export the current price WebSocket authentication frame into a temporary local text file. Do not paste its content into EGX AI Trader.
3. Double-click `scripts\start_rubix_production.bat`.
4. Use **Browse** to select the fresh file (maximum age 15 minutes), then select the collector folder and production database. The auth field is intentionally read-only.
5. Press **Start**.

The launcher starts `rubix_collector_supervisor.py`, waits for acknowledged authentication, a valid schema, fresh quotes, and symbol coverage, then starts Streamlit.

If readiness fails, the UI offers exactly three actions: Retry Rubix, continue with Yahoo research-only, or cancel. It cannot claim Rubix is active without recent quotes.

## Supervisor behavior

The supervisor restarts a crashed collector with bounded backoff. The external adapter remains responsible for WebSocket connection, authentication, subscription, reconnect, and resubscription. The supervisor records only redacted JSONL events, heartbeat health, restart count, database integrity, passive WAL checkpoint results, and session coverage.

Files:

- `data/rubix_supervisor.pid.json`
- `data/rubix_supervisor_health.json`
- `logs/rubix_supervisor.jsonl` with size-based rotation
- `data/rubix_live_market.db`

The user-supplied auth file is not copied, stored in settings, or printed. It is retained by default because a child restart needs the same file. Delete it after shutdown or after the session has expired. Automatic deletion is deliberately not enabled until the external collector can accept an in-memory credential handoff safely.

## Stop and recovery

Use **Stop** or close the launcher. Streamlit and the supervisor receive graceful termination; remaining child processes are killed only after timeout. The database and logs remain.

Run `venv\Scripts\python.exe scripts\system_health.py` before a session. `HEALTHY` requires fresh Rubix plus a healthy Forward database and backup. `RESEARCH_ONLY` means Yahoo/cache is available but live actionability is prohibited.

## Current production gate

No real-money approval is granted by this project. A fresh Rubix session, nonzero received/updating symbol coverage, successful backup, and forward-test acceptance are still required.
