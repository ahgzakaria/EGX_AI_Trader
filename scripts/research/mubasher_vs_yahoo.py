r"""Mubasher against the Yahoo snapshot every backtest in this repository runs on.

Yahoo is still in the program for three things, found in the code rather than
remembered:

* **Backtests.** Purpose ``backtest`` reads ``LEGACY_BACKTEST_V1``: the Yahoo
  rows in ``data/market_data_cache.sqlite``, no network
  (``core.research_router.get_legacy_backtest_history``). Every strategy result
  and research panel here was measured on it.
* **Seeds.** ``data/frozen_yahoo_seed`` bootstraps the symbols EODHD does not
  support, with a Mubasher tail appended (``core.local_daily_history``).
* **Audits.** ``data/yahoo_cache`` holds one-year CSVs that two EODHD audit
  scripts read on demand.

This measures whether MubasherTrade PRO's own daily record could stand in for
the first two. Everything is read locally; nothing goes to the network.

A. The snapshot itself -- symbols, span, how many are active, and whether it
   is still frozen.
B. Coverage -- which snapshot symbols Mubasher carries, by ticker or by ISIN.
C. Agreement on shared sessions -- close against Yahoo's Close and Adj Close,
   high, low, volume, daily return.
D. Traded sessions one source has and the other does not.
E. Price-basis steps, and which series moved, with EODHD as the third source.
F. The opening price and sub-pound precision.
G. The shipped CONFIRMED_VOLUME_BREAKOUT rule on both, cleaned the way the
   backtest loader cleans (NaN rows and zero-volume bars dropped), signal for
   signal, with single-source signals checked against EODHD.
H. The frozen seeds against the snapshot and against Mubasher.
I. The live seam: where an active seeded symbol's Yahoo body meets its
   Mubasher tail, whether the join adds a return Mubasher itself does not have.

    venv\Scripts\python.exe scripts\research\mubasher_vs_yahoo.py
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.research.mubasher_vs_eodhd import (                       # noqa: E402
    ACTION_NEAR_SESSIONS, BANDS, STEP_PERCENT, STEP_WINDOW, basis_steps, bands,
    eodhd_frames, mubasher_frame, mubasher_isin_map, mubasher_tables,
    signal_dates)
from core.universe import load_universe, read_alias_registry           # noqa: E402
from providers.eodhd_client import EODHDClient                         # noqa: E402
from sector_flow.mubasher_local import find_root                       # noqa: E402
from strategy_momentum_breakout.config import load as load_config      # noqa: E402
from strategy_momentum_breakout.signal import warmup_bars              # noqa: E402

SNAPSHOT = PROJECT_ROOT / "data" / "market_data_cache.sqlite"
SEEDS = PROJECT_ROOT / "data" / "frozen_yahoo_seed"
OUT = PROJECT_ROOT / "reports" / "data_sources"
ERA_SPLIT = pd.Timestamp("2023-01-01")
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def base_of(symbol) -> str:
    return str(symbol).split(".")[0].upper()


def to_naive_dates(values):
    stamps = pd.to_datetime(values)
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_convert(None)
    return stamps.dt.normalize()


def read_snapshot():
    """Yahoo 10y daily rows per symbol, and when each entry was written."""

    uri = f"file:{SNAPSHOT.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        entries = pd.read_sql(
            "SELECT symbol, fetched_at FROM market_data_entries "
            "WHERE provider='yahoo' AND period='10y' AND interval='1d'", connection)
        candles = pd.read_sql(
            "SELECT symbol, timestamp, open, high, low, close, adj_close, volume "
            "FROM market_data_candles "
            "WHERE provider='yahoo' AND period='10y' AND interval='1d'", connection)
    candles["Date"] = to_naive_dates(candles["timestamp"])
    frames = {}
    for symbol, group in candles.groupby("symbol"):
        frame = pd.DataFrame({
            "Open": group["open"].to_numpy(float), "High": group["high"].to_numpy(float),
            "Low": group["low"].to_numpy(float), "Close": group["close"].to_numpy(float),
            "Adj Close": group["adj_close"].to_numpy(float),
            "Volume": group["volume"].to_numpy(float),
        }, index=pd.DatetimeIndex(group["Date"]))
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        frames[base_of(symbol)] = frame
    fetched = {base_of(r.symbol): str(r.fetched_at) for r in entries.itertuples()}
    return frames, fetched


def engine_clean(frame):
    """What `core.data_provider._clean_for_engine` keeps: no NaN, positive volume."""

    kept = frame.dropna(subset=OHLCV)
    return kept[kept["Volume"] > 0]


def print_bands(label, tag, values, width=26):
    b = bands(values)
    if not b["n"]:
        return
    print(f"   {label:<{width}}{tag:<9}{b['n']:>10,}{b['median']:>9.3f}"
          + "".join(f"{b['<= ' + str(x) + '%']:>8.2f}%" for x in BANDS)
          + f"{b['> ' + str(BANDS[-1]) + '%']:>8.2f}%")


def band_header(width=26):
    print(f"   {'field':<{width}}{'era':<9}{'n':>10}{'median':>9}"
          + "".join(f"{'<=' + str(b) + '%':>9}" for b in BANDS)
          + f"{'>' + str(BANDS[-1]) + '%':>9}")


def main() -> int:
    cfg = load_config()
    lookback = warmup_bars(cfg)
    frames, fetched = read_snapshot()
    records = {r.canonical_symbol: r for r in load_universe()}
    active = {s for s, r in records.items() if r.is_active}
    aliases = read_alias_registry()
    base = find_root()
    if base is None:
        print("MubasherTrade PRO data not found on this machine.")
        return 1
    connection, tables = mubasher_tables(base)
    by_isin = mubasher_isin_map(base)
    client = EODHDClient()

    last = pd.Series({s: f.index[-1] for s, f in frames.items()})
    freeze = last.mode().iloc[0]

    # --- A. the snapshot ---------------------------------------------------------
    print("A. THE SNAPSHOT (data/market_data_cache.sqlite, yahoo 10y 1d)")
    print(f"   symbols {len(frames)}, rows {sum(len(f) for f in frames.values()):,}, "
          f"{min(f.index[0] for f in frames.values()).date()} .. {last.max().date()}")
    kinds = Counter("active" if s in active else "alias" if s in aliases
                    else "inactive" if s in records else "not in universe"
                    for s in frames)
    print(f"   of them: {dict(kinds)}")
    print(f"   the last candle most symbols share: {freeze.date()} "
          f"({int((last == freeze).sum())} symbols)")
    after = last[last > freeze].sort_values()
    print(f"   symbols with candles after it: {len(after)}")
    for symbol, when in after.items():
        grown = int((frames[symbol].index > freeze).sum())
        print(f"     {symbol:<6} to {when.date()} (+{grown} rows), entry written "
              f"{fetched.get(symbol, '?')[:19]}")
    earlier = last[last < freeze].sort_values()
    print(f"   symbols ending before it: {len(earlier)}  "
          + ", ".join(f"{s} {d.date()}" for s, d in earlier.items()))
    missing = sorted(active - set(frames))
    print(f"   active symbols the backtest cannot load (no snapshot): {len(missing)}  "
          f"{missing}")

    per_symbol, agreement, returns = [], defaultdict(list), []
    whole_span, missing_in = [], Counter()
    steps, step_reason = [], Counter()
    opens, precision = Counter(), Counter()
    signal_rows, arbitration = [], {k: [0, 0] for k in ("both", "Mubasher only",
                                                         "Yahoo only")}

    for symbol in sorted(frames):
        yahoo = frames[symbol]
        record = records.get(symbol)
        isin = str(getattr(record, "isin", "") or "").strip() if record else ""
        ticker = symbol if symbol in tables else by_isin.get(isin)
        if ticker not in tables:
            ticker = None
        row = {"symbol": symbol,
               "status": ("active" if symbol in active else "alias" if symbol in aliases
                          else "inactive" if record else "not in universe"),
               "mubasher_ticker": ticker, "yahoo_first": str(yahoo.index[0].date()),
               "yahoo_last": str(yahoo.index[-1].date())}
        recent = yahoo.index >= pd.Timestamp("2024-01-01")
        carried = np.isclose(yahoo["Open"], yahoo["Close"].shift(1), rtol=1e-6)
        opens["yahoo_rows"] += int(recent.sum())
        opens["yahoo_carried"] += int((carried & recent).sum())
        if ticker is None:
            per_symbol.append(row)
            continue

        mub, events = mubasher_frame(connection, tables[ticker])
        if mub is None or not len(mub):
            per_symbol.append(row)
            continue
        row.update(mub_first=str(mub.index[0].date()), mub_last=str(mub.index[-1].date()))
        start, end = max(yahoo.index[0], mub.index[0]), min(yahoo.index[-1], mub.index[-1])
        y = yahoo[(yahoo.index >= start) & (yahoo.index <= end)]
        m = mub[(mub.index >= start) & (mub.index <= end)]
        common = y.index.intersection(m.index)
        row["matched_sessions"] = len(common)

        only_y = y.index[y["Volume"] > 0].difference(m.index)
        only_m = m.index[m["Volume"] > 0].difference(y.index)
        missing_in["mubasher"] += len(only_y)
        missing_in["yahoo"] += len(only_m)
        row.update(traded_missing_in_mubasher=len(only_y),
                   traded_missing_in_yahoo=len(only_m))
        if not len(common):
            per_symbol.append(row)
            continue

        yc, mc = y.loc[common], m.loc[common]
        tags = np.where(common < ERA_SPLIT, "2016-22", "2023-26")
        for label, mine, theirs in (("close vs Yahoo Close", mc["Close"], yc["Close"]),
                                    ("close vs Yahoo Adj Close", mc["Close"], yc["Adj Close"]),
                                    ("high", mc["High"], yc["High"]),
                                    ("low", mc["Low"], yc["Low"])):
            diff = ((mine / theirs - 1).abs() * 100).to_numpy()
            agreement[(label, "all")].extend(diff.tolist())
            for tag in ("2016-22", "2023-26"):
                agreement[(label, tag)].extend(diff[tags == tag].tolist())
        traded = ((mc["Volume"] > 0) & (yc["Volume"] > 0)).to_numpy()
        vdiff = ((mc["Volume"] / yc["Volume"] - 1).abs() * 100).to_numpy()
        agreement[("volume", "all")].extend(vdiff[traded].tolist())
        mr = mc["Close"].to_numpy()[1:] / mc["Close"].to_numpy()[:-1] - 1
        yr = yc["Close"].to_numpy()[1:] / yc["Close"].to_numpy()[:-1] - 1
        returns.extend((np.abs(mr - yr) * 100).tolist())
        within = float(((mc["Close"] / yc["Close"] - 1).abs() <= 0.01).mean() * 100)
        row["close_within_1pct"] = within
        whole_span.append(within)

        recent_m = common >= pd.Timestamp("2024-01-01")
        m_carried = np.isclose(mc["Open"], mc["Close"].shift(1), rtol=1e-6)
        opens["mub_rows"] += int(recent_m.sum())
        opens["mub_carried"] += int((m_carried & recent_m).sum())

        cheap = common[(common >= ERA_SPLIT) & (mc["Close"].to_numpy() < 1.0)]
        if len(cheap):
            def third(values):
                values = np.round(values, 6)
                return np.abs(values * 100 - np.round(values * 100)) > 1e-6
            mine = mc.loc[cheap, "Close"].to_numpy(float)
            theirs = yc.loc[cheap, "Close"].to_numpy(float)
            precision["rows"] += len(cheap)
            precision["mub_third"] += int(third(mine).sum())
            precision["yahoo_third"] += int(third(theirs).sum())
            precision["differ"] += int((np.abs(mine - theirs) >= 0.0005).sum())

        # E. Steps, attributed against EODHD over the same two dates.
        eod = eodhd_frames(client, symbol)
        ratio = (mc["Close"] / yc["Close"]).replace([np.inf, -np.inf], np.nan).dropna()
        found = basis_steps(ratio)
        row["basis_steps"] = len(found)
        for date, change in found:
            k = ratio.index.get_loc(date)
            a = ratio.index[max(0, k - ACTION_NEAR_SESSIONS)]
            b = ratio.index[min(len(ratio) - 1, k + ACTION_NEAR_SESSIONS)]
            event_near = any(abs((d - date).days) <= 14 for d, _, _ in events)
            reason = "no EODHD bars at both ends"
            if eod is not None:
                third_close = eod[0]["Close"]
                if a in third_close.index and b in third_close.index:
                    ref = float(third_close[b]) / float(third_close[a]) - 1
                    m_off = abs(mc.at[b, "Close"] / mc.at[a, "Close"] - 1 - ref) * 100 > STEP_PERCENT
                    y_off = abs(yc.at[b, "Close"] / yc.at[a, "Close"] - 1 - ref) * 100 > STEP_PERCENT
                    reason = {(True, False): "Mubasher departs from EODHD",
                              (False, True): "Yahoo departs from EODHD",
                              (True, True): "both depart from EODHD",
                              (False, False): "neither departs from EODHD"}[(m_off, y_off)]
            step_reason[reason] += 1
            steps.append((symbol, str(date.date()), round(change, 2), event_near, reason))

        # G. The rule on both, cleaned as the backtest loader cleans.
        y_signals = signal_dates(engine_clean(yahoo[OHLCV]), cfg)
        m_signals = signal_dates(engine_clean(mub[OHLCV]), cfg)
        if y_signals is not None and m_signals is not None and len(yahoo) > lookback:
            low_edge, high_edge = yahoo.index[lookback], end
            inside = lambda s: {d for d in s if low_edge <= d <= high_edge}  # noqa: E731
            ys, ms = inside(y_signals), inside(m_signals)
            both = ys & ms
            signal_rows.append({"symbol": symbol, "yahoo": len(ys), "mubasher": len(ms),
                                "same_day": len(both)})
            if eod is not None and len(eod[1]) > lookback:
                e_signals = signal_dates(engine_clean(eod[1]), cfg)
                if e_signals is not None:
                    e_edge = eod[1].index[lookback]
                    judged = lambda s: {d for d in s if d >= e_edge}          # noqa: E731
                    for key, chosen in (("both", judged(both)),
                                        ("Mubasher only", judged(ms - ys)),
                                        ("Yahoo only", judged(ys - ms))):
                        arbitration[key][0] += len(chosen)
                        arbitration[key][1] += len(chosen & e_signals)
        per_symbol.append(row)

    frame = pd.DataFrame(per_symbol)
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT / "mubasher_vs_yahoo_by_symbol.csv", index=False)
    pd.DataFrame(steps, columns=["symbol", "date", "change_percent",
                                 "mubasher_event_within_14_days", "eodhd_attribution"]
                 ).to_csv(OUT / "mubasher_vs_yahoo_basis_steps.csv", index=False)

    # --- B. coverage -------------------------------------------------------------
    print("\nB. COVERAGE -- snapshot symbols Mubasher carries")
    carried = frame[frame["mubasher_ticker"].notna()]
    print(f"   {len(carried)} of {len(frame)}")
    renamed = carried[carried["mubasher_ticker"] != carried["symbol"]]
    print("   under another Mubasher ticker, by ISIN: "
          + (", ".join(f"{r.symbol}->{r.mubasher_ticker}" for r in renamed.itertuples())
             or "none"))
    absent = frame[frame["mubasher_ticker"].isna()]
    print("   not carried: " + (", ".join(f"{r.symbol}({r.status})"
                                           for r in absent.itertuples()) or "none"))
    if "mub_first" in frame:
        both = carried.dropna(subset=["mub_first"])
        earlier = (pd.to_datetime(both["mub_first"]) < pd.to_datetime(both["yahoo_first"])).sum()
        print(f"   Mubasher's record starts before the snapshot for {int(earlier)} of "
              f"{len(both)} (the snapshot is a ten-year window by construction)")

    # --- C. agreement ----------------------------------------------------------------
    print("\nC. AGREEMENT on shared sessions (absolute difference, %)")
    band_header()
    for (label, tag), values in agreement.items():
        print_bands(label, tag, values)
    print_bands("daily return (pts)", "all", returns)
    if whole_span:
        share = pd.Series(whole_span)
        print(f"   per symbol, close within 1% of Yahoo on every shared session: "
              f"{int((share >= 100).sum())} of {len(share)}; >= 95%: "
              f"{int((share >= 95).sum())}; < 50%: {int((share < 50).sum())}")
        worst = frame.nsmallest(10, "close_within_1pct")[["symbol", "close_within_1pct"]]
        print("   lowest: " + ", ".join(f"{s} {v:.1f}%" for s, v in worst.values))

    # --- D. missing sessions -------------------------------------------------------------
    print("\nD. TRADED SESSIONS one source lacks, inside the shared span")
    print(f"   Yahoo traded, Mubasher has no row: {missing_in['mubasher']:,}")
    print(f"   Mubasher traded, Yahoo has no row: {missing_in['yahoo']:,}")
    for column, label in (("traded_missing_in_mubasher", "worst for Mubasher"),
                          ("traded_missing_in_yahoo", "worst for Yahoo")):
        if column in frame:
            top = frame.nlargest(6, column)[["symbol", column]].values.tolist()
            print(f"   {label}: {[(s, int(v)) for s, v in top]}")

    # --- E. steps ------------------------------------------------------------------------
    print(f"\nE. PRICE-BASIS STEPS (> {STEP_PERCENT}% persistent over {STEP_WINDOW} "
          f"sessions each side), attributed with EODHD over +-{ACTION_NEAR_SESSIONS} sessions")
    print(f"   steps {len(steps)} across {int((frame.get('basis_steps', pd.Series(dtype=float)) > 0).sum())} symbols; "
          f"a Mubasher corporate action within 14 days: {sum(s[3] for s in steps)}")
    for reason, count in step_reason.most_common():
        print(f"     {count:>4}  {reason}")
    if steps:
        print(f"   dates carrying the most steps: "
              f"{Counter(s[1] for s in steps).most_common(5)}")

    # --- F. open and precision ------------------------------------------------------------
    print("\nF. OPENING PRICE (sessions since 2024-01-01) AND PRECISION")
    if opens["yahoo_rows"]:
        print(f"   Yahoo open == previous close:    "
              f"{opens['yahoo_carried'] / opens['yahoo_rows'] * 100:.1f}% of {opens['yahoo_rows']:,}")
    if opens["mub_rows"]:
        print(f"   Mubasher OP == previous close:   "
              f"{opens['mub_carried'] / opens['mub_rows'] * 100:.1f}% of {opens['mub_rows']:,}")
    if precision["rows"]:
        rows = precision["rows"]
        print(f"   closes under 1 EGP since 2023: {rows:,} sessions; third decimal "
              f"Mubasher {precision['mub_third'] / rows * 100:.1f}%, Yahoo "
              f"{precision['yahoo_third'] / rows * 100:.1f}%; differ by >= 0.0005 on "
              f"{precision['differ'] / rows * 100:.1f}%")

    # --- G. signals ------------------------------------------------------------------------
    print("\nG. CONFIRMED_VOLUME_BREAKOUT, backtest-cleaned, from each symbol's Yahoo "
          "warm-up to the last shared session")
    signals = pd.DataFrame(signal_rows)
    if len(signals):
        y_total, m_total = int(signals["yahoo"].sum()), int(signals["mubasher"].sum())
        same = int(signals["same_day"].sum())
        print(f"   symbols {len(signals)}   Yahoo {y_total:,}   Mubasher {m_total:,}   "
              f"same symbol, same day {same:,} ({same / max(y_total, 1) * 100:.1f}% of "
              f"Yahoo's, {same / max(m_total, 1) * 100:.1f}% of Mubasher's)")
        signals["disagree"] = signals["yahoo"] + signals["mubasher"] - 2 * signals["same_day"]
        print("   most disagreement: " + ", ".join(
            f"{r.symbol} Y{r.yahoo}/M{r.mubasher}/both{r.same_day}"
            for r in signals.nlargest(8, "disagree").itertuples()))
        signals.to_csv(OUT / "mubasher_vs_yahoo_signals.csv", index=False)
        print("   single-source signals, judged by EODHD (from EODHD's own warm-up):")
        for key, (count, hits) in arbitration.items():
            print(f"     {key:<14}{count:>6}; EODHD fires the same day on {hits} "
                  f"({hits / max(count, 1) * 100:.1f}%)")

    # --- H. seeds ------------------------------------------------------------------------
    print("\nH. THE FROZEN SEEDS (data/frozen_yahoo_seed)")
    for path in sorted(SEEDS.glob("*.csv")):
        symbol = path.stem.upper()
        seed = pd.read_csv(path, parse_dates=["Date"]).set_index("Date")
        status = ("active" if symbol in active else "alias" if symbol in aliases
                  else "inactive" if symbol in records else "not in universe")
        note = f"{symbol:<6} {status:<16} {seed.index[0].date()}..{seed.index[-1].date()}"
        if symbol in frames:
            shared = seed.index.intersection(frames[symbol].index)
            same = np.isclose(seed.loc[shared, "Close"], frames[symbol].loc[shared, "Close"],
                              rtol=1e-6).mean() * 100 if len(shared) else float("nan")
            note += f" | equals snapshot close on {same:.1f}% of {len(shared)}"
        record = records.get(symbol)
        isin = str(getattr(record, "isin", "") or "").strip() if record else ""
        ticker = symbol if symbol in tables else by_isin.get(isin)
        if ticker in tables:
            mub, _ = mubasher_frame(connection, tables[ticker])
            shared = seed.index.intersection(mub.index)
            if len(shared):
                close = ((mub.loc[shared, "Close"] / seed.loc[shared, "Close"] - 1).abs()
                         <= 0.01).mean() * 100
                note += (f" | Mubasher {ticker} within 1% on {close:.1f}% of {len(shared)}, "
                         f"runs to {mub.index[-1].date()}")
        else:
            note += " | not in Mubasher"
        print("   " + note)

    # --- I. the live seam ------------------------------------------------------------------
    print("\nI. THE LIVE SEAM -- active seeded symbols, as core.local_daily_history builds them")
    from core.local_daily_history import build_local_daily_history

    for path in sorted(SEEDS.glob("*.csv")):
        symbol = path.stem.upper()
        if symbol not in active:
            continue
        try:
            live, provenance = build_local_daily_history(symbol)
        except Exception as error:                              # noqa: BLE001
            print(f"   {symbol:<6} not built: {error}")
            continue
        if live is None:
            print(f"   {symbol:<6} not built")
            continue
        seed_end = pd.Timestamp(provenance.get("seed_latest_session"))
        appended = int(provenance.get("bridge_sessions_appended", 0))
        position = live.index.searchsorted(seed_end, side="right")
        if not appended or position >= len(live.index):
            print(f"   {symbol:<6} seed ends {seed_end.date()}, no tail appended")
            continue
        before, after = live.index[position - 1], live.index[position]
        joined = live["Close"].iloc[position] / live["Close"].iloc[position - 1] - 1
        ticker = symbol if symbol in tables else by_isin.get(
            str(getattr(records.get(symbol), "isin", "") or "").strip())
        own, basis = "n/a", "n/a"
        if ticker in tables:
            mub, _ = mubasher_frame(connection, tables[ticker])
            if before in mub.index and after in mub.index:
                own = f"{(mub.at[after, 'Close'] / mub.at[before, 'Close'] - 1) * 100:+.2f}%"
            shared = live.index[:position].intersection(mub.index)[-STEP_WINDOW:]
            if len(shared):
                ratio = mub.loc[shared, "Close"] / live.loc[shared, "Close"]
                basis = (f"median {ratio.median():.4f}, within 1% on "
                         f"{((ratio - 1).abs() <= 0.01).mean() * 100:.0f}%")
        print(f"   {symbol:<6} seed ends {before.date()}, first tail bar {after.date()}, "
              f"{appended} appended | return across the join {joined * 100:+.2f}%, "
              f"Mubasher's own {own} | Mubasher / seed over the last {STEP_WINDOW} "
              f"shared sessions: {basis}")

    print(f"\nwrote {OUT.relative_to(PROJECT_ROOT)}")
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
