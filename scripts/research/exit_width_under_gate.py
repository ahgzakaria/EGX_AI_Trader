r"""Task I — the exit sweep under a working gate. The last strategy experiment.

The deficit this investigation ended on is not losses taken; it is upside never
captured — 57.67% against WEAK_BULL's 1,213.59% and 87.82% against BULL's
864.09% ([BENCHMARK_RISK_AND_REGIME §4]). Three measurements bear on that: 81%
of trades exit on the trailing stop at a mean -0.47% (DAILY_STRATEGY_DIAGNOSIS
§9), stop distance responds monotonically with tighter always worse in both eras
(CONFIRMED_VOLUME_BREAKOUT §4), and the causal gate from REGIME_DETECTABILITY
limits downside at 89.5% precision with one session of lag.

The trail and the gate do the same job. Removing the trail was tested when no
working gate existed. This tests it with one.

The gate used is the best-measured variant from that document: proxy drawdown
beyond 10%, with the account in USD while gated off. The hard stop is untouched
in every variant — the trailing logic only ever raises a stop, so disabling it
leaves the initial stop exactly where the strategy put it.

The bar was set before the result and is not moved: over the full window, in
USD, beat the ex-ante buy-and-hold basket on Calmar (+0.10) and not be matched
by a random gate of the same duty cycle.

    venv\Scripts\python.exe scripts\research\exit_width_under_gate.py
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

from exposure_vs_drift import Row, eligible_universe, prices_for   # noqa: E402
from exposure_usd_and_survivorship import usd_rate                 # noqa: E402
from benchmark_risk_profile import (WINDOW, ex_ante_basket,        # noqa: E402
                                    hold_basket, metrics, to_usd)
from regime_detectability import (detector_drawdown, gated_curve,  # noqa: E402
                                  liquid_half, panel_frames, proxy_index)

EXPERIMENTS = PROJECT_ROOT / "reports" / "experiments"
REGIMES = PROJECT_ROOT / "reports" / "phase11_market_regimes.csv"
OUT_DIR = PROJECT_ROOT / "reports" / "audits" / "strategies"

#: The sweep, widest last. Labels are the isolated-backtest run labels.
VARIANTS = [
    ("baseline EMA20 (shipped)", "exitsweep_ema20_baseline"),
    ("ATR 2.0", "exitsweep_atr2"),
    ("ATR 3.0", "exitsweep_atr3"),
    ("ATR 4.0", "exitsweep_atr4"),
    ("ATR 6.0", "exitsweep_atr6"),
    ("trailing disabled", "exitsweep_notrail"),
]
BAR_USD_CALMAR = 0.10
RANDOM_ITERATIONS = 100
SEED = 20260910


def find_run(label: str) -> Path | None:
    matches = sorted(p for p in EXPERIMENTS.iterdir()
                     if p.is_dir() and p.name.endswith(label))
    return matches[-1] if matches else None


def load_rows(run_dir: Path):
    stats = pd.read_csv(run_dir / "backtest_statistics.csv",
                        encoding="utf-8-sig").iloc[0].to_dict()
    frame = pd.read_csv(run_dir / "backtest_results.csv", encoding="utf-8-sig")
    rows = [Row(record) for record in frame.to_dict("records")]
    return [r for r in rows if r.executed and r.shares], stats, frame


def trade_shape(frame: pd.DataFrame, kept_symbols_dates=None) -> dict:
    """Win rate, median trade and how much of the profit sits in five trades."""

    executed = frame[frame["executed"].astype(str).str.lower().isin(("true", "1"))]
    if kept_symbols_dates is not None:
        keys = set(kept_symbols_dates)
        executed = executed[[(s, d) in keys for s, d in
                             zip(executed["symbol"].astype(str),
                                 executed["entry_date"].astype(str))]]
    if not len(executed):
        return {"trades": 0}
    profit = executed["portfolio_profit"].astype(float)
    percent = executed["profit_percent"].astype(float)
    net = float(profit.sum())
    best5 = float(profit.nlargest(5).sum())
    return {
        "trades": len(executed),
        "win_rate": round(float((profit > 0).mean() * 100), 1),
        "median_trade": round(float(percent.median()), 3),
        "best5_share_of_net": (round(best5 / net * 100, 1) if net > 0 else None),
    }


def regime_capture(curve: pd.Series, market: pd.Series) -> dict:
    """Share of each regime's move the variant captured. The hypothesis metric."""

    if not REGIMES.exists() or not len(curve):
        return {}
    labels = pd.read_csv(REGIMES, usecols=["Date", "Regime"])
    labels["Date"] = pd.to_datetime(labels["Date"])
    out = {}
    for wanted in ("WEAK_BULL", "BULL"):
        days = pd.DatetimeIndex(labels.loc[labels["Regime"] == wanted, "Date"])
        s = curve.pct_change().reindex(curve.index.intersection(days)).dropna()
        b = market.pct_change().reindex(market.index.intersection(days)).dropna()
        if len(s) < 5 or len(b) < 5:
            out[f"capture_{wanted}"] = None
            continue
        s_total = (1 + s).prod() - 1
        b_total = (1 + b).prod() - 1
        out[f"return_{wanted}"] = round(s_total * 100, 2)
        out[f"benchmark_{wanted}"] = round(b_total * 100, 2)
        out[f"capture_{wanted}"] = (round(s_total / b_total * 100, 1)
                                    if b_total > 0 else None)
    return out


