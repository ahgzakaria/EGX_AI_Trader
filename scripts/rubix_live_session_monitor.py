"""Phase 9 live-session monitor for the patched Rubix collector (read-only).

Declared BEFORE the run (do not change after seeing results):
  ACCEPTANCE TARGETS
  - connection uptime >= 90% of elapsed session minutes (market-wide active)
  - market-wide maximum silence <= 2 minutes during the open session
  - at least some symbols reach >= 60% observation coverage by close
  - the liveness watchdog demonstrably works (reconnects recover the feed)

Runs during the EGX session (10:00-14:30 Cairo), samples at checkpoints, and
appends results to reports/rubix_live_session_monitor.(txt|csv). Never writes
the Rubix DB. Exits after the post-close checkpoint.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, r"D:\EGX_AI_Trader")
os.chdir(r"D:\EGX_AI_Trader")

CAIRO = ZoneInfo("Africa/Cairo")
DB = Path(r"D:\EGX_AI_Trader\data\rubix_live_market.db")
SESSION_OPEN = dt.time(10, 0)      # Cairo
SESSION_CLOSE = dt.time(14, 30)
S_UTC, E_UTC = "07:00:00", "11:30:00"   # session window in UTC (Cairo UTC+3, July)
TXT = Path("reports/rubix_live_session_monitor.txt")
CSVP = Path("reports/rubix_live_session_monitor.csv")


def connect():
    c = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def insess(col):
    return f"substr({col},12,8)>='{S_UTC}' AND substr({col},12,8)<='{E_UTC}'"


def measure(label):
    today = dt.datetime.now(CAIRO).date().isoformat()
    now_cairo = dt.datetime.now(CAIRO)
    elapsed_min = 0
    if now_cairo.time() >= SESSION_OPEN:
        end = min(now_cairo.time(), SESSION_CLOSE)
        elapsed_min = max(1, int((dt.datetime.combine(now_cairo.date(), end) -
                                  dt.datetime.combine(now_cairo.date(), SESSION_OPEN)).total_seconds() // 60))
    c = connect()
    # market-wide active minutes so far today
    active_min = c.execute(
        f"SELECT COUNT(DISTINCT substr(market_timestamp,12,5)) FROM quotes "
        f"WHERE substr(market_timestamp,1,10)=? AND {insess('market_timestamp')}", (today,)).fetchone()[0]
    stored = c.execute(
        f"SELECT COUNT(*) FROM quotes WHERE substr(market_timestamp,1,10)=? AND {insess('market_timestamp')}",
        (today,)).fetchone()[0]
    latest = c.execute(
        "SELECT MAX(market_timestamp) FROM quotes WHERE substr(market_timestamp,1,10)=?", (today,)).fetchone()[0]
    # per-symbol observation coverage today
    rows = c.execute(
        f"SELECT UPPER(ticker) t, COUNT(DISTINCT substr(market_timestamp,12,5)) m FROM quotes "
        f"WHERE substr(market_timestamp,1,10)=? AND {insess('market_timestamp')} GROUP BY UPPER(ticker)",
        (today,)).fetchall()
    covs = sorted((r["m"] / 270 for r in rows), reverse=True)
    def pctabove(x): return sum(1 for v in covs if v >= x)
    # connection events today (session window)
    def ev(e):
        return c.execute(
            f"SELECT COUNT(*) FROM feed_metrics WHERE event=? AND substr(observed_at,1,10)=? "
            f"AND {insess('observed_at')}", (e, today)).fetchone()[0]
    disc, recon, live_to, resub = ev("disconnect"), ev("reconnect_success"), ev("liveness_timeout"), ev("resubscribe_complete")
    # largest market-wide silence so far
    mm = [r[0] for r in c.execute(
        f"SELECT DISTINCT substr(market_timestamp,12,5) m FROM quotes WHERE substr(market_timestamp,1,10)=? "
        f"AND {insess('market_timestamp')} ORDER BY m", (today,)).fetchall()]
    max_gap = 0
    if len(mm) > 1:
        t = [dt.datetime.strptime(x, "%H:%M") for x in mm]
        max_gap = max((t[i+1]-t[i]).total_seconds()/60 for i in range(len(t)-1))
    c.close()

    uptime = round(active_min / elapsed_min * 100, 1) if elapsed_min else 0.0
    median_cov = round((covs[len(covs)//2] * 100), 1) if covs else 0.0
    row = {
        "checkpoint": label, "cairo_time": now_cairo.strftime("%Y-%m-%d %H:%M"),
        "elapsed_session_min": elapsed_min, "market_active_min": active_min,
        "uptime_pct": uptime, "stored_quotes": stored, "latest_quote": latest,
        "symbols_with_data": len(covs), "median_cov_pct": median_cov,
        "max_cov_pct": round(covs[0]*100, 1) if covs else 0.0,
        "sym_ge60": pctabove(0.6), "sym_ge80": pctabove(0.8), "sym_ge90": pctabove(0.9),
        "max_silence_min": round(max_gap, 1),
        "disconnects": disc, "reconnects": recon, "liveness_timeouts": live_to,
        "resubscribes": resub,
    }
    return row


def emit(row):
    TXT.parent.mkdir(exist_ok=True)
    line = (f"[{row['checkpoint']}] {row['cairo_time']} | uptime {row['uptime_pct']}% "
            f"({row['market_active_min']}/{row['elapsed_session_min']} min) | "
            f"median_cov {row['median_cov_pct']}% max {row['max_cov_pct']}% | "
            f">=60%:{row['sym_ge60']} >=80%:{row['sym_ge80']} >=90%:{row['sym_ge90']} | "
            f"max_silence {row['max_silence_min']}min | disc {row['disconnects']} "
            f"recon {row['reconnects']} liveness_to {row['liveness_timeouts']} | "
            f"latest {row['latest_quote']}")
    with TXT.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    write_header = not CSVP.exists()
    with CSVP.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            w.writeheader()
        w.writerow(row)
    print(line, flush=True)


def main():
    checkpoints = [
        ("BASELINE_PREOPEN", dt.time(9, 45)),
        ("FIRST_30MIN", dt.time(10, 35)),
        ("MID_SESSION", dt.time(12, 15)),
        ("FINAL_30MIN", dt.time(14, 5)),
        ("AFTER_CLOSE", dt.time(14, 45)),
    ]
    emit_header()
    for label, when in checkpoints:
        target = dt.datetime.combine(dt.datetime.now(CAIRO).date(), when, tzinfo=CAIRO)
        wait = (target - dt.datetime.now(CAIRO)).total_seconds()
        if wait > 0:
            time.sleep(wait)
        try:
            emit(measure(label))
        except Exception as error:  # never let one checkpoint abort the run
            with TXT.open("a", encoding="utf-8") as f:
                f.write(f"[{label}] ERROR: {type(error).__name__}: {error}\n")
            print(f"[{label}] ERROR {error}", flush=True)
    print("=== live-session monitoring complete ===", flush=True)


def emit_header():
    TXT.parent.mkdir(exist_ok=True)
    with TXT.open("a", encoding="utf-8") as f:
        f.write(f"\n===== RUBIX LIVE SESSION MONITOR {dt.datetime.now(CAIRO).date()} =====\n")
        f.write("TARGETS (declared before run): uptime>=90%, max_silence<=2min, "
                "some symbols>=60% coverage, watchdog recovers feed\n")


if __name__ == "__main__":
    main()
