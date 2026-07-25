# Rubix Collector Usability Report

Date: 2026-07-14  
Outcome: **SAFE STOP — automatic browser-session attachment was not implemented**

## Executive Conclusion

The requested one-click flow cannot be implemented while simultaneously meeting the stated security requirements and the currently available Rubix interfaces.

The validated external `rubix_feed` adapter does not attach to a browser. It opens an independent Rubix price WebSocket, reads a user-exported authentication frame from a temporary file, sends that frame to the legitimate Rubix endpoint, and then receives market data. No documented local browser bridge, API, SDK, Native Messaging interface, or official session-sharing mechanism is present in the adapter or EGX AI Trader repository.

Automatically reusing the already-open browser session would therefore require at least one prohibited mechanism:

- reading browser cookies, local storage, authorization headers, or in-memory tokens;
- inspecting or intercepting WebSocket frames through DevTools/CDP;
- injecting a browser extension/script that hooks the page WebSocket;
- automating or controlling the authenticated browser;
- copying an authentication frame from browser network traffic.

None of those mechanisms was added. The manual, short-lived authentication-frame export remains the only currently validated connection path.

## Evidence From the Existing Implementation

### External adapter

`C:\Users\ahgza\OneDrive\Documents\Scrapping\rubix_feed\adapter.py`:

- validates the configured `mubashertrade.com` WebSocket endpoint;
- requires `auth_frame_file` to exist;
- reads the authentication frame from that file;
- creates a separate WebSocket connection;
- sends the frame before subscriptions are accepted.

The adapter has no browser attachment, browser IPC, extension, official local service, or session-sharing interface.

### EGX launcher and supervisor

- `scripts/launch_rubix_production.py` requires a fresh authentication-frame file and passes only its path to the supervisor.
- `scripts/rubix_collector_supervisor.py` refuses an empty or expired frame file.
- logs redact common secret-bearing values.
- EGX AI Trader itself reads only the adapter SQLite database and never connects to Rubix.

This is consistent with the existing security boundary and must not be silently replaced by browser inspection.

## Official-Interface Review

No public official Rubix/Mubasher developer documentation was found for a supported browser-to-local market-data bridge or an account market-data API usable by this project.

The official MubasherTrade site states that unauthorized access or use is prohibited and that receipt and use of market data is governed by the user's market-data agreement: [MubasherTrade official site](https://www.mubashertrade.com/?home=1).

