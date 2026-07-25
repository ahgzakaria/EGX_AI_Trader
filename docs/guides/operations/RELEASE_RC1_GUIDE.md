# EGX AI Trader RC1 Guide

## Build the release

```text
venv\Scripts\python.exe scripts\create_release.py --replace
```

The result is `EGX_AI_Trader_RC1/`, a transparent source release. It contains application source, launchers, requirements, setup, non-secret configuration templates, empty operational directories, and documentation. It does not contain an opaque executable, virtual environment, Git history, runtime databases, caches, auth frames, or credentials.

## Install on Windows

1. Copy the RC1 directory to the target machine.
2. Double-click `scripts\setup_windows.bat`.
3. Review `config\settings.json` after setup.
4. Put or select the independent `rubix_feed` collector directory.
5. Start with `scripts\start_rubix_production.bat`.

The setup creates a virtual environment, installs direct dependencies from `requirements-lock.txt`, validates all critical imports, creates operational directories, copies `settings.example.json` only when runtime settings do not exist, and optionally creates a desktop shortcut when the user confirms.

## Acceptance before any deployment

- all automated tests pass;
- System Health reports the intended state;
- backup and isolated restore succeed;
- Rubix schema, authentication acknowledgement, fresh quote, and coverage checks pass;
- a new archived Backtest can be replayed with identical metrics;
- forward testing remains operational.

RC1 is a release candidate, not approval for real-money trading.
