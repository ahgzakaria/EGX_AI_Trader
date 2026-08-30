# Forward Testing, Started 2026-08-29

**Question:** every number CONFIRMED_VOLUME_BREAKOUT has is in-sample. What
would make that stop being true, and what does it take to start?

**Status:** **running and scheduled.** `data/confirmed_breakout_forward.db`
holds its first session, 2026-08-26. The Windows task
`EGX Confirmed Breakout Forward Test` fires Sunday–Thursday at **15:30** Cairo,
again at 17:30, and has been executed through the scheduler to prove the chain
(exit code 0, log and status file written). The times come from the exchange
calendar rather than from padding — §5. 15 new tests; suite 3,749 passing.

**Short answer:** signals are now written down before anyone knows the answer,
resolved weeks later by the same code that backtested them, and scored against
what an average tradeable name did over the identical window. The record is one
session and zero signals long, and it will be thin for months. That is the cost
of the only evidence that counts.

---

## 1. Why nothing measured so far is evidence about the future

The strategy was built on 2016-2026 history over 190 symbols that **still
exist**; its thresholds were swept with both eras visible; its walk-forward
check re-used the same decade. Every one of those is a reason to take it
seriously and none of them is out-of-sample. The distinction is not pedantry —
this project's own record contains a config-only recommendation that was
measured, defensible and wrong
([TRAILING_STOP_VERDICT.md](TRAILING_STOP_VERDICT.md) §5).

A forward record is the only construction that cannot be tuned after the fact,
and only if it is built so that it *cannot* be.

## 2. What makes this record hard to fake

| Property | Why it is there |
|---|---|
| **Signals and outcomes are immutable**, enforced by SQLite triggers | A record you can edit is a record of what you wish had happened |
| **Sessions are recorded even when they produce nothing** | "The rule fired 3 times" is meaningless without "in how many sessions". A quiet week must look like evidence, not like missing data |
| **Each signal carries a hash of the configuration that made it** | Changing a threshold starts a new record rather than silently pooling two strategies. `resolve` refuses to walk a signal whose hash no longer matches, and says so |
| **Outcomes are walked by `MomentumBreakoutBacktest.resolve_signal`** | The same method the backtest uses. A forward result and a backtested one cannot diverge through a second implementation |
| **A signal is not scored until its full window has passed** | A partial window is not a result, however tempting it is to look |
| **Every outcome carries its own benchmark** | See §3 |

## 3. The benchmark is the whole point

A forward record of raw returns would measure the Egyptian market, which in
nominal EGP returned several hundred percent over the backtested decade. So each
outcome stores what an average name above the strategy's own turnover floor did
over that trade's **identical** window, and the lift between them.

Without it, a bull quarter makes any long-only rule look like a discovery.

## 4. The plumbing is proven, and the proof is not evidence

The live store cannot be used to test resolution — its whole value is that it
only ever contains signals recorded before the outcome was known. So the
record → resolve → report path was exercised in a **throwaway database**,
replaying 33 real sessions from 2026-06-04 to 2026-07-22 by truncating every
symbol's history at each date:

```
62 signals, 61 closed, 1 still running
mean net  +17.87%   median +13.35%   win rate 77%
mean lift  +3.49%
```

**Do not read that as a result.** It is 33 sessions in a strong bull market,
inside the data the thresholds were chosen against, and the scratch database was
deleted. It proves the pipeline runs, resolves and benchmarks correctly. Nothing
else.

One thing in it *is* worth acting on. The live scan produced **62 signals in 33
sessions** — about 1.9 a session, against the backtest's 927 over roughly 2,400
sessions, or 0.39. Five times the rate. Two candidates, and they are not
exclusive: June–July 2026 was exceptionally strong, and the live router serves a
different, wider universe (216 readable symbols) than the Yahoo backtest cache
(191). If the live rate really is that much higher, the fifteen-position cap
binds far harder in practice than
[CAPACITY_IS_THE_CONSTRAINT.md](CAPACITY_IS_THE_CONSTRAINT.md) implies. Worth
watching in the real record, not worth re-tuning on.

## 5. The schedule, and where the safety actually is

Registered 2026-08-29 as **`EGX Confirmed Breakout Forward Test`**, firing
**Sunday–Thursday at 15:30 Cairo, and again at 17:30**. Installed by
`scripts/windows/install_confirmed_breakout_forward_task.ps1`; removed by the
matching `remove_…ps1`, which deliberately leaves the database, the log and the
status files alone.

### The times are the calendar's, not a guess — corrected 2026-08-30

It was first set to 16:30 on the reasoning that the market closes at 14:30 and
two hours is comfortable slack. Asked to justify the gap, the honest answer was
that I could not: it was padding, not a number.

