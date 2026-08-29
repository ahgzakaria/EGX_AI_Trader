r"""What the portfolio limits cost CONFIRMED_VOLUME_BREAKOUT, measured.

The strategy generates 927 trades over the test window. Under the limits as they
stood before 2026-08-29 the simulator executed 299 of them and refused 628 for
capacity, which made capacity — not the rule — the binding constraint on the
result. This is the sweep that led to changing them to 1% / 15% / 15, where 634
are executed. It still runs, and re-running it now sweeps around the new point.

Two properties of `PortfolioSimulator` decide how the output has to be read, and
both are easy to miss:

* **Position size is computed from `initial_capital`, never from current
  equity** (`PortfolioSimulator._enter` constructs
  `PositionSizer(self.initial_capital, ...)` on every trade). So a position is
  the same number of EGP in 2026 as in 2017, and the equity curve is a *sum of
  fixed-size bets* rather than a compounded one. Total return is therefore
  close to linear in `risk_percent`: doubling it roughly doubles the return and
  roughly doubles the drawdown, and that is arithmetic, not an improvement.
  **`TotalReturn` must not be read as a ranking across risk levels.** Sharpe,
  Calmar and the share of signals taken can be.
* **There are two caps, and they interact.** `effective_max_positions` is
  `min(max_open_positions, floor(max_portfolio_risk_percent / risk_percent))`,
  and there is a *separate* live heat check on the sum of open risk. At the old
  2.0 / 10.0 the floor division gave **5**, so `max_open_positions: 10` was
  never the limit. At 1.0 / 15.0 / 15 both terms are 15, so neither overrides
  the other -- which `tests/test_confirmed_volume_breakout.py` now pins.

The trades are generated once and re-simulated under each setting, so every row
sees the identical signal stream and the difference is the portfolio policy.

    venv\Scripts\python.exe scripts\research\capacity_sweep.py
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import math
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from core.environment import load_project_environment

load_project_environment()

from backtesting.equity import EquityCurve                          # noqa: E402
from backtesting.prices import closes_for_trades                    # noqa: E402
from strategy_momentum_breakout.config import load as load_config   # noqa: E402
from strategy_momentum_breakout.runner import run, simulate         # noqa: E402


ROW = ("{label:<26}{cap:>5}{taken:>7}{share:>7.0f}%{ret:>9.1f}%{dd:>8.1f}%"
       "{calmar:>8.2f}{sharpe:>8.2f}{expo:>8.1f}%{avg:>8.2f}%{worst:>8.1f}%")
HEAD = (f"{'setting':<26}{'cap':>5}{'taken':>7}{'of all':>8}{'return':>9}"
        f"{'maxDD':>8}{'calmar':>8}{'sharpe':>8}{'expo':>9}{'avg/tr':>8}"
        f"{'worst yr':>9}")


def worst_year(executed):
    if not executed:
        return float("nan")
    frame = pd.DataFrame([{"year": int(str(t.entry_date)[:4]),
                           "profit": t.portfolio_profit} for t in executed])
    return frame.groupby("year")["profit"].sum().min() / 1000.0


def peak_concurrency(executed, initial_capital):
    """The most positions open at once, and how much of the account funded them.

    A cap of thirty does not mean thirty are ever held, and the average exposure
    figure hides the moment the account is most committed — which is the moment
    a real account has to fund.

    The denominator is equity **at that moment**, not the starting 100,000. An
    earlier version of this used the starting capital and reported "peak capital
    203,392 against a 100,000 account", which reads as two-times leverage and is
    not: by the time that peak occurs the account has earned its way there. The
    simulator's own cash check would have refused it otherwise, and the cash
    refusals column is where that constraint actually shows.
    """
    events = []
    for trade in executed:
        cost = trade.shares * trade.entry_price
        events.append((str(trade.entry_date), 1, cost, 0.0))
        events.append((str(trade.exit_date), -1, -cost, trade.portfolio_profit))
    open_count = peak_count = 0
    committed = 0.0
    equity = initial_capital
    peak_share = 0.0
    peak_committed = 0.0
    # Exits settle before entries on a date, matching the simulator.
    for _date, delta, cost, realised in sorted(events, key=lambda e: (e[0], e[1])):
        open_count += delta
        committed += cost
        equity += realised
        peak_count = max(peak_count, open_count)
        peak_committed = max(peak_committed, committed)
        if equity > 0:
            peak_share = max(peak_share, committed / equity * 100)
    return peak_count, peak_committed, peak_share


def mark_to_market(executed, closes, initial_capital):
    """The drawdown an account would actually have watched, day by day.

    This sweep is where the defect was found: `backtesting/equity.py` booked
    profit only at exit and never valued an open position, so its curve could
    not fall while fifteen positions were open and losing — and it hid more the
    more were held, which is the variable being swept here.

    The curve is now built by `EquityCurve.daily_curve`, the production one, so
    this file cannot drift from what the summaries report. It was originally a
    separate implementation here; the two agreed to 11.5% against 11.54% on the
    shipped configuration, which is what gave the confidence to move the logic
    into `backtesting/`. What stays local is only the two extra statistics the
    summaries do not carry.
    """
    if not executed:
        return None
    series = EquityCurve(
        executed, initial_capital=initial_capital, prices=closes).daily_curve()
    if not len(series):
        return None

    drawdown = (series / series.cummax() - 1) * 100
    daily = series.pct_change() * 100
    return {"max_drawdown": -drawdown.min(),
            "final": series.iloc[-1],
            "return": (series.iloc[-1] / initial_capital - 1) * 100,
            # The correlation test. If holding fifteen names at once were really
            # fifteen independent bets, the worst single session would grow far
            # more slowly than the position count. If they all fall together on
            # the bad days, it grows in step with it.
            "worst_day": daily.min(),
            "worst_month": (series / series.shift(21) - 1).min() * 100}


def compounded(executed, initial_capital):
    """The same trades, sized off running equity instead of initial capital.

    `PortfolioSimulator` sizes every position from `initial_capital`, so a
    position is the same number of EGP in 2026 as in 2017 and late drawdowns are
    damped against an equity base that has grown. That flatters any setting that
    earns its money late — which is every setting here, because the signal count
    rises through the window.

    This re-runs the identical trade list with each position sized off equity as
    it stands on the entry date. Selection is unchanged, which is legitimate
    only because the sweep shows the cash constraint never binds; where it does,
    the row is flagged rather than silently rescaled.
    """
    if not executed:
        return None
    rows = sorted(executed, key=lambda t: str(t.entry_date))
    # Realise profits on their exit date, so equity at entry reflects only what
    # has actually closed by then.
    pending = []
    equity = initial_capital
    curve = []
    for trade in rows:
        entry_date = str(trade.entry_date)
        for closed in [p for p in pending if p[0] <= entry_date]:
            equity += closed[1]
            curve.append((closed[0], equity))
        pending = [p for p in pending if p[0] > entry_date]
        # The same fraction of equity the fixed-size position was of initial.
        scale = equity / initial_capital
        pending.append((str(trade.exit_date), trade.portfolio_profit * scale))
    for closed in sorted(pending):
        equity += closed[1]
        curve.append((closed[0], equity))

    values = pd.Series([value for _date, value in curve])
    drawdown = (values / values.cummax() - 1) * 100
    return {"final": equity,
            "return": (equity / initial_capital - 1) * 100,
            "max_drawdown": -drawdown.min()}


def measure(trades, cfg, label, **overrides):
    variant = replace(cfg, **overrides)
    simulation, executed, summary = simulate(trades, variant)
    if not executed:
        print(f"{label:<26}{'no trades':>10}")
        return None
    cap = simulation["effective_max_positions"]
    row = dict(
        label=label, cap=cap, taken=len(executed),
        share=100.0 * len(executed) / len(trades),
        ret=summary["TotalReturn"], dd=summary["MaxDrawdown"],
        calmar=summary["CalmarRatio"], sharpe=summary["SharpeRatio"],
        expo=summary["ExposurePercent"],
        avg=summary["AverageProfitPercent"],
        worst=worst_year(executed),
    )
    print(ROW.format(**row))
    return {**row, "rejections": simulation["rejection_reasons"],
            "executed": executed, "summary": summary}


def selection_quality(trades, taken):
    """Does the capacity limit keep the better trades or merely the earlier ones?

    When more signals arrive on a day than there is room for, the simulator
    ranks them by `selection_key`, which for this strategy falls through to
    `score` — and this strategy has no score, so `backtest.py` puts the volume
    ratio there as an explicit capacity tie-break. Whether that tie-break helps
    is a separate question from whether the rule works, and it has not been
    measured until here.
    """
    executed = {(t.symbol, t.entry_date) for t in taken}
    rows = [{"pct": (t.exit_price / t.entry_price - 1) * 100,
             "taken": (t.symbol, t.entry_date) in executed,
             "volume_ratio": t.volume_ratio}
            for t in trades]
    frame = pd.DataFrame(rows)
    kept = frame[frame["taken"]]["pct"]
    dropped = frame[~frame["taken"]]["pct"]
    print(f"\n  taken   {len(kept):>4} trades, mean {kept.mean():+.2f}%, "
          f"median {kept.median():+.2f}%, win {(kept > 0).mean() * 100:.0f}%")
    print(f"  refused {len(dropped):>4} trades, mean {dropped.mean():+.2f}%, "
          f"median {dropped.median():+.2f}%, win {(dropped > 0).mean() * 100:.0f}%")
    difference = kept.mean() - dropped.mean()
    pooled = math.sqrt(kept.var() / len(kept) + dropped.var() / len(dropped))
    print(f"  difference {difference:+.2f}%  (t = {difference / pooled:+.2f})")
    return difference / pooled


def main() -> int:
    cfg = load_config()
    print("Generating the signal stream once; every row below re-simulates it.")
    trades, errors, _ = run()
    print(f"{len(trades):,} trades from {241 - len(errors)} readable symbols\n")

    print("=" * 104)
    print(f"A. RISK PER TRADE  (heat {cfg.max_portfolio_risk_percent:g}%, "
          f"max_open_positions {cfg.max_open_positions})")
    print("=" * 104)
    print("Position size is fixed in EGP off initial capital, so `return` here is")
    print("close to linear in risk and is NOT a ranking. Read `sharpe`, `calmar`")
    print("and `taken`.")
    print(HEAD)
    print("-" * 104)
    baseline = None
    for risk in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
        marker = " <- shipped" if risk == cfg.risk_percent else ""
        result = measure(trades, cfg, f"risk {risk}%{marker}", risk_percent=risk)
        if risk == cfg.risk_percent:
            baseline = result

    print("\n" + "=" * 104)
    print(f"B. PORTFOLIO HEAT  (risk {cfg.risk_percent:g}%, "
          f"max_open_positions {cfg.max_open_positions})")
    print("=" * 104)
    print(HEAD)
    print("-" * 104)
    for heat in (5.0, 10.0, 15.0, 20.0, 30.0, 40.0):
        marker = " <- shipped" if heat == cfg.max_portfolio_risk_percent else ""
        measure(trades, cfg, f"heat {heat:g}%{marker}",
                max_portfolio_risk_percent=heat)
    print("\nSections A and B hold the *other two* settings at whatever is")
    print("currently configured, so they sweep around the shipped point rather")
    print("than around a fixed one. The tables in CAPACITY_IS_THE_CONSTRAINT.md")
    print("sections 3 and 4 were measured from the pre-2026-08-29 baseline of")
    print("2% / 10% / 10 and will not reproduce from here; sections 5 and 7 set")
    print("all three explicitly and do reproduce.")

    print("\n" + "=" * 104)
    print("C. THE CAP ITSELF, holding risk-per-position constant at 1%")
    print("=" * 104)
    print("Raising heat alone raises the cap AND the risk taken. This raises the")
    print("cap only: heat is set to exactly 1% x the number of positions, so each")
    print("row risks the same 1% per trade and differs only in how many it holds.")
    print(HEAD)
    print("-" * 104)
    capacity = {}
    for positions in (3, 5, 8, 10, 15, 20, 30, 40):
        capacity[positions] = measure(
            trades, cfg, f"{positions} positions @1%",
            risk_percent=1.0, max_portfolio_risk_percent=float(positions),
            max_open_positions=positions)

    print("\n  What each of those actually demanded of the account, and what the")
    print("  result looks like once positions are sized off running equity")
    print("  rather than off the initial 100,000 the simulator always uses:")
    print(f"\n  {'cap':>5}{'peak held':>11}{'peak EGP':>11}{'of equity':>11}"
          f"{'cash refused':>14}{'fixed ret':>11}{'fixed DD':>10}"
          f"{'compounded':>12}{'comp. DD':>10}")
    print("  " + "-" * 92)
    for positions, result in capacity.items():
        if result is None:
            continue
        held, committed, share = peak_concurrency(
            result["executed"], cfg.initial_capital)
        grown = compounded(result["executed"], cfg.initial_capital)
        print(f"  {result['cap']:>5}{held:>11}{committed:>11,.0f}{share:>10.0f}%"
              f"{result['rejections']['Capital']:>14,}"
              f"{result['ret']:>10.1f}%{result['dd']:>9.1f}%"
              f"{grown['return']:>11.1f}%{grown['max_drawdown']:>9.1f}%")
    print("\n  'of equity' is the most of the account committed at any one moment,")
    print("  against equity as it stood then. 'cash refused' is the simulator's")
    print("  own constraint biting: a row with refusals is a row the account")
    print("  could not fund, whatever the position cap allowed.")

    print("\n" + "=" * 104)
    print("C2. WHAT THE OLD, CLOSED-TRADE DRAWDOWN WOULD HAVE SAID")
    print("=" * 104)
    print("Until 2026-08-29 `backtesting/equity.py` booked profit only at exit and")
    print("never valued an open position, so its curve could not fall while a")
    print("position was open and losing -- and it hid more the more positions were")
    print("held, which is precisely the variable swept here. That is the defect")
    print("this sweep found; it is fixed, and `maxDD` above is already marked.")
    print("`BacktestStatistics` still reports the old figure as")
    print("`MaxDrawdownClosedTrades`, so the two can be read against each other:")
    print(f"\n  {'cap':>5}{'taken':>8}{'closed-trade DD':>18}{'marked DD':>12}"
          f"{'understated by':>16}")
    print("  " + "-" * 60)
    for positions, result in capacity.items():
        if result is None:
            continue
        closed = float(result["summary"]["MaxDrawdownClosedTrades"])
        marked = float(result["summary"]["MaxDrawdown"])
        print(f"  {result['cap']:>5}{result['taken']:>8}{closed:>17.1f}%"
              f"{marked:>11.1f}%{marked - closed:>15.1f}pp")

    print("\n" + "=" * 104)
    print("D. WHERE THE REFUSALS COME FROM")
    print("=" * 104)
    print(f"{'setting':<26}{'MaxPositions':>14}{'Heat':>8}{'Capital':>9}{'Overlap':>9}")
    print("-" * 66)
    for label, overrides in [
        ("previous 2% / 10% / 10", dict(risk_percent=2.0,
                                        max_portfolio_risk_percent=10.0,
                                        max_open_positions=10)),
        ("shipped 1% / 15% / 15", dict(risk_percent=1.0,
                                       max_portfolio_risk_percent=15.0,
                                       max_open_positions=15)),
        ("10 positions @1%", dict(risk_percent=1.0,
                                  max_portfolio_risk_percent=10.0,
                                  max_open_positions=10)),
        ("20 positions @1%", dict(risk_percent=1.0,
                                  max_portfolio_risk_percent=20.0,
                                  max_open_positions=20)),
        ("30 positions @0.5%", dict(risk_percent=0.5,
                                    max_portfolio_risk_percent=15.0,
                                    max_open_positions=30)),
    ]:
        simulation, _executed, _summary = simulate(trades, replace(cfg, **overrides))
        reasons = simulation["rejection_reasons"]
        print(f"{label:<26}{reasons['MaxPositions']:>14,}{reasons['Heat']:>8,}"
              f"{reasons['Capital']:>9,}{reasons['Overlap']:>9,}")

    print("\n" + "=" * 104)
    print("E. IS THE CAPACITY TIE-BREAK SELECTING, OR JUST RATIONING?")
    print("=" * 104)
    print("At the shipped setting:")
    selection_quality(trades, baseline["executed"])

    print("\n" + "=" * 104)
    print("F. THE CANDIDATES, ON LIKE-FOR-LIKE TERMS")
    print("=" * 104)
    print("Everything marked to market daily, so the drawdowns are comparable")
    print("to each other and to what an account would have watched.")
    # The production price source, so these rows and the summaries agree by
    # construction rather than because two loaders happen to match.
    closes = closes_for_trades(trades)
    print(f"\n{'setting':<28}{'cap':>5}{'taken':>7}{'of all':>8}{'return':>9}"
          f"{'marked DD':>11}{'ret/DD':>8}{'worst day':>11}{'worst 21d':>11}"
          f"{'of equity':>11}{'cash refused':>14}")
    print("-" * 124)
    # Every row sets all three explicitly. Inheriting the unset ones from the
    # live config made the first three rows identical to each other while their
    # labels claimed otherwise -- the labels were describing settings the row
    # was not using.
    for label, overrides in [
        ("previous: 2% / 10% / 10", dict(risk_percent=2.0,
                                         max_portfolio_risk_percent=10.0,
                                         max_open_positions=10)),
        ("1% / 10% / 10", dict(risk_percent=1.0,
                               max_portfolio_risk_percent=10.0,
                               max_open_positions=10)),
        ("1% / 15% / 15  <- shipped", dict(risk_percent=1.0,
                                           max_portfolio_risk_percent=15.0,
                                           max_open_positions=15)),
        ("1% / 20% / 20", dict(risk_percent=1.0,
                               max_portfolio_risk_percent=20.0,
                               max_open_positions=20)),
        ("1% / 30% / 30", dict(risk_percent=1.0,
                               max_portfolio_risk_percent=30.0,
                               max_open_positions=30)),
        ("1.5% / 22.5% / 15", dict(risk_percent=1.5,
                                   max_portfolio_risk_percent=22.5,
                                   max_open_positions=15)),
    ]:
        variant = replace(cfg, **overrides)
        simulation, executed, summary = simulate(trades, variant)
        marked = mark_to_market(executed, closes, cfg.initial_capital)
        held, _committed, share = peak_concurrency(executed, cfg.initial_capital)
        print(f"{label:<28}{simulation['effective_max_positions']:>5}"
              f"{len(executed):>7}{100.0 * len(executed) / len(trades):>7.0f}%"
              f"{marked['return']:>8.1f}%{marked['max_drawdown']:>10.1f}%"
              f"{marked['return'] / marked['max_drawdown']:>8.1f}"
              f"{marked['worst_day']:>10.2f}%{marked['worst_month']:>10.1f}%"
              f"{share:>10.0f}%"
              f"{simulation['rejection_reasons']['Capital']:>14,}")

    print("\nThe cost of the last two rows is not in these columns: at 96-100% of")
    print("equity committed there is no cash buffer left, and this backtest cannot")
    print("price that. It has no gap risk beyond the daily close, no margin, and")
    print("no forced liquidation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
