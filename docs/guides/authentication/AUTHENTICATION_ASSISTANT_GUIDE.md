# Rubix Authentication Assistant Guide

The Rubix authentication step remains manual. EGX AI Trader does not sign in,
control the browser, read cookies, extract tokens, or bypass authentication.

## One-minute workflow

1. Open the official Rubix website normally.
2. Sign in yourself.
3. Open the Live Market page.
4. Press `F12` to open Chrome DevTools.
5. Select **Network**, then choose the **WS** filter.
6. Select the connection whose path ends with `/websocket/price`.
7. Open **Messages** and locate the outbound price authentication message.
8. Copy only that one outbound message into a new local text file.
9. Save the file as `rubix-price-auth-frame.txt`.
10. In the launcher, choose **Browse**, select the file, run **Self Check**, and
   then select **Start Rubix**.

> Screenshot placeholder 1: Rubix Live Market page with Chrome DevTools Network
> tab highlighted.

> Screenshot placeholder 2: WS filter and `/websocket/price` row highlighted.

> Screenshot placeholder 3: Messages view with the outbound direction marked;
> message contents must be hidden in documentation screenshots.

> Screenshot placeholder 4: Launcher showing `VALID` and all Self Check rows
> passing.

## How to recognize the right file

The launcher checks the file locally and displays one of four states:

- `VALID`: a fresh documented Rubix delimiter-based price-authentication frame,
  or a compatible JSON authentication envelope.
- `EXPIRED`: the file is older than 15 minutes or its timestamp is invalid.
- `INVALID`: malformed/unknown data, empty/oversized data, a heartbeat, market metadata,
  a subscription message, or an unknown envelope.
- `MISSING`: no file is selected or the selected file no longer exists.

The contents are never displayed. Validation reads them only in memory and
returns non-secret metadata such as status, age, size, and filename.

## Common mistakes

- Copying an inbound quote or metadata message instead of the outbound auth
  message.
- Copying a heartbeat (`MT: 0`).
- Copying a subscription message (`MT: 10`).
- Pasting the frame into the launcher field instead of saving and selecting a
  file.
- Reusing yesterday's or an older session's file.
- Saving extra explanatory text around the JSON.
- Selecting the file after it has become older than 15 minutes.

## Troubleshooting

### No authentication file selected

Use **Browse** and choose the file itself. The path field is deliberately
read-only to prevent accidental on-screen exposure.

### The selected file is expired

Return to the current signed-in Rubix session, copy the current outbound price
authentication message, save a new file, and select it within 15 minutes.

### The file is not a Rubix price authentication frame

Confirm you selected `/websocket/price`, opened **Messages**, and copied the
outbound authentication message rather than a heartbeat, metadata, quote, or
subscription message.

### The collector is disconnected

Stop the launcher, confirm Rubix is still signed in, create a new auth file,
run Self Check, and start again. You can explicitly choose **Start Yahoo Only**
for disclosed research-only fallback.

### Waiting for fresh market data

The file may be valid while the market is closed or before quotes arrive. The
monitor shows collector, database, quote freshness, coverage, latest quote,
provider, fallback, market, and overall health separately.

### Rubix database has not been created yet

Confirm the database folder exists and is writable, then confirm the external
collector path passes Self Check. EGX AI Trader never writes to adapter tables.

## Security and privacy FAQ

**Does the launcher automate Rubix login?** No.

**Does it read cookies, browser storage, or credentials?** No.

**Does it control Chrome or DevTools?** No.

**Does it extract or intercept tokens?** No.

**Is the authentication frame saved in launcher settings or logs?** No. Only
the last folder—not the file path or its contents—is remembered.

**Can the temporary file be deleted automatically?** Optionally. The checkbox
is OFF by default and deletion happens only after the collector is stopped so
reconnections are not broken.

**What settings are remembered?** Database path, adapter path, last auth
folder, window size, theme, preferred port, and a short non-secret launch
history.
