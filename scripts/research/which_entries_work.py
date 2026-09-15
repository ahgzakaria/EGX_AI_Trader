r"""Which of the daily strategy's entry features predict how its trades end?

    venv\Scripts\python.exe scripts\research\which_entries_work.py

`SCORE_CANNOT_BE_REBUILT.md` asked this against a fixed twenty-session lift on
the 1,040 bars that pass the gates, and found nothing that survives both eras
except a hint in `DIST_HIGH20`. That target ignores how trades actually end.
`exit_rule_sweep.py` then showed the ending is most of the story: the EMA20
trail cuts a typical trade at about five days, and every looser exit loses
money. So this asks the same question against the outcome the strategy really
produces -- the realised net return of every trade the engine took, under the
real entry band, trail and costs (`trade_features_dump.py`).

**Pre-registered, before reading any result:**

* The one hypothesis carried in from the earlier study is `dist_high20`,
  expected **negative**: entries nearer their twenty-day high end better.
* A feature is called a survivor only if its rank correlation has the same sign
  and p < 0.05 in **both** eras (<2023 and >=2023). With 28 features and two
  independent eras, about 28 x 0.05 x 0.05 x 0.5 = 0.035 same-sign survivors
  are expected by chance -- so one survivor is weak evidence and none is the
  expected result of noise. Both counts are printed.
* Nothing is fitted. A threshold or a weighting chosen here and read back here
  would be the in-sample illusion the earlier study documents.

The population is every trade the engine produced. `PortfolioSimulator` refuses
some for capacity, which is unrelated to their quality, so the executed subset
is reported as a check, not as the primary sample.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                            # noqa: E402
import pandas as pd                                           # noqa: E402
from scipy import stats                                       # noqa: E402

TRADES = PROJECT_ROOT / "reports" / "research" / "daily_strategy_trades.csv"
SPLIT = "2023-01-01"
HYPOTHESIS = ("dist_high20", -1)

FEATURES = [
    "score", "confidence", "trend_score", "volume_score", "momentum_score",
    "candle_score", "breakout_score", "rsi", "rsi7", "adx", "adx_rising",
    "atr_percent", "macd", "ema20_dist", "ema50_dist", "ema200_dist",
    "ema20_slope", "ema50_slope", "rsi_slope", "volume_ratio", "bb_position",
    "bb_width", "obv_slope", "dist_high20", "dist_low20", "macd_cross_age", "rr",
]


def profit_factor(returns):
    gains = returns[returns > 0].sum()
    losses = -returns[returns < 0].sum()
    return gains / losses if losses else float("inf")


def correlations(frame, outcome="profit_percent"):
    rows = []
    for feature in FEATURES:
        if feature not in frame:
            continue
        values = pd.to_numeric(frame[feature], errors="coerce")
        row = {"feature": feature}
        for era, part in (("early", frame[frame["_era"] == "early"]),
                          ("late", frame[frame["_era"] == "late"])):
            x = values.loc[part.index]
            y = part[outcome]
            ok = x.notna() & y.notna()
            if ok.sum() < 30 or x[ok].nunique() < 3:
                row[f"{era}_rho"], row[f"{era}_p"], row[f"{era}_n"] = np.nan, np.nan, int(ok.sum())
                continue
            rho, p = stats.spearmanr(x[ok], y[ok])
            row[f"{era}_rho"], row[f"{era}_p"], row[f"{era}_n"] = rho, p, int(ok.sum())
        rows.append(row)
    table = pd.DataFrame(rows)
    table["survives"] = ((table["early_p"] < 0.05) & (table["late_p"] < 0.05)
                         & (np.sign(table["early_rho"]) == np.sign(table["late_rho"])))
    return table


def quintiles(frame, feature):
    """Win rate, mean net return and PF per fifth of the feature, per era."""
    out = []
    for era in ("early", "late"):
        part = frame[frame["_era"] == era].copy()
        part["_x"] = pd.to_numeric(part[feature], errors="coerce")
        part = part.dropna(subset=["_x"])
        if len(part) < 50:
            continue
        part["_q"] = pd.qcut(part["_x"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
        for q, g in part.groupby("_q", observed=True):
            r = g["profit_percent"]
            out.append({"era": era, "fifth": int(q), "n": len(g),
                        "range": f"{g['_x'].min():.3g}..{g['_x'].max():.3g}",
                        "win%": round((r > 0).mean() * 100, 1),
                        "mean%": round(r.mean(), 2), "PF": round(profit_factor(r), 2)})
    return pd.DataFrame(out)


def main():
    frame = pd.read_csv(TRADES)
    frame["_era"] = np.where(frame["entry_date"].astype(str) < SPLIT, "early", "late")
    frame["profit_percent"] = pd.to_numeric(frame["profit_percent"], errors="coerce")

    for label, population in (("ALL engine trades", frame),
                              ("EXECUTED only", frame[frame["executed"].astype(bool)])):
        counts = population["_era"].value_counts().to_dict()
        print(f"\n===== {label}: {len(population)} trades "
              f"(<{SPLIT[:4]} {counts.get('early', 0)}, >={SPLIT[:4]} {counts.get('late', 0)})")
        for era in ("early", "late"):
            r = population[population["_era"] == era]["profit_percent"]
            print(f"  {era:5}  win {(r > 0).mean() * 100:.1f}%  mean {r.mean():+.2f}%  "
                  f"median {r.median():+.2f}%  PF {profit_factor(r):.2f}")

        table = correlations(population)
        shown = table.assign(
            early=lambda t: t["early_rho"].map("{:+.3f}".format) + " (p " + t["early_p"].map("{:.2f}".format) + ")",
            late=lambda t: t["late_rho"].map("{:+.3f}".format) + " (p " + t["late_p"].map("{:.2f}".format) + ")",
        ).sort_values("late_rho", key=lambda s: s.abs(), ascending=False)
        print(shown[["feature", "early", "late", "survives"]].to_string(index=False))

        survivors = table[table["survives"]]
        tested = table.dropna(subset=["early_p", "late_p"])
        expected = len(tested) * 0.05 * 0.05 * 0.5
        print(f"\n  survivors (same sign, p<0.05 in both eras): {len(survivors)}"
              f" of {len(tested)} tested; about {expected:.2f} expected by chance")

        name, sign = HYPOTHESIS
        row = table[table["feature"] == name]
        if not row.empty:
            row = row.iloc[0]
            agrees = (np.sign(row["early_rho"]) == sign and np.sign(row["late_rho"]) == sign)
            print(f"  pre-registered {name} (expected {'negative' if sign < 0 else 'positive'}): "
                  f"early {row['early_rho']:+.3f} p {row['early_p']:.3f}, "
                  f"late {row['late_rho']:+.3f} p {row['late_p']:.3f} -> "
                  f"{'direction holds' if agrees else 'direction does not hold'}"
                  f"{', significant in both' if row['survives'] else ''}")

        for feature in list(survivors["feature"]) + ([name] if name not in set(survivors["feature"]) else []):
            q = quintiles(population, feature)
            if not q.empty:
                print(f"\n  {feature} by fifth (1 = lowest):")
                print(q.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
