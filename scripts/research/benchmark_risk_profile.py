r"""Tasks D, E and F — the benchmark judged on the axes the strategies are judged on.

EXPOSURE_VS_DRIFT.md compared return and nothing else. A benchmark that wins on
return and loses badly on risk would mean the objective was mis-specified rather
than dead, so the comparison is only finished when both sides carry the same
risk profile.

**D** gives every benchmark the strategy's own treatment: marked drawdown,
longest drawdown in days, worst session, worst 21 sessions, Sharpe, Sortino,
Calmar, CAGR — in EGP and in USD.

**E** splits the history by calendar year and by the regime labels in
`reports/phase11_market_regimes.csv`, because a whole-window verdict over a
decade containing two currency repricings may be a statement about the regime
rather than about the strategies.

**F** builds a benchmark that could actually have been bought: the top N names
by traded value in the twelve months **before** the window opens, bought once at
the open, never rebalanced, paying the same per-symbol entry cost the strategies
pay. No forward information enters the selection.

    venv\Scripts\python.exe scripts\research\benchmark_risk_profile.py
"""

from __future__ import annotations

from pathlib import Path
import sys
import warnings

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from core.environment import load_project_environment

load_project_environment()

from backtesting.equity import EquityCurve                        # noqa: E402
from exposure_vs_drift import (Row, eligible_universe, prices_for,  # noqa: E402
                               round_trip)
from exposure_usd_and_survivorship import usd_rate                # noqa: E402

EXPERIMENTS = PROJECT_ROOT / "reports" / "experiments"
SHIPPED = EXPERIMENTS / "20260827_193224_reward_guard"
REGIMES = PROJECT_ROOT / "reports" / "phase11_market_regimes.csv"
OUT_DIR = PROJECT_ROOT / "reports" / "audits" / "strategies"

WINDOW = (pd.Timestamp("2017-08-03"), pd.Timestamp("2026-06-28"))
SESSIONS_PER_YEAR = 250
#: 54 of 245 listed names delisted without prices -- 22.0% of the universe.
#: Applied to an ex-ante basket as a bound, never as a measurement.
DELISTED_SHARE = 54 / (191 + 54)


# --- risk, the same way for every curve --------------------------------------

def metrics(curve: pd.Series, label: str) -> dict:
    """Every axis the strategies are reported on, from a daily equity series."""

    curve = curve.dropna()
    curve = curve[curve > 0]
    if len(curve) < 30:
        return {"line": label, "sessions": len(curve)}

    years = (curve.index[-1] - curve.index[0]).days / 365.25
    total = curve.iloc[-1] / curve.iloc[0] - 1
    peak = curve.cummax()
    underwater = (curve - peak) / peak
    max_dd = float(underwater.min()) * 100

    # Longest stretch below a prior peak, in calendar days.
    below = underwater < -1e-9
    longest, start = 0, None
    for when, wet in below.items():
        if wet and start is None:
            start = when
        elif not wet and start is not None:
            longest = max(longest, (when - start).days)
            start = None
    if start is not None:
        longest = max(longest, (below.index[-1] - start).days)

    daily = curve.pct_change().dropna()
    rolling21 = (curve / curve.shift(21) - 1).dropna()
    downside = daily[daily < 0]
    cagr = ((1 + total) ** (1 / years) - 1) * 100 if years > 0 and total > -1 else None

    return {
        "line": label,
        "sessions": len(curve),
        "years": round(years, 2),
        "total_return": round(total * 100, 2),
        "cagr": None if cagr is None else round(cagr, 2),
        "max_dd": round(max_dd, 2),
        "longest_dd_days": int(longest),
        "worst_session": round(float(daily.min()) * 100, 2),
        "worst_21_sessions": round(float(rolling21.min()) * 100, 2),
        "sharpe": (round(float(daily.mean() / daily.std()
                               * np.sqrt(SESSIONS_PER_YEAR)), 2)
                   if daily.std() else None),
        "sortino": (round(float(daily.mean() / downside.std()
                                * np.sqrt(SESSIONS_PER_YEAR)), 2)
                    if len(downside) > 1 and downside.std() else None),
        "calmar": (round(cagr / abs(max_dd), 2)
                   if cagr is not None and max_dd < -1e-9 else None),
    }


