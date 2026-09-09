r"""Every completed run, against what its own capital would have done sitting still.

Three documents in this repo report the same pattern without naming it: every
reduction in market exposure destroys return. The daily dashboard sits at 23.6%
exposure and returns +60.08%; `strategy_momentum_breakout` at 1%/15%/15 reaches
82% peak deployment and returns +135.8%. This measures the axis directly.

Nothing here re-runs a backtest. Every run in `reports/experiments/` carries its
own trade file, and `remark_published_drawdowns.py` already established that a
run can be re-priced from it, so the drawdowns below are marked to market even
for runs published before `backtesting/equity.py` did that.

Two benchmarks per run, both over that run's own window:

* **its own traded names**, bought and held, equally weighted -- the question
  "would holding what you picked have paid more than trading it".
* **the full eligible universe**, equally weighted -- the market. A rule in the
  market a quarter of the time is really competing against this one.

Both pay costs the way the strategy does: one round trip per name at that
symbol's own measured spread, from `TradingCosts(symbol=...)`. A benchmark that
trades free is not a benchmark. Buy-and-hold pays it once; it is not rebalanced,
so no daily turnover is charged, and that is stated rather than assumed.

    venv\Scripts\python.exe scripts\research\exposure_vs_drift.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from core.environment import load_project_environment

load_project_environment()

from backtesting.costs import TradingCosts                       # noqa: E402
from backtesting.equity import EquityCurve                       # noqa: E402
from backtesting.prices import daily_closes                      # noqa: E402

EXPERIMENTS = PROJECT_ROOT / "reports" / "experiments"
OUT_DIR = PROJECT_ROOT / "reports" / "audits" / "strategies"

_PRICES = None
_COSTS: dict[str, float] = {}


class Row:
    """The subset of `Trade` the marked curve reads, from a results CSV.

    Same shape `remark_published_drawdowns.py` uses, and for the same reason:
    re-running these experiments would cost hours and would not reproduce them.
    """

    __slots__ = ("symbol", "entry_date", "exit_date", "entry_price",
                 "shares", "portfolio_profit", "executed")

    def __init__(self, record):
        self.symbol = str(record["symbol"])
        self.entry_date = str(record["entry_date"])
        self.exit_date = str(record["exit_date"])
        self.entry_price = float(record["entry_price"])
        self.shares = int(float(record.get("shares") or 0))
        self.portfolio_profit = float(record.get("portfolio_profit") or 0.0)
        self.executed = str(record.get("executed", "True")).strip().lower() not in (
            "false", "0", "")


def round_trip(symbol: str) -> float:
    """That symbol's measured round trip, as a percentage. Cached."""

    if symbol not in _COSTS:
        try:
            _COSTS[symbol] = float(TradingCosts(symbol=symbol).round_trip_percent())
        except Exception:                                        # noqa: BLE001
            _COSTS[symbol] = float("nan")
    return _COSTS[symbol]


def prices_for(symbols):
    """Daily closes, loaded once and sliced. The runs overlap almost entirely."""

    global _PRICES
    wanted = sorted({str(s) for s in symbols if s})
    if _PRICES is None:
        _PRICES = daily_closes(wanted)
        return _PRICES
    missing = [s for s in wanted if s not in _PRICES.columns]
    if missing:
        extra = daily_closes(missing)
        if len(extra):
            _PRICES = _PRICES.join(extra, how="outer")
    return _PRICES


def read_run(run_dir: Path) -> dict | None:
    """Statistics, trades and window for one archived run, or None if unusable."""

    stats_path = run_dir / "backtest_statistics.csv"
    trades_path = run_dir / "backtest_results.csv"
    if not stats_path.exists() or not trades_path.exists():
        return None
    try:
        stats = pd.read_csv(stats_path).iloc[0].to_dict()
        trades = pd.read_csv(trades_path)
    except Exception:                                            # noqa: BLE001
        return None
    if not len(trades):
        return None

    executed = trades[trades.get("executed", True).astype(str).str.lower()
                      .isin(("true", "1")) if "executed" in trades else slice(None)]
    if not len(executed):
        executed = trades
    try:
        start = pd.to_datetime(executed["entry_date"]).min()
        end = pd.to_datetime(executed["exit_date"]).max()
    except Exception:                                            # noqa: BLE001
        return None
    if pd.isna(start) or pd.isna(end):
        return None

    label = "unknown"
    manifest = run_dir / "manifest.json"
    if manifest.exists():
        try:
            label = json.loads(manifest.read_text(encoding="utf-8")).get("label", label)
        except Exception:                                        # noqa: BLE001
            pass

    return {"run": run_dir.name, "label": label, "stats": stats,
            "trades": trades, "executed": executed, "start": start, "end": end}


