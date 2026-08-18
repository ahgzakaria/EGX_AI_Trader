"""Is the breakout the signal, or is the volume?

`high_volume_breakout` is `previous_resistance_breakout AND volume >= 1.5x`,
so the two overlap and weighting both would count the same event twice. This
splits them into disjoint groups.
"""
import glob, os
import pandas as pd

COST, SPLIT, TOP_N = 0.80, "2024-01-01", 60
frames = {}
for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
    s = os.path.basename(path)[:-4]
    f = pd.read_csv(path)
    if len(f) < 400: continue
    f = f.rename(columns=str.title)[["Date","Open","High","Low","Close","Volume"]].dropna()
    f = f[(f[["Open","High","Low","Close"]] > 0).all(axis=1) & (f["Volume"] > 0)]
    f = f[(f["High"]-f["Low"])/f["Open"]*100 <= 25]
    if len(f) >= 400: frames[s] = f.reset_index(drop=True)

liq = {s: (f["Close"]*f["Volume"]).tail(250).median() for s, f in frames.items()}
universe = sorted(liq, key=liq.get, reverse=True)[:TOP_N]

rows = []
for s in universe:
    f = frames[s].copy()
    c, h, l, v = f["Close"], f["High"], f["Low"], f["Volume"]
    res = h.rolling(20).max().shift(1)
    vr = v / v.rolling(20).mean()
    f["breakout"] = c > res
    f["vr"] = vr
    for hz in (5, 10, 20):
        f[f"fwd{hz}"] = (c.shift(-hz) - c) / c * 100.0 - COST
    f["Symbol"] = s
    rows.append(f)

d = pd.concat(rows, ignore_index=True).dropna(subset=["fwd20", "vr"])
test = d[d["Date"] >= SPLIT]
train = d[d["Date"] < SPLIT]

for hz in (5, 10, 20):
    col = f"fwd{hz}"
    print(f"=== {hz}-day, net of cost — disjoint groups ===")
    print(f"{'group':<34}{'train':>10}{'test':>10}{'test n':>10}{'test win%':>11}")
    print("-" * 75)
    groups = [
        ("no breakout",                 lambda x: ~x["breakout"]),
        ("breakout, volume < 1.0x",     lambda x: x["breakout"] & (x["vr"] < 1.0)),
        ("breakout, volume 1.0-1.5x",   lambda x: x["breakout"] & (x["vr"] >= 1.0) & (x["vr"] < 1.5)),
        ("breakout, volume 1.5-2.5x",   lambda x: x["breakout"] & (x["vr"] >= 1.5) & (x["vr"] < 2.5)),
        ("breakout, volume >= 2.5x",    lambda x: x["breakout"] & (x["vr"] >= 2.5)),
    ]
    for name, mask in groups:
        tr, te = train[mask(train)][col], test[mask(test)][col]
        if len(te) < 100: 
            print(f"{name:<34}{'':>10}{'':>10}{len(te):>10}  too few")
            continue
        print(f"{name:<34}{tr.mean():>+9.3f}%{te.mean():>+9.3f}%{len(te):>10,}{(te>0).mean()*100:>10.1f}%")
    print()