def to_usd(curve: pd.Series, rate: pd.Series) -> pd.Series:
    """An EGP curve expressed in dollars, using the daily rate."""

    aligned = rate.reindex(curve.index).ffill().bfill()
    return (curve / aligned).dropna()


# --- the curves --------------------------------------------------------------

def hold_basket(symbols, start, end, charge_costs=True,
                weights=None) -> pd.Series:
    """Buy once at `start`, hold to `end`, never rebalance.

    Costs are charged as a haircut on the entry price, once, at that symbol's
    own measured round trip -- the same table the strategies pay from. A name
    that stops trading mid-window keeps its last price rather than dropping out
    of the basket, because dropping it would quietly rebalance into the
    survivors.
    """

    frame = prices_for(symbols)
    if frame is None or not len(frame):
        return pd.Series(dtype=float)
    columns = [s for s in symbols if s in frame.columns]
    window = frame.loc[(frame.index >= start) & (frame.index <= end), columns]
    if len(window) < 2:
        return pd.Series(dtype=float)

    pieces, used = [], []
    for symbol in columns:
        series = window[symbol]
        first_valid = series.first_valid_index()
        if first_valid is None:
            continue
        entry = float(series.loc[first_valid])
        if entry <= 0:
            continue
        cost = round_trip(symbol) if charge_costs else 0.0
        if cost != cost:
            cost = 0.0
        held = series.ffill()
        held = held.where(held.index >= first_valid)
        pieces.append(held / entry * (1 - cost / 100.0))
        used.append(symbol)
    if not pieces:
        return pd.Series(dtype=float)

    basket = pd.concat(pieces, axis=1)
    basket.columns = used
    if weights is None:
        return basket.mean(axis=1, skipna=True).dropna()
    weight = pd.Series(weights).reindex(used).fillna(0.0)
    weight = weight / weight.sum() if weight.sum() else weight
    return (basket * weight).sum(axis=1, min_count=1).dropna()