def marked_drawdown(run):
    """Re-price the run from its own trades, marking open positions to market."""

    rows = [Row(record) for record in run["trades"].to_dict("records")]
    rows = [r for r in rows if r.executed and r.shares > 0]
    if not rows:
        return None
    curve = EquityCurve(
        rows,
        initial_capital=float(run["stats"].get("InitialCapital") or 100_000),
        prices=prices_for(sorted({r.symbol for r in rows})))
    return float(curve.max_drawdown()), str(curve.basis())


def peak_deployment(run) -> float | None:
    """The most of the account committed at any one session, against equity then.

    Reconstructed from the trade file: a position occupies capital from its
    entry date to its exit date at `shares * entry_price`. The equity it is
    measured against is the run's own curve where one exists, and the initial
    capital where it does not -- stated rather than silently defaulted.
    """

    executed = run["executed"]
    needed = {"shares", "entry_price", "entry_date", "exit_date"}
    if not needed.issubset(executed.columns):
        return None
    try:
        opens = pd.to_datetime(executed["entry_date"])
        closes = pd.to_datetime(executed["exit_date"])
        size = executed["shares"].astype(float) * executed["entry_price"].astype(float)
    except Exception:                                            # noqa: BLE001
        return None

    events = pd.concat([
        pd.DataFrame({"date": opens, "delta": size}),
        pd.DataFrame({"date": closes, "delta": -size}),
    ]).sort_values("date")
    committed = events.groupby("date")["delta"].sum().cumsum()
    if not len(committed):
        return None

    capital = float(run["stats"].get("InitialCapital") or 100_000)
    curve_path = Path(EXPERIMENTS / run["run"] / "equity_curve.csv")
    equity = None
    if curve_path.exists():
        try:
            curve = pd.read_csv(curve_path)
            curve["Date"] = pd.to_datetime(curve["Date"])
            equity = curve.set_index("Date")["Equity"].reindex(
                committed.index, method="ffill").fillna(capital)
        except Exception:                                        # noqa: BLE001
            equity = None
    if equity is None:
        equity = pd.Series(capital, index=committed.index)
    equity = equity.replace(0, np.nan).fillna(capital)
    return float((committed / equity * 100).max())


def buy_and_hold(symbols, start, end, charge_costs=True) -> dict:
    """Equal-weight buy-and-hold over a window, one round trip per name.

    Not rebalanced. A rebalanced basket returns more on this universe and would
    owe daily turnover costs nobody would pay; this is the version an investor
    could actually have held, so it pays the spread once and then nothing.
    """

    frame = prices_for(symbols)
    if frame is None or not len(frame):
        return {}
    columns = [s for s in symbols if s in frame.columns]
    if not columns:
        return {}
    window = frame.loc[(frame.index >= start) & (frame.index <= end), columns]
    if len(window) < 2:
        return {}

    returns, charged = [], []
    for symbol in columns:
        series = window[symbol].dropna()
        if len(series) < 2 or series.iloc[0] <= 0:
            continue
        gross = (series.iloc[-1] / series.iloc[0] - 1) * 100
        cost = round_trip(symbol) if charge_costs else 0.0
        if cost != cost:                                          # NaN
            cost = 0.0
        returns.append(gross - cost)
        charged.append(cost)
    if not returns:
        return {}

    years = (end - start).days / 365.25
    mean = float(np.mean(returns))
    return {
        "names": len(returns),
        "total_return": round(mean, 2),
        "cagr": (round(((1 + mean / 100) ** (1 / years) - 1) * 100, 2)
                 if years > 0 and mean > -100 else None),
        "median_name": round(float(np.median(returns)), 2),
        "mean_cost_charged": round(float(np.mean(charged)), 3),
        "years": round(years, 2),
    }