The number the calendar actually gives is **15:25**. In `core/egx_session.py`
continuous trading ends **14:15**, the closing auction ends **14:25**, and
`egx_settlement_grace_minutes` is **60**. So:

| asked at | `authoritative_completed_session` returns |
|---|---|
| 14:25 | the **previous** session |
| 14:40 | the **previous** session |
| **15:25** | **today** |

14:30 is when trading stops, not when the session is complete. That distinction
is load-bearing here, because the recorder refuses any session the exchange has
not completed and writes **nothing**. A run at 14:40 — the time the gap
recorder uses, and the obvious choice to copy — would have recorded nothing
every single day, silently, while the count of sessions scanned is half the
evidence this file exists to produce.

**15:30** is five minutes past the grace. **17:30** is a catch-up, and it covers
the one failure that cannot be repaired later: the provider has not published
the completed bar by 15:30, so the scan sees an older session, finds it already
recorded, and today never enters the record at all. `StartWhenAvailable` does
not help — the task ran, it just found nothing new. Both halves are idempotent
and a run with nothing to do exits in about a second, so the second firing is
close to free.

It runs as the logged-in user with no stored password, `StartWhenAvailable` so a
session missed because the machine was off is caught up, and
`MultipleInstances IgnoreNew` so the 17:30 firing can never overlap a slow 15:30
one. Output is appended to `logs/confirmed_breakout_forward.log` as UTF-8, and each run writes
`data/automation_status/confirmed_breakout_forward_<date>.json`.

The script is idempotent in both halves: a session already recorded exits in
under a second, so weekends, holidays and repeat runs cost nothing, and
`resolve` runs every time regardless, because a signal recorded five weeks ago
matures on a day when nothing new is scanned.

**The clock is not the safety.** A recorded signal is immutable, so one computed
from a half-formed daily bar would be wrong permanently. The recorder therefore
refuses any session the exchange has not authoritatively completed —
`core.egx_session.authoritative_completed_session` — and writes nothing at all
rather than writing something it would have to live with. A test pins the
refusal. The schedule decides whether the normal case is a clean write; the
refusal decides whether a wrong one is possible.

**The absence of today's status file is the alarm**, and that matters more here
than usual: for this strategy a session with no signals is the *normal* outcome,
so a task that quietly stopped running looks exactly like a quiet market.

Verified by triggering it **through Task Scheduler** rather than by running the
Python directly — the PowerShell wrapper, the encoded command, the working
directory and the log are all part of what can break:

```
Last result : 0
session 2026-08-26 — 0 signals from 216 symbols, already recorded
```

To run it by hand at any time:

```
venv\Scripts\python.exe scripts\record_confirmed_breakout_forward.py
```

## 6. Where it is not shared with `forward_testing/`

That subsystem exists, is good, and is recording live evidence for the Daily
Dashboard strategy right now. It was not reused for two concrete reasons:

* its paper portfolio is hard-wired to the other strategy's execution model — a
  `buy_low`/`buy_high` zone filled within `ENTRY_WAIT_DAYS`, sized from
  `config/settings.json -> backtest`. This strategy buys at the **next close**,
  once, sized from its own configuration. Making that pluggable means editing
  fourteen queries in a subsystem currently collecting real evidence;
* its `dedupe_key` is `ticker|date|signal_type` with **no strategy in it**, so
  two strategies emitting a BUY for the same name on the same day collide and
  the second is silently dropped.

Merging later is a `strategy` column and an import. Corrupting a live record is
not undoable, so they stay separate until someone decides otherwise. A test pins
that the two databases are different files.

## 7. When this will be worth reading

The rule fires roughly ninety times a year across this universe and holds twenty
sessions. A first closed trade is weeks away. A mean lift with any weight behind
it needs dozens of closed trades, which is **most of a year**. The dashboard
section says so rather than showing an encouraging early number, and it will
keep saying so until the count justifies otherwise.

If the forward lift lands near the measured **+1.92%** validation lift, that is
the first real support this strategy has. If it lands at zero, the in-sample
result was the thresholds fitting the decade they were chosen on — which is
exactly what a forward test is for, and the reason it was started before anyone
put money behind it.

## Limits

- One market, one strategy, one configuration.
- The benchmark pool is the names above the turnover floor measured over recent
  history rather than as of each date — an approximation, and a deliberately
  generous one, since a benchmark containing only the names the rule liked would
  not be a benchmark.
- Resolution reads the live router, so a revision to a past price changes a
  resolved outcome's inputs. The outcome row itself is immutable once written.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv\Scripts\python.exe -m strategy_momentum_breakout.forward record
venv\Scripts\python.exe -m strategy_momentum_breakout.forward resolve
venv\Scripts\python.exe -m strategy_momentum_breakout.forward report
```