def median_name_curve(symbols, start, end) -> pd.Series:
    """The name whose held return is the median, and its own path.

    Chosen with hindsight, which is why it is labelled ex-post everywhere it
    appears: nobody could have picked it at the start.
    """

    frame = prices_for(symbols)
    columns = [s for s in symbols if s in frame.columns]
    window = frame.loc[(frame.index >= start) & (frame.index <= end), columns]
    scored = {}
    for symbol in columns:
        series = window[symbol].dropna()
        if len(series) < 30 or series.iloc[0] <= 0:
            continue
        cost = round_trip(symbol)
        cost = 0.0 if cost != cost else cost
        scored[symbol] = (series.iloc[-1] / series.iloc[0] - 1) * 100 - cost
    if not scored:
        return pd.Series(dtype=float)
    ordered = sorted(scored, key=scored.get)
    chosen = ordered[len(ordered) // 2]
    return hold_basket([chosen], start, end)


def strategy_curve(run_dir: Path) -> pd.Series:
    """The shipped run's marked daily equity, rebuilt from its own trade file."""

    stats = pd.read_csv(run_dir / "backtest_statistics.csv",
                        encoding="utf-8-sig").iloc[0].to_dict()
    frame = pd.read_csv(run_dir / "backtest_results.csv", encoding="utf-8-sig")
    rows = [Row(record) for record in frame.to_dict("records")]
    rows = [r for r in rows if r.executed and r.shares]
    curve = EquityCurve(rows,
                        initial_capital=float(stats.get("InitialCapital") or 100_000),
                        prices=prices_for(sorted({r.symbol for r in rows})))
    daily = curve.daily_curve()
    if isinstance(daily, pd.Series):
        return daily.dropna()
    frame = pd.DataFrame(daily)
    column = "Equity" if "Equity" in frame else frame.columns[-1]
    if "Date" in frame:
        frame = frame.set_index(pd.to_datetime(frame["Date"]))
    return frame[column].dropna()


def traded_symbols(run_dir: Path) -> list[str]:
    frame = pd.read_csv(run_dir / "backtest_results.csv", encoding="utf-8-sig")
    if "executed" in frame:
        frame = frame[frame["executed"].astype(str).str.lower().isin(("true", "1"))]
    return sorted(set(frame["symbol"].astype(str)))


# --- F: the basket somebody could have bought --------------------------------

def ex_ante_basket(universe, start, top_n, lookback_days=365) -> list[str]:
    """Top N by traded value in the year BEFORE the window. No forward data."""

    frame = prices_for(universe)
    volumes = _volume_panel(universe)
    if volumes is None:
        return []
    lookback_start = start - pd.Timedelta(days=lookback_days)
    prior_p = frame.loc[(frame.index >= lookback_start) & (frame.index < start)]
    prior_v = volumes.loc[(volumes.index >= lookback_start) & (volumes.index < start)]
    shared = [c for c in prior_p.columns if c in prior_v.columns]
    if not shared or len(prior_p) < 60:
        return []
    value = (prior_p[shared] * prior_v[shared]).median(axis=0, skipna=True)
    return list(value.dropna().sort_values(ascending=False).head(top_n).index)


_VOLUMES = None


def _volume_panel(symbols):
    """Daily volume per symbol, from the research panel."""

    global _VOLUMES
    if _VOLUMES is not None:
        return _VOLUMES
    cache = PROJECT_ROOT / "data" / "research" / "backtest_panel.parquet"
    if not cache.exists():
        return None
    panel = pd.read_parquet(cache, columns=["Date", "Symbol", "Volume"])
    _VOLUMES = panel.pivot_table(index="Date", columns="Symbol", values="Volume")
    _VOLUMES.index = pd.to_datetime(_VOLUMES.index)
    return _VOLUMES


def haircut(total_return_percent: float, share: float) -> float:
    """The same basket if `share` of it had been delisted to nothing."""

    return round(total_return_percent * (1 - share) + (-100.0) * share, 2)


# --- E: the slices -----------------------------------------------------------

def slice_table(strategy: pd.Series, benchmark: pd.Series, slices) -> pd.DataFrame:
    rows = []
    for label, mask_start, mask_end, size in slices:
        s = strategy.loc[(strategy.index >= mask_start) & (strategy.index <= mask_end)]
        b = benchmark.loc[(benchmark.index >= mask_start) & (benchmark.index <= mask_end)]
        if len(s) < 5 or len(b) < 5:
            rows.append({"slice": label, "sessions": size,
                         "strategy_return": None, "benchmark_return": None,
                         "strategy_dd": None, "benchmark_dd": None,
                         "strategy_beats": None})
            continue
        sr = (s.iloc[-1] / s.iloc[0] - 1) * 100
        br = (b.iloc[-1] / b.iloc[0] - 1) * 100
        sdd = ((s - s.cummax()) / s.cummax()).min() * 100
        bdd = ((b - b.cummax()) / b.cummax()).min() * 100
        rows.append({
            "slice": label, "sessions": len(s),
            "strategy_return": round(sr, 2), "benchmark_return": round(br, 2),
            "strategy_dd": round(sdd, 2), "benchmark_dd": round(bdd, 2),
            "strategy_beats": bool(sr > br),
            "strategy_dd_better": bool(sdd > bdd),
        })
    return pd.DataFrame(rows)


def main() -> int:
    universe = eligible_universe()
    prices_for(universe)
    start, end = WINDOW
    rate = usd_rate()
    if rate is None:
        print("EGP=X unavailable - every USD column reports None")

    traded = traded_symbols(SHIPPED)
    strategy = strategy_curve(SHIPPED)
    print(f"strategy curve: {len(strategy)} sessions "
          f"{strategy.index.min().date()} -> {strategy.index.max().date()}\n")

    curves = {
        "STRATEGY shipped (reward_guard)": strategy,
        "(a) equal-weight universe, held": hold_basket(universe, start, end),
        "(b) median EGX name, held (ex-post)": median_name_curve(universe, start, end),
        "(c) the names this run traded, held": hold_basket(traded, start, end),
    }

    print("=" * 104)
    print("TASK D - THE SAME RISK PROFILE FOR BOTH SIDES, IN EGP")
    print("=" * 104)
    header = (f"{'line':<38}{'return':>10}{'CAGR':>8}{'maxDD':>9}{'longest':>9}"
              f"{'worst d':>9}{'worst 21':>10}{'Sharpe':>8}{'Sortino':>9}{'Calmar':>8}")
    print(header)
    print("-" * len(header))
    egp_rows = []
    for label, curve in curves.items():
        m = metrics(curve, label)
        egp_rows.append(m)
        if "total_return" not in m:
            print(f"{label:<38}  not computable ({m.get('sessions')} sessions)")
            continue
        print(f"{label:<38}{m['total_return']:>9.2f}%{m['cagr']:>7.2f}%"
              f"{m['max_dd']:>8.2f}%{m['longest_dd_days']:>9}"
              f"{m['worst_session']:>8.2f}%{m['worst_21_sessions']:>9.2f}%"
              f"{str(m['sharpe']):>8}{str(m['sortino']):>9}{str(m['calmar']):>8}")

    usd_rows = []
    if rate is not None:
        print()
        print("=" * 104)
        print("TASK D - THE SAME, IN USD")
        print("=" * 104)
        print(header)
        print("-" * len(header))
        for label, curve in curves.items():
            m = metrics(to_usd(curve, rate), label)
            usd_rows.append(m)
            if "total_return" not in m:
                print(f"{label:<38}  not computable")
                continue
            print(f"{label:<38}{m['total_return']:>9.2f}%{m['cagr']:>7.2f}%"
                  f"{m['max_dd']:>8.2f}%{m['longest_dd_days']:>9}"
                  f"{m['worst_session']:>8.2f}%{m['worst_21_sessions']:>9.2f}%"
                  f"{str(m['sharpe']):>8}{str(m['sortino']):>9}{str(m['calmar']):>8}")

    pd.DataFrame(egp_rows).assign(currency="EGP").to_csv(
        OUT_DIR / "benchmark_risk_egp.csv", index=False)
    if usd_rows:
        pd.DataFrame(usd_rows).assign(currency="USD").to_csv(
            OUT_DIR / "benchmark_risk_usd.csv", index=False)

    # --- E ------------------------------------------------------------------
    print()
    print("=" * 104)
    print("TASK E - BY CALENDAR YEAR, STRATEGY AGAINST THE UNIVERSE HELD")
    print("=" * 104)
    market = curves["(a) equal-weight universe, held"]
    years = sorted({d.year for d in strategy.index})
    year_slices = [(str(y), pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y}-12-31"), None)
                   for y in years]
    by_year = slice_table(strategy, market, year_slices)
    trades = pd.read_csv(SHIPPED / "backtest_results.csv", encoding="utf-8-sig")
    trades = trades[trades["executed"].astype(str).str.lower().isin(("true", "1"))]
    trades["year"] = pd.to_datetime(trades["entry_date"]).dt.year
    counts = trades["year"].value_counts().to_dict()
    by_year["trades"] = by_year["slice"].astype(int).map(counts).fillna(0).astype(int)
    by_year["too_small"] = by_year["trades"] < 30
    print(by_year.to_string(index=False))
    by_year.to_csv(OUT_DIR / "benchmark_by_year.csv", index=False)

    print()
    print("=" * 104)
    print("TASK E - BY REGIME LABEL")
    print("=" * 104)
    if not REGIMES.exists():
        print("  reports/phase11_market_regimes.csv missing - reported as None")
    else:
        regimes = pd.read_csv(REGIMES, usecols=["Date", "Regime"])
        regimes["Date"] = pd.to_datetime(regimes["Date"])
        print(f"  regime labels cover {regimes.Date.min().date()} -> "
              f"{regimes.Date.max().date()} ({len(regimes)} sessions), "
              f"not the whole window")
        rows = []
        for label, group in regimes.groupby("Regime"):
            days = pd.DatetimeIndex(group["Date"])
            s = strategy.reindex(strategy.index.intersection(days)).dropna()
            b = market.reindex(market.index.intersection(days)).dropna()
            if len(s) < 5 or len(b) < 5:
                rows.append({"regime": label, "sessions": len(days),
                             "strategy_mean_daily": None, "benchmark_mean_daily": None,
                             "strategy_beats": None})
                continue
            # Regime days are not contiguous, so compound the daily returns that
            # fall inside the label rather than taking endpoint to endpoint.
            sd = strategy.pct_change().reindex(s.index).dropna()
            bd = market.pct_change().reindex(b.index).dropna()
            s_total = (1 + sd).prod() - 1
            b_total = (1 + bd).prod() - 1
            in_regime = trades[pd.to_datetime(trades["entry_date"]).isin(days)]
            rows.append({
                "regime": label, "sessions": len(days),
                "trades_entered": len(in_regime),
                "strategy_compounded": round(s_total * 100, 2),
                "benchmark_compounded": round(b_total * 100, 2),
                "strategy_beats": bool(s_total > b_total),
                "too_small": len(in_regime) < 30,
            })
        frame = pd.DataFrame(rows)
        print(frame.to_string(index=False))
        frame.to_csv(OUT_DIR / "benchmark_by_regime.csv", index=False)

    # --- F ------------------------------------------------------------------
    print()
    print("=" * 104)
    print("TASK F - A BASKET THAT COULD HAVE BEEN BOUGHT AT THE OPEN")
    print("=" * 104)
    print(f"  selection: top N by median traded value in the 12 months before "
          f"{start.date()}, no forward data")
    print(f"  worst-case delisting haircut applied at {DELISTED_SHARE * 100:.1f}% "
          f"of the basket (54 of 245 listed names have no prices)\n")
    print(f"{'basket':<26}{'names':>7}{'EGP total':>12}{'EGP CAGR':>10}{'maxDD':>9}"
          f"{'Calmar':>8}{'USD total':>12}{'USD CAGR':>10}{'worst case EGP':>16}")
    print("-" * 110)
    f_rows = []
    for top_n in (10, 20, 30):
        picked = ex_ante_basket(universe, start, top_n)
        if not picked:
            print(f"  top {top_n:<21} selection not computable - volume panel missing")
            f_rows.append({"basket": f"top{top_n}", "names": None})
            continue
        curve = hold_basket(picked, start, end)
        m = metrics(curve, f"ex-ante top {top_n}")
        u = metrics(to_usd(curve, rate), f"ex-ante top {top_n} USD") if rate is not None else {}
        worst = haircut(m["total_return"], DELISTED_SHARE) if "total_return" in m else None
        print(f"  ex-ante top {top_n:<13}{len(picked):>7}{m['total_return']:>11.2f}%"
              f"{m['cagr']:>9.2f}%{m['max_dd']:>8.2f}%{str(m['calmar']):>8}"
              f"{str(u.get('total_return')):>12}{str(u.get('cagr')):>10}"
              f"{str(worst):>16}")
        f_rows.append({"basket": f"ex_ante_top{top_n}", "names": len(picked),
                       "symbols": ",".join(picked), **{f"egp_{k}": v for k, v in m.items()},
                       **{f"usd_{k}": v for k, v in u.items()},
                       "egp_worst_case_total": worst})
    pd.DataFrame(f_rows).to_csv(OUT_DIR / "benchmark_ex_ante.csv", index=False)
    print("\n  EGX30: no index history from any provider "
          "(DAILY_STRATEGY_DIAGNOSIS §7) - reported as None")
    print(f"\nwrote {OUT_DIR.relative_to(PROJECT_ROOT)}\\benchmark_*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
