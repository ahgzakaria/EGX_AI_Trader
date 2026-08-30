# Scheduled tasks run from F:, and the D: junction is no longer load-bearing

**Date:** 2026-08-30 · **Scope:** Windows Task Scheduler on this host only — no
code changed, because no code ever referenced `D:`.

## What was true before

`D:\EGX_AI_Trader` is a **junction** onto `F:\EGX_AI_Trader` — a link, not a
copy. It holds no data and occupies no space, and both paths resolve to the same
files: `Get-FileHash` on `scripts\run_rubix_daily_finalizer.py` through either
path returns the same hash.

Nine EGX scheduled tasks existed. Four named `F:`, five named `D:`. That split
was drift, not design, and it cost real time: an investigation into why a candle
was missing began by reading the task's `D:` path and asking whether it was even
the same tree.

Nothing in the repository referenced `D:` — not `config/`, not `scripts/`, not
`core/`, not `services/`, not `docs/`. Nor did any desktop shortcut, Start Menu
entry, `Startup` folder item, or `HKCU`/`HKLM` `Run` key. The dependency existed
only inside Task Scheduler.

## What changed

Six task definitions were repointed from `D:\EGX_AI_Trader` to
`F:\EGX_AI_Trader`, rewriting only the three path-bearing fields of each action
— `Execute`, `Arguments`, `WorkingDirectory`:

| Task | Schedule |
|---|---|
| EGX Expected Range Pre-Session | 09:45 Sun–Thu |
| EGX Expected Range Live Monitor | 09:55 Sun–Thu |
| EGX Expected Range Outcome Finalizer | 14:40 Sun–Thu |
| EGX Scalping Session Validator | 14:40 Sun–Thu |
| EGX Rubix Daily Finalizer | 15:45 Sun–Thu |
| EGX_AI_Trader_YahooCacheRefresh | 08:00 — **disabled, and left disabled** |

Triggers, settings, principals and enabled/disabled state were untouched:
`Set-ScheduledTask -Action` replaces the actions and nothing else. Verified
afterwards — zero remaining `D:` references across all ten tasks, every next-run
time unchanged.

**No data moved and none could have.** Each launcher derives its own root from
its own location (`$PSScriptRoot`, or `Split-Path $MyInvocation.MyCommand.Path`),
so a task launched through `D:` was already writing to `F:` physically. Only the
label changed.

Every task definition was exported to
`backups/scheduled_tasks/<timestamp>/*.xml` before any change. Restore one with
`Register-ScheduledTask -Xml (Get-Content <file> -Raw) -TaskName <name> -Force`.

## Verified by running, not by reading

- **EGX Rubix Daily Finalizer** triggered from the new path: result `0`, log
  written to `F:`, backfill classification correct.
- **EGX Scalping Session Validator** triggered from the new path: result `0`,
  `root=F:\EGX_AI_Trader` in its own log, verdict table regenerated.
- **run_daily_orb_automation.ps1** driven down its refuse path with a Friday
  date: refused correctly, exit 1, logging intact. The two artifacts that run
  created were removed afterwards.

## The junction itself

Still present, and now referenced by nothing. It can be removed at any time:

```
cmd /c rmdir "D:\EGX_AI_Trader"
```

`rmdir` **without** `/S` — it unlinks and does not descend. This matters: a
recursive delete can follow a junction and destroy the target, which here is the
entire project on `F:`. Removing it frees no space, because it never used any.

To recreate:

```
cmd /c mklink /J "D:\EGX_AI_Trader" "F:\EGX_AI_Trader"
```