def eligible_universe() -> list[str]:
    from core.symbols import load_symbols
    return list(load_symbols())


def main() -> int:
    runs = sorted(p for p in EXPERIMENTS.iterdir() if p.is_dir())
    print(f"{len(runs)} run directories under reports/experiments")

    universe = eligible_universe()
    print(f"{len(universe)} symbols in the eligible universe\n")
    prices_for(universe)
    print(f"prices loaded for {0 if _PRICES is None else _PRICES.shape[1]} symbols\n")

    rows = []
    for run_dir in runs:
        run = read_run(run_dir)
        if run is None:
            rows.append({"run": run_dir.name, "usable": False})
            print(f"  {run_dir.name:<46} unusable")
            continue

        stats = run["stats"]
        traded = sorted(set(run["executed"]["symbol"].astype(str)))
        own = buy_and_hold(traded, run["start"], run["end"])
        market = buy_and_hold(universe, run["start"], run["end"])
        exposure = stats.get("ExposurePercent")
        exposure = float(exposure) if exposure == exposure and exposure else None
        cagr = stats.get("CAGR")
        cagr = float(cagr) if cagr == cagr and cagr is not None else None
        total = float(stats.get("TotalReturn") or 0.0)
        try:
            marked, marked_basis = marked_drawdown(run)
        except Exception as error:                               # noqa: BLE001
            marked, marked_basis = None, f"unmeasured: {type(error).__name__}"

        rows.append({
            "run": run_dir.name,
            "label": run["label"],
            "usable": True,
            "start": run["start"].date(),
            "end": run["end"].date(),
            "years": own.get("years"),
            "trades": int(stats.get("Trades") or 0),
            "total_return": total,
            "cagr": cagr,
            "published_dd": stats.get("MaxDrawdown"),
            "dd_basis": stats.get("DrawdownBasis"),
            "marked_dd": None if marked is None else round(marked, 2),
            "marked_basis": marked_basis,
            "sharpe": stats.get("SharpeRatio"),
            "exposure": exposure,
            "peak_deployed": (lambda v: None if v is None else round(v, 1))(
                peak_deployment(run)),
            "return_per_exposure": (round(cagr / exposure, 3)
                                    if cagr is not None and exposure else None),
            "traded_names": len(traded),
            "bh_own": own.get("total_return"),
            "bh_own_cagr": own.get("cagr"),
            "bh_own_names": own.get("names"),
            "bh_own_median_name": own.get("median_name"),
            "bh_market": market.get("total_return"),
            "bh_market_cagr": market.get("cagr"),
            "bh_market_names": market.get("names"),
            "bh_market_median_name": market.get("median_name"),
            "vs_own": (round(total - own["total_return"], 2)
                       if own.get("total_return") is not None else None),
            "vs_market": (round(total - market["total_return"], 2)
                          if market.get("total_return") is not None else None),
            "mean_cost_charged": own.get("mean_cost_charged"),
        })
        print(f"  {run_dir.name:<46} {total:>8.2f}%  exp {str(exposure):>6}  "
              f"own {str(own.get('total_return')):>9}  "
              f"mkt {str(market.get('total_return')):>9}")

    frame = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "exposure_vs_drift.csv"
    frame.to_csv(out, index=False)
    print(f"\nwrote {out.relative_to(PROJECT_ROOT)}")

    good = frame[frame["usable"] & frame["vs_own"].notna()]
    print(f"\n{len(good)} runs comparable\n")
    if len(good):
        beat_own = good[good["vs_own"] > 0]
        beat_market = good[good["vs_market"] > 0]
        print(f"beat buy-and-hold of their OWN traded names : "
              f"{len(beat_own)} of {len(good)}")
        print(f"beat buy-and-hold of the FULL universe      : "
              f"{len(beat_market)} of {len(good)}")
        both = good.dropna(subset=["exposure", "total_return"])
        if len(both) > 2:
            r = both["exposure"].corr(both["total_return"])
            rho = both["exposure"].corr(both["total_return"], method="spearman")
            print(f"\nreturn against average exposure over {len(both)} runs: "
                  f"pearson r = {r:.3f}, spearman rho = {rho:.3f}")
            print("  (this is a description of 30-odd points, not a law)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