The published MubasherTrade customer agreement defines market data and documents password-authenticated access, but does not provide a public browser-session export or developer market-data API: [MubasherTrade customer agreement](https://trdgm-uat.mubashertrade.com/en/forms_applications.html).

Absence from public search is not proof that no commercial API exists. Mubasher/Rubix support may offer an entitled institutional integration privately. Written documentation and account authorization are required before such an interface can be implemented.

## Safe Target Architecture — If an Official Bridge Becomes Available

```text
User opens official Rubix site
        |
User performs manual login
        |
Official documented Rubix local API / SDK / companion
        |  (market data only; no credential export)
External rubix_feed adapter
        |
Read-only SQLite
        |
EGX AI Trader Provider Manager
        |
Dashboard / Scanner / Forward Testing
```

The official bridge would need to guarantee:

- explicit account entitlement for EGX market data;
- documented local attachment or OAuth/session delegation;
- no password, cookie, token, header, or authentication-frame exposure;
- session termination notification;
- market-data schema and timestamps;
- permitted local storage and redistribution terms;
- a supported method to distinguish login, logout, expiration, and market close.

## Session Lifecycle Design

The requested states are valid as a design, but some cannot be truthfully detected without a supported browser interface.

| State | Safe observable evidence today | Status |
|---|---|---|
| `NOT_RUNNING` | Launcher/supervisor process absent | Supported |
| `WAITING_FOR_BROWSER` | A browser process can be observed, but not the authenticated page | Partial and not sufficient |
| `WAITING_FOR_LOGIN` | Requires an official browser/session signal | Not safely observable |
| `CONNECTED` | Collector connected, authentication acknowledged, fresh SQLite quotes | Supported |
| `RECONNECTING` | Supervisor/adapter reconnect events | Supported after valid authentication material was supplied |
| `SESSION_EXPIRED` | Explicit authentication failure/disconnect evidence from adapter | Supported only after connection |
| `MARKET_CLOSED` | EGX session calendar/time state | Supported |
| `STOPPED` | Graceful process shutdown | Supported |

Merely detecting `chrome.exe`, `msedge.exe`, or a Rubix page title would not prove authentication or market-feed entitlement. The application must not label a session `CONNECTED` until the external collector has acknowledged authentication and recent quotes exist.

## Why a Browser Extension Was Not Added

A custom extension would have to inject into the Rubix page or inspect the page's WebSocket traffic to forward quotes. Even if it attempted to discard authentication messages, it would still sit on a channel carrying authentication/session material and would rely on an undocumented internal protocol. That conflicts with the no-interception and no-reverse-engineering boundary and creates account, licensing, and secret-exposure risk.

## Why DevTools/CDP Was Not Added

Chrome DevTools Protocol can observe WebSocket frames only when remote debugging or browser instrumentation is enabled. This would expose the same traffic that contains authentication material and would constitute browser automation/network interception. It was explicitly excluded.

## Current Supported Workflow

1. User signs in manually through the official Rubix website.
2. User opens the market page.
3. User exports a fresh price-WebSocket authentication frame to a temporary local file.
4. User opens `scripts/start_rubix_production.bat`.
5. The launcher validates freshness without logging the payload.
6. The external adapter connects and writes market data to read-only-consumed SQLite.
7. If authentication is missing or expires, Rubix becomes unavailable and EGX AI Trader discloses fallback status.

This workflow is less convenient, but it preserves the verified security boundary.

## Supported Ways to Achieve the Requested One-Click Experience

One of the following must be supplied or approved by Rubix/Mubasher:

1. A documented market-data API with an account entitlement and supported authentication flow.
2. An official browser companion or extension that exposes sanitized market data locally.
3. An official desktop-terminal IPC/DDE/COM/local REST interface.
4. A broker-supplied read-only data SDK with explicit EGX permissions.

After receiving documentation, the project can implement the lifecycle state machine around that official interface without changing the trading engine.

## Files Changed

- Added `RUBIX_COLLECTOR_USABILITY_REPORT.md` only.
- No collector, adapter, launcher, provider, dashboard, strategy, AI, portfolio, backtest, replay, scalping, or SQLite schema code was modified for this feasibility request.

## Regression and Smoke Status

Because the unsafe integration was not implemented, no runtime calculation path changed. The latest project validation before this report remains:

- full regression suite: 126 passed;
- focused workspace/navigation/scalping validation: 31 passed;
- Streamlit smoke test: `STREAMLIT_SMOKE_OK`;
- sealed Phase 8 replay: metrics match `true`;
- Walk-Forward prediction replay: 895/895 match;
- dataset hash: `fafa064f87acf39992f53f3f8bf3b22b09d2ef34fb70b26d53dd932f38704414`.

## Known Limitation

The dynamic authentication frame must still be supplied manually for each fresh Rubix browser session. Removing this step is blocked by the absence of a documented official session-sharing interface—not by the EGX launcher UI.

## Final Confirmations

- Authentication bypass exists: **No**.
- Login automation exists: **No**.
- Browser cookies/tokens/session storage are read or persisted: **No**.
- DevTools, Selenium, Playwright, or browser WebSocket interception was added: **No**.
- Broker execution exists: **No**.
- Trading logic changed: **No**.
- AI, ranking, portfolio, backtest, replay, forward testing, or scalping logic changed: **No**.
- Validated metrics changed: **No**.
- One-click browser-session attachment achieved: **No — requires an official supported Rubix/Mubasher interface**.