def describe(curve, label, rate, shape, market) -> dict:
    egp = metrics(curve, label)
    usd = metrics(to_usd(curve, rate), label) if rate is not None else {}
    row = {
        "variant": label,
        **shape,
        "egp_return": egp.get("total_return"), "egp_cagr": egp.get("cagr"),
        "egp_dd": egp.get("max_dd"), "egp_underwater_days": egp.get("longest_dd_days"),
        "egp_sharpe": egp.get("sharpe"), "egp_sortino": egp.get("sortino"),
        "egp_calmar": egp.get("calmar"),
        "usd_return": usd.get("total_return"), "usd_cagr": usd.get("cagr"),
        "usd_dd": usd.get("max_dd"), "usd_underwater_days": usd.get("longest_dd_days"),
        "usd_sharpe": usd.get("sharpe"), "usd_sortino": usd.get("sortino"),
        "usd_calmar": usd.get("calmar"),
        **regime_capture(curve, market),
    }
    row["too_small"] = bool(row.get("trades") is not None and row["trades"] < 30)
    row["clears_bar"] = (None if row["usd_calmar"] is None
                         else bool(row["usd_calmar"] > BAR_USD_CALMAR))
    return row


def main() -> int:
    start, end = WINDOW
    universe = eligible_universe()
    prices_for(universe)
    rate = usd_rate()
    closes, volumes = panel_frames()
    members = liquid_half(closes, volumes, start)
    index = proxy_index(closes, members)
    gate = detector_drawdown(index, 10)
    duty = float(gate.loc[(gate.index >= start) & (gate.index <= end)]
                 .fillna(False).mean())

    basket = ex_ante_basket(universe, start, 30)
    market = hold_basket(basket, start, end)
    print(f"gate: proxy drawdown beyond 10%, off {duty * 100:.1f}% of days, "
          f"account in USD while off")
    print(f"bar : USD Calmar of the ex-ante top-30 basket = {BAR_USD_CALMAR}\n")

    rows = [describe(market, "(a) ex-ante top-30 basket, held", rate,
                     {"trades": None}, market)]

    for label, run_label in VARIANTS:
        run_dir = find_run(run_label)
        if run_dir is None:
            rows.append({"variant": f"{label} | GATED", "trades": None,
                         "note": "run missing"})
            print(f"  {label:<28} run not found - reported as None")
            continue
        trades, stats, frame = load_rows(run_dir)

        for gated, gate_label, fx in ((gate, "GATED USD-off", rate),
                                      (None, "gate OFF (control b)", None)):
            curve, kept = gated_curve(trades, stats, gated, off_state_fx=fx)
            if not len(curve):
                rows.append({"variant": f"{label} | {gate_label}", "trades": kept})
                continue
            if gated is not None:
                flagged = gated.fillna(False)
                keys = [(r.symbol, r.entry_date) for r in trades
                        if not bool(flagged.get(pd.Timestamp(r.entry_date), False))]
            else:
                keys = None
            rows.append(describe(curve, f"{label} | {gate_label}", rate,
                                 trade_shape(frame, keys), market))
        print(f"  {label:<28} done")

    frame = pd.DataFrame(rows)
    pd.set_option("display.width", 260)

    print("\n" + "=" * 120)
    print("TASK I - THE SWEEP, GATED AND UNGATED")
    print("=" * 120)
    show = ["variant", "trades", "win_rate", "median_trade", "best5_share_of_net",
            "egp_return", "egp_dd", "egp_calmar",
            "usd_return", "usd_dd", "usd_calmar", "clears_bar", "too_small"]
    print(frame[[c for c in show if c in frame]].to_string(
        index=False, float_format=lambda v: f"{v:,.2f}"))

    print("\n" + "=" * 120)
    print("UPSIDE CAPTURE - THE METRIC THAT PRODUCED THE HYPOTHESIS")
    print("=" * 120)
    cap = ["variant", "return_WEAK_BULL", "benchmark_WEAK_BULL", "capture_WEAK_BULL",
           "return_BULL", "benchmark_BULL", "capture_BULL"]
    print(frame[[c for c in cap if c in frame]].to_string(
        index=False, float_format=lambda v: f"{v:,.2f}"))
    frame.to_csv(OUT_DIR / "exit_width_sweep.csv", index=False)

    # --- control (c) --------------------------------------------------------
    print("\n" + "=" * 120)
    print("CONTROL (c) - RANDOM GATE, SAME DUTY CYCLE, 100 ITERATIONS")
    print("=" * 120)
    rng = np.random.default_rng(SEED)
    best = frame.dropna(subset=["usd_calmar"])
    best = best[best["variant"].str.contains("GATED")]
    if not len(best):
        print("  no gated variant produced a curve")
        return 0
    winner = best.loc[best["usd_calmar"].idxmax()]
    print(f"  best gated variant: {winner['variant']}  USD Calmar "
          f"{winner['usd_calmar']}")

    run_label = next(rl for lb, rl in VARIANTS if winner["variant"].startswith(lb))
    trades, stats, _ = load_rows(find_run(run_label))
    window = gate.loc[(gate.index >= start) & (gate.index <= end)].fillna(False)
    draws = []
    for _ in range(RANDOM_ITERATIONS):
        random_flags = pd.Series(rng.random(len(window)) < duty, index=window.index)
        curve, _ = gated_curve(trades, stats, random_flags, off_state_fx=rate)
        if len(curve):
            m = metrics(to_usd(curve, rate), "r")
            if m.get("calmar") is not None:
                draws.append(m["calmar"])
    if draws:
        pct = float((np.array(draws) < winner["usd_calmar"]).mean() * 100)
        print(f"  random median {np.median(draws):.3f}   best {np.max(draws):.3f}   "
              f"real at the {pct:.0f}th percentile")
        print(f"  beats random median: {winner['usd_calmar'] > np.median(draws)}")
        pd.DataFrame({"usd_calmar": draws}).to_csv(
            OUT_DIR / "exit_width_random_control.csv", index=False)

    print("\n" + "=" * 120)
    verdict = ("CLEARS" if winner["usd_calmar"] > BAR_USD_CALMAR else "MISSES")
    print(f"THE BAR: {winner['usd_calmar']} against {BAR_USD_CALMAR} -> {verdict}")
    print("=" * 120)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
