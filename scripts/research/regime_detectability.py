r"""Tasks G and H — is the BEAR advantage detectable at the time, and does the
overlay have anywhere to stand when it is off.

BENCHMARK_RISK_AND_REGIME.md found one slice where the shipped strategy wins with
an adequate sample: the BEAR regime, 97 trades, losing 44.50% where the market
lost 91.73%. Those labels come from `phase11_market_regimes.csv`, which is model
output. A regime-conditional product whose regime detector is a label assigned
afterwards is not a product.

**G** builds the detector from the panel instead of waiting for an index the
providers do not serve. Every detector reads only data up to and including the
day it flags; any lookahead is a bug, not a calibration choice. Agreement with
the phase11 labels is reported as a diagnostic and is not the target -- the
labels are the thing under suspicion, so tuning a detector to match them would
answer nothing.

**H** gates the shipped run by each detector and states the off-state rather than
assuming it. The panel holds 191 symbols and every one is an EGX common share,
so there is no defensive instrument in this project's data. EGP cash and USD
cash are the two available, and Task D already showed EGP cash stops being
defensive once results are stated in dollars.

The bar was set before the result: the overlay is a product only if, over the
full window and in USD, it beats the buy-and-hold basket on Calmar while not
being beaten by a random gate of the same duty cycle.

    venv\Scripts\python.exe scripts\research\regime_detectability.py
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
from exposure_vs_drift import Row, eligible_universe, prices_for  # noqa: E402
from exposure_usd_and_survivorship import usd_rate                # noqa: E402
from benchmark_risk_profile import (WINDOW, ex_ante_basket,       # noqa: E402
                                    hold_basket, metrics, to_usd)

PANEL = PROJECT_ROOT / "data" / "research" / "backtest_panel.parquet"
SHIPPED = PROJECT_ROOT / "reports" / "experiments" / "20260827_193224_reward_guard"
REGIMES = PROJECT_ROOT / "reports" / "phase11_market_regimes.csv"
OUT_DIR = PROJECT_ROOT / "reports" / "audits" / "strategies"

RANDOM_ITERATIONS = 100
SEED = 20260910


# --- the proxy ---------------------------------------------------------------

def panel_frames():
    frame = pd.read_parquet(PANEL, columns=["Date", "Symbol", "Close", "Volume"])
    frame["Date"] = pd.to_datetime(frame["Date"])
    closes = frame.pivot_table(index="Date", columns="Symbol", values="Close")
    volumes = frame.pivot_table(index="Date", columns="Symbol", values="Volume")
    return closes.sort_index(), volumes.sort_index()


def liquid_half(closes, volumes, as_of) -> list[str]:
    """The more liquid half, chosen on the year BEFORE `as_of`. Causal."""

    lookback = closes.loc[(closes.index >= as_of - pd.Timedelta(days=365))
                          & (closes.index < as_of)]
    vol = volumes.reindex(lookback.index)
    shared = [c for c in lookback.columns if c in vol.columns]
    value = (lookback[shared] * vol[shared]).median(axis=0, skipna=True).dropna()
    if value.empty:
        return []
    return list(value.sort_values(ascending=False).head(max(1, len(value) // 2)).index)


def proxy_index(closes, members, weights=None) -> pd.Series:
    """Equal-weight daily index of `members`, rebased to 1.0 at its first bar.

    Built from daily returns rather than price levels so a name that lists late
    joins the index at its own first bar instead of dragging the level.
    """

    frame = closes[[m for m in members if m in closes.columns]]
    daily = frame.pct_change()
    if weights is None:
        composite = daily.mean(axis=1, skipna=True)
    else:
        w = pd.Series(weights).reindex(daily.columns).fillna(0.0)
        w = w / w.sum() if w.sum() else w
        composite = (daily * w).sum(axis=1, min_count=1)
    return (1 + composite.fillna(0.0)).cumprod()


# --- G: the detectors, every one causal --------------------------------------

def detector_below_ma(index: pd.Series, window=200) -> pd.Series:
    """Flag when the proxy closes below its own trailing average."""

    return index < index.rolling(window, min_periods=window).mean()


def detector_drawdown(index: pd.Series, threshold_pct: float) -> pd.Series:
    """Flag when the proxy is more than `threshold_pct` below its running peak."""

    return (index / index.cummax() - 1) * 100 <= -abs(threshold_pct)


def detector_breadth(closes, members, share: float, window=200) -> pd.Series:
    """Flag when more than `share` of the members sit below their own average."""

    frame = closes[[m for m in members if m in closes.columns]]
    below = frame < frame.rolling(window, min_periods=window).mean()
    valid = frame.rolling(window, min_periods=window).mean().notna()
    counted = valid.sum(axis=1)
    return (below.sum(axis=1) / counted.replace(0, np.nan)) > share


def detector_volatility(index: pd.Series, percentile: float,
                        vol_window=21, rank_window=250) -> pd.Series:
    """Flag when realized volatility sits above its own trailing percentile.

    The rank is taken over the trailing window only, so the threshold at date t
    is computed from history t knows about.
    """

    realized = index.pct_change().rolling(vol_window).std()
    rank = realized.rolling(rank_window, min_periods=rank_window).apply(
        lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    return rank > percentile


def assert_causal(flags: pd.Series, index: pd.Series) -> bool:
    """Recompute the flag on a truncated history and require the past to agree.

    A detector that reads the future changes its mind about an old date once
    later data arrives. This checks that it does not.
    """

    cut = len(index) // 2
    truncated = index.iloc[:cut]
    again = detector_below_ma(truncated)
    overlap = flags.reindex(again.index).dropna()
    common = again.dropna().index.intersection(overlap.index)
    if not len(common):
        return True
    return bool((again.loc[common] == flags.loc[common]).all())


def label_agreement(flags: pd.Series) -> dict:
    """How the flags line up with the phase11 BEAR label. A diagnostic only."""

    if not REGIMES.exists():
        return {}
    labels = pd.read_csv(REGIMES, usecols=["Date", "Regime"])
    labels["Date"] = pd.to_datetime(labels["Date"])
    labels = labels.set_index("Date")["Regime"]
    common = flags.index.intersection(labels.index)
    if not len(common):
        return {}
    flag = flags.reindex(common).fillna(False)
    bear = labels.reindex(common) == "BEAR"

    def episodes(series):
        out, start = [], None
        for when, on in series.items():
            if on and start is None:
                start = when
            elif not on and start is not None:
                out.append((start, when))
                start = None
        if start is not None:
            out.append((start, series.index[-1]))
        return out

    entry_lags, exit_lags = [], []
    positions = {d: i for i, d in enumerate(common)}
    for b_start, b_end in episodes(bear):
        nearest_start = [f for f, _ in episodes(flag)
                         if abs(positions[f] - positions[b_start]) <= 120]
        if nearest_start:
            best = min(nearest_start, key=lambda f: abs(positions[f] - positions[b_start]))
            entry_lags.append(positions[best] - positions[b_start])
        nearest_end = [e for _, e in episodes(flag)
                       if abs(positions[e] - positions[b_end]) <= 120]
        if nearest_end:
            best = min(nearest_end, key=lambda e: abs(positions[e] - positions[b_end]))
            exit_lags.append(positions[best] - positions[b_end])

    both = int((flag & bear).sum())
    return {
        "days_flagged": int(flag.sum()),
        "days_bear": int(bear.sum()),
        "overlap_days": both,
        "precision": round(both / max(1, int(flag.sum())) * 100, 1),
        "recall": round(both / max(1, int(bear.sum())) * 100, 1),
        "entry_lag_median_days": (int(np.median(entry_lags)) if entry_lags else None),
        "exit_lag_median_days": (int(np.median(exit_lags)) if exit_lags else None),
    }


# --- H: gating the shipped run ----------------------------------------------

def shipped_rows():
    stats = pd.read_csv(SHIPPED / "backtest_statistics.csv",
                        encoding="utf-8-sig").iloc[0].to_dict()
    frame = pd.read_csv(SHIPPED / "backtest_results.csv", encoding="utf-8-sig")
    rows = [Row(record) for record in frame.to_dict("records")]
    return [r for r in rows if r.executed and r.shares], stats


def gated_curve(rows, stats, blocked: pd.Series | None,
                off_state_fx: pd.Series | None = None) -> tuple[pd.Series, int]:
    """Marked daily equity of the trades that survive the gate.

    A trade is suppressed when its ENTRY date is flagged off; positions already
    open are left to run, because a gate that also liquidates is a different
    strategy and this one is not being redesigned.

    `off_state_fx` moves the account with the exchange rate on days the gate is
    off and nothing is held -- the USD-cash off-state. None keeps EGP cash,
    which earns nothing.
    """

    if blocked is None:
        kept = rows
    else:
        flagged = blocked.fillna(False)
        kept = [r for r in rows
                if not bool(flagged.get(pd.Timestamp(r.entry_date), False))]
    if not kept:
        return pd.Series(dtype=float), 0

    curve = EquityCurve(kept,
                        initial_capital=float(stats.get("InitialCapital") or 100_000),
                        prices=prices_for(sorted({r.symbol for r in kept})))
    daily = curve.daily_curve()
    series = daily if isinstance(daily, pd.Series) else pd.Series(dtype=float)
    if not isinstance(daily, pd.Series):
        frame = pd.DataFrame(daily)
        column = "Equity" if "Equity" in frame else frame.columns[-1]
        if "Date" in frame:
            frame = frame.set_index(pd.to_datetime(frame["Date"]))
        series = frame[column]
    series = series.dropna()

    if off_state_fx is not None and blocked is not None and len(series):
        held = pd.Series(False, index=series.index)
        for r in kept:
            held.loc[(held.index >= pd.Timestamp(r.entry_date))
                     & (held.index <= pd.Timestamp(r.exit_date))] = True
        idle_off = (~held) & blocked.reindex(series.index).fillna(False)
        # EGP per USD rising means a dollar balance is worth more in pounds.
        fx = off_state_fx.reindex(series.index).ffill()
        step = fx.pct_change().fillna(0.0).where(idle_off, 0.0)
        series = series * (1 + step).cumprod()
    return series, len(kept)


def summarise(curve, label, rate, trades) -> dict:
    egp = metrics(curve, label)
    usd = metrics(to_usd(curve, rate), label) if rate is not None else {}
    return {
        "line": label, "trades": trades,
        "egp_return": egp.get("total_return"), "egp_cagr": egp.get("cagr"),
        "egp_dd": egp.get("max_dd"), "egp_calmar": egp.get("calmar"),
        "usd_return": usd.get("total_return"), "usd_cagr": usd.get("cagr"),
        "usd_dd": usd.get("max_dd"), "usd_calmar": usd.get("calmar"),
        "too_small": bool(trades is not None and trades < 30),
    }


def main() -> int:
    start, end = WINDOW
    closes, volumes = panel_frames()
    universe = eligible_universe()
    prices_for(universe)
    rate = usd_rate()
    if rate is None:
        print("EGP=X unavailable - every USD column reports None")

    members = liquid_half(closes, volumes, start)
    print(f"proxy: equal-weight index of the liquid half chosen on the year "
          f"before {start.date()} - {len(members)} names\n")
    index = proxy_index(closes, members)
    value_weights = (closes[members].loc[:start].mul(
        volumes[members].loc[:start]).median(axis=0, skipna=True))
    index_vw = proxy_index(closes, members, weights=value_weights.to_dict())

    detectors = {
        "1. proxy below its 200d average": detector_below_ma(index),
        "2a. proxy drawdown > 10%": detector_drawdown(index, 10),
        "2b. proxy drawdown > 15%": detector_drawdown(index, 15),
        "2c. proxy drawdown > 20%": detector_drawdown(index, 20),
        "3a. breadth: >50% below own 200d": detector_breadth(closes, members, 0.50),
        "3b. breadth: >60% below own 200d": detector_breadth(closes, members, 0.60),
        "3c. breadth: >70% below own 200d": detector_breadth(closes, members, 0.70),
        "4a. realized vol above 80th pct": detector_volatility(index, 0.80),
        "4b. realized vol above 90th pct": detector_volatility(index, 0.90),
        "5. value-weighted proxy below 200d": detector_below_ma(index_vw),
    }

    print("=" * 108)
    print("TASK G - CAUSAL DETECTORS, AND HOW THEY LINE UP WITH LABELS THAT ARE UNDER SUSPICION")
    print("=" * 108)
    print(f"causality check on detector 1 (recomputed on half the history): "
          f"{'PASS' if assert_causal(detectors['1. proxy below its 200d average'], index) else 'FAIL'}\n")
    header = (f"{'detector':<36}{'flagged':>9}{'% of days':>11}{'overlap':>9}"
              f"{'precision':>11}{'recall':>9}{'entry lag':>11}{'exit lag':>10}")
    print(header)
    print("-" * len(header))
    g_rows = []
    for label, flags in detectors.items():
        window = flags.loc[(flags.index >= start) & (flags.index <= end)].fillna(False)
        agree = label_agreement(flags)
        share = window.mean() * 100 if len(window) else None
        g_rows.append({"detector": label, "days_flagged": int(window.sum()),
                       "share_of_days": None if share is None else round(share, 1),
                       **agree})
        def cell(value, width, suffix=""):
            return f"{'None':>{width}}" if value is None else f"{value:>{width}.1f}{suffix}"

        print(f"{label:<36}{int(window.sum()):>9}{cell(share, 10, '%')}"
              f"{str(agree.get('overlap_days')):>9}"
              f"{cell(agree.get('precision'), 10, '%')}"
              f"{cell(agree.get('recall'), 8, '%')}"
              f"{str(agree.get('entry_lag_median_days')):>11}"
              f"{str(agree.get('exit_lag_median_days')):>10}")
    pd.DataFrame(g_rows).to_csv(OUT_DIR / "regime_detectors.csv", index=False)

    # --- H ------------------------------------------------------------------
    print()
    print("=" * 108)
    print("TASK H - THE GATED OVERLAY, EGP AND USD, AGAINST A BUYABLE BASKET")
    print("=" * 108)
    rows, stats = shipped_rows()
    basket = ex_ante_basket(universe, start, 30)
    bh = hold_basket(basket, start, end)

    lines = [summarise(bh, "(a) ex-ante top-30 basket, held", rate, None)]
    ungated, n = gated_curve(rows, stats, None)
    lines.append(summarise(ungated, "(b) ungated strategy", rate, n))

    for label, flags in detectors.items():
        for variant, fx in (("EGP cash", None), ("USD cash", rate)):
            curve, kept = gated_curve(rows, stats, flags, off_state_fx=fx)
            if not len(curve):
                lines.append({"line": f"{label} | off in {variant}",
                              "trades": kept, "too_small": True})
                continue
            lines.append(summarise(curve, f"{label} | off in {variant}", rate, kept))

    frame = pd.DataFrame(lines)
    pd.set_option("display.width", 250)
    show = ["line", "trades", "egp_return", "egp_dd", "egp_calmar",
            "usd_return", "usd_dd", "usd_calmar", "too_small"]
    print(frame[show].to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    frame.to_csv(OUT_DIR / "regime_gated_overlay.csv", index=False)

    # --- the random control -------------------------------------------------
    print()
    print("=" * 108)
    print("TASK H(c) - THE RANDOM GATE, SAME DUTY CYCLE, 100 ITERATIONS")
    print("=" * 108)
    rng = np.random.default_rng(SEED)
    bh_usd_calmar = summarise(bh, "bh", rate, None)["usd_calmar"]
    control_rows = []
    for label in ("1. proxy below its 200d average", "2b. proxy drawdown > 15%",
                  "3b. breadth: >60% below own 200d"):
        flags = detectors[label]
        window = flags.loc[(flags.index >= start) & (flags.index <= end)].fillna(False)
        duty = float(window.mean())
        real_curve, real_kept = gated_curve(rows, stats, flags)
        real = summarise(real_curve, label, rate, real_kept)
        draws = []
        for _ in range(RANDOM_ITERATIONS):
            random_flags = pd.Series(
                rng.random(len(window)) < duty, index=window.index)
            curve, kept = gated_curve(rows, stats, random_flags)
            if len(curve):
                draws.append(summarise(curve, "r", rate, kept)["usd_calmar"])
        draws = [d for d in draws if d is not None]
        if not draws:
            control_rows.append({"detector": label, "percentile": None})
            continue
        pct = float((np.array(draws) < real["usd_calmar"]).mean() * 100)
        control_rows.append({
            "detector": label, "duty_cycle_off": round(duty * 100, 1),
            "real_usd_calmar": real["usd_calmar"],
            "random_median_usd_calmar": round(float(np.median(draws)), 3),
            "random_best": round(float(np.max(draws)), 3),
            "percentile_of_real": round(pct, 1),
            "beats_random_median": bool(real["usd_calmar"] > np.median(draws)),
            "bh_usd_calmar": bh_usd_calmar,
            "clears_the_bar": bool(real["usd_calmar"] is not None
                                   and bh_usd_calmar is not None
                                   and real["usd_calmar"] > bh_usd_calmar),
        })
    control = pd.DataFrame(control_rows)
    print(control.to_string(index=False))
    control.to_csv(OUT_DIR / "regime_random_control.csv", index=False)
    print(f"\nthe bar: USD Calmar of the buy-and-hold basket = {bh_usd_calmar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
