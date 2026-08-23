"""Where does the stop stop protecting and start destroying?

The entry has an edge: signalled symbols drift +1.38% to the close against
+0.47% for every other symbol the engine was watching at the same instant, and
they fall about a point less on the way. The trades still lose 0.89%, and the
trigger sits only 0.13% above the market at detection, so the entry price is
not the leak.

That leaves the stop. Average worst excursion on a signalled symbol is -3.19%,
while the engine places its stop between -0.5% and -1.1% -- inside the ordinary
adverse move, where it is a coin toss rather than a risk control.

So: hold every signal to the session close and vary only the stop.
"""
import glob, os, sqlite3, statistics, sys
sys.path.insert(0, ".")

COST = 0.80
rubix = sqlite3.connect("file:data/rubix_live_market.db?mode=ro", uri=True)
rubix.execute("PRAGMA query_only=ON")

sigs = []
for path in sorted(glob.glob("data/research/orb_full_shadow/*.db")):
    day = os.path.basename(path)[-13:-3]
    if day < "2026-07-15":
        continue
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True); c.row_factory = sqlite3.Row
    if "orb_signal_qualification" not in {r[0] for r in c.execute(
            "select name from sqlite_master where type='table'")}:
        continue
    for r in c.execute("""select q.canonical_ticker t, q.trigger_price p,
            q.proposed_stop s, q.detection_at_utc d
        from orb_signal_qualification q join (select canonical_ticker,
            min(detection_at_utc) f from orb_signal_qualification
            where trigger_price is not null group by canonical_ticker) m
        on m.canonical_ticker=q.canonical_ticker and m.f=q.detection_at_utc
        where q.trigger_price is not null and q.proposed_stop is not null
        group by q.canonical_ticker"""):
        sigs.append((r["t"], float(r["p"]), float(r["s"]), str(r["d"])))
    c.close()

paths = {}
for t, p, s, d in sigs:
    rows = rubix.execute("select last_price from quotes where ticker=? and "
                         "market_timestamp>=? and last_price>0 order by market_timestamp",
                         (t, d)).fetchall()
    if len(rows) >= 10:
        paths[(t, d)] = [float(x[0]) for x in rows]

print(f"{len(paths)} signals with a usable path, cost {COST}% round trip")
print(f"{'stop':<28}{'n':>5}{'avg net%':>11}{'win%':>8}{'stopped':>9}{'total%':>9}")
print("-" * 72)


def run(label, stop_of):
    nets, stopped = [], 0
    for t, p, s, d in sigs:
        path = paths.get((t, d))
        if not path:
            continue
        stop = stop_of(p, s)
        out = None
        if stop is not None:
            for price in path:
                if price <= stop:
                    out = (stop - p) / p * 100 - COST
                    stopped += 1
                    break
        if out is None:
            out = (path[-1] - p) / p * 100 - COST
        nets.append(out)
    if not nets:
        return
    print(f"{label:<28}{len(nets):>5}{statistics.fmean(nets):>11.2f}"
          f"{sum(1 for n in nets if n > 0)/len(nets)*100:>8.0f}"
          f"{stopped:>9}{sum(nets):>9.1f}")


run("engine's own stop", lambda p, s: s)
for pct in (1.0, 2.0, 3.0, 4.0, 5.0):
    run(f"fixed -{pct:.0f}%", lambda p, s, q=pct: p * (1 - q / 100))
run("none (hold to close)", lambda p, s: None)
