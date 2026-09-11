r"""Can MubasherTrade PRO's own daily record replace EODHD as the price source?

Measured, not argued. Both sides are read from this machine and nothing goes
to the network: Mubasher from the terminal's ``history.db`` (read-only,
immutable), EODHD from the client's own response cache, split-adjusted by the
same ``providers.eodhd_adjustment.adjust`` and the same operational-volume
policy the live router serves. A symbol whose EODHD response is not cached is
reported as not cached, never fetched.

What this answers, in order:

A. Coverage -- which of the 241 routed symbols each source has, how deep, how fresh.
B. Agreement on the sessions both hold -- close, high, low, volume, daily return.
C. Sessions one source has and the other does not.
D. Price basis -- where Mubasher's series and EODHD's split-adjusted series step
   apart, and whether the step sits on a corporate action either source records.
E. The opening price -- whether either source carries a real one.
F. The only consequence that matters to the program: the shipped
   CONFIRMED_VOLUME_BREAKOUT rule run on each source, signal for signal.
G. A third source to settle disagreements: the frozen Yahoo research panel
   (2016-07 to 2026-07), independent of both.
H. Internal consistency: whether each source's volume agrees with the
   exchange's own reported turnover, which no adjustment should change.
I. Identity by data: the routed symbols Mubasher lacks, matched against every
   active Mubasher series, and the routed EODHD tickers that duplicate another.
J. The live window's disagreements, attributed: which series jumps, and which
   one the third source agrees with.
K. Signals only one source produced, and whether the third source produces them.
L. Price precision below 1 EGP, where EGX quotes to 0.001.

The live program reads only the last ``warmup_bars`` sessions of each symbol,
so agreement over that window is reported beside agreement since 2016: a
disagreement in 2017 is a research problem, not a live one.

The population is the active universe from ``core.universe``: registered EODHD
aliases (a second code for a company already listed) are inactive and excluded,
so no company is counted twice. Where Mubasher files a company under a
different ticker, the terminal's own symbol master resolves it by ISIN.

Scope note: every backtest in this repository runs on the frozen Yahoo snapshot
(``core.data_provider``, purpose ``backtest``). EODHD feeds the live path -- the
scanner, the watchlist, the dashboard. This compares the live path's source.

    venv\Scripts\python.exe scripts\research\mubasher_vs_eodhd.py
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from core.environment import load_project_environment

load_project_environment()

from core.universe import active_universe, read_alias_registry         # noqa: E402
from indicators.technical import calculate_indicators                  # noqa: E402
from providers.eodhd_adjustment import adjust                          # noqa: E402
from providers.eodhd_client import EODHDClient                         # noqa: E402
from providers.eodhd_volume_adjustment import resolve_operational_volume  # noqa: E402
from sector_flow.mubasher_local import (HISTORY_RELATIVE,              # noqa: E402
                                        INTRADAY_RELATIVE, NOT_EQUITY,
                                        available_sessions, find_root)
from strategy_momentum_breakout.config import load as load_config      # noqa: E402
from strategy_momentum_breakout.signal import measure, warmup_bars     # noqa: E402

ROUTING = PROJECT_ROOT / "data" / "eodhd" / "historical_symbol_routing_active.json"
ACTIONS = PROJECT_ROOT / "data" / "eodhd" / "corporate_actions"
OUT = PROJECT_ROOT / "reports" / "data_sources"
RESEARCH_START = pd.Timestamp("2016-01-01")

#: Agreement bands, in percent. Reporting bands rather than one threshold, so no
#: single cut decides the verdict.
BANDS = (0.05, 0.5, 1.0)
#: A basis step is a persistent change in Mubasher / EODHD-adjusted of more than
#: this, holding on both sides of the date for STEP_WINDOW matched sessions.
STEP_PERCENT = 2.0
STEP_WINDOW = 20
ACTION_NEAR_SESSIONS = 5
#: Two tickers carry "the same series" when at least this share of their shared
#: recent closes sit within 0.5% of each other, over at least MATCH_MIN_SESSIONS.
DUPLICATE_LEVEL = 90.0
MATCH_MIN_SESSIONS = 60

SACT_TYPES = {"2": "split / bonus shares", "6": "cash dividend", "3": "rights issue",
              "1": "type 1", "5": "type 5"}


def routed_symbols():
    data = json.loads(ROUTING.read_text(encoding="utf-8"))
    items = data.get("symbols", data) if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = [dict(v, symbol=k) for k, v in items.items()]
    return {str(i["symbol"]).split(".")[0].upper(): i["tier"] for i in items}


def active_symbols_with_isin():
    """``{ticker: ISIN}`` for the operational universe.

    Registered EODHD aliases are inactive by construction, so a company listed
    under two EODHD codes is compared once rather than weighted twice.
    """
    return {record.canonical_symbol: str(getattr(record, "isin", "") or "").strip()
            for record in active_universe()}


def mubasher_isin_map(base):
    """``{ISIN: Mubasher ticker}`` from the terminal's own CASE symbol master.

    The two sources name some companies differently (EODHD MATD is Mubasher
    MMAT). The ISIN decides which table is the same company; a name never does.
    """
    path = base.parent.parent / "Cache" / "PrimarySystemMeta.db"
    if not path.is_file():
        return {}
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT JSON FROM SYMBOL_MASTER WHERE EXCHANGE='CASE' AND LANGUAGE='EN'"
        ).fetchone()
    if not row:
        return {}
    payload = json.loads(row[0])
    fields = payload["HED"]["TD"].split("|")
    mapping = {}
    for line in payload["DAT"]["TD"]:
        entry = dict(zip(fields, line.split("|")))
        isin = str(entry.get("ISIN_CODE", "")).strip()
        if isin:
            mapping.setdefault(isin, str(entry.get("SYMBOL", "")).upper())
    return mapping


# --- Mubasher ----------------------------------------------------------------

def mubasher_tables(base):
    uri = f"file:{(base / HISTORY_RELATIVE).as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    tables = {name[1:].upper(): name for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'") if name.startswith("_")}
    return connection, tables


def mubasher_frame(connection, table):
    rows = pd.read_sql(
        f'SELECT DATE, OP, HIG, LOW, CLS, VOL, TOVR, VWAP, SACT FROM "{table}"',
        connection)
    if rows.empty:
        return None, []
    frame = pd.DataFrame({
        "Open": pd.to_numeric(rows["OP"], errors="coerce"),   # the previous close
        "High": pd.to_numeric(rows["HIG"], errors="coerce"),
        "Low": pd.to_numeric(rows["LOW"], errors="coerce"),
        "Close": pd.to_numeric(rows["CLS"], errors="coerce"),
        "Volume": pd.to_numeric(rows["VOL"], errors="coerce").fillna(0.0),
        "Turnover": pd.to_numeric(rows["TOVR"], errors="coerce"),
        "VWAP": pd.to_numeric(rows["VWAP"], errors="coerce"),
    })
    frame.index = pd.to_datetime(rows["DATE"].astype(str), format="%Y%m%d",
                                 errors="coerce")
    frame = frame[frame.index.notna() & (frame["Close"] > 0)]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    events = []
    for date, sact in zip(pd.to_datetime(rows["DATE"].astype(str), format="%Y%m%d",
                                         errors="coerce"), rows["SACT"]):
        text = str(sact or "")
        if "~" not in text or pd.isna(date):
            continue
        for part in text.split(","):
            kind, _, factor = part.partition("~")
            events.append((date, kind.strip(), factor.strip()))
    return frame, events


# --- EODHD, from cache only ----------------------------------------------------

def eodhd_frames(client, base):
    """(raw+adjusted frame, production frame, split dates) or None if not cached."""

    # `get_json` adds fmt=json before it keys the cache, so the key must too.
    path, params = f"eod/{base}.EGX", {"period": "d", "order": "a", "fmt": "json"}
    if not client._cache_path(path, params).is_file():
        return None
    raw = client.get_json(path, params, cache_ttl_seconds=None)
    rows = [r for r in (raw or []) if isinstance(r, dict) and r.get("close")]
    if not rows:
        return None
    splits_path = client._cache_path(f"splits/{base}.EGX", {"fmt": "json"})
    if splits_path.is_file():
        splits = client.get_json(f"splits/{base}.EGX", cache_ttl_seconds=None) or []
    elif (ACTIONS / f"{base}_splits.json").is_file():
        splits = json.loads((ACTIONS / f"{base}_splits.json").read_text(encoding="utf-8"))
    else:
        splits = []
    frame = pd.DataFrame({
        "Date": pd.to_datetime([r["date"] for r in rows]).date,
        "Open": [r["open"] for r in rows], "High": [r["high"] for r in rows],
        "Low": [r["low"] for r in rows], "Close": [r["close"] for r in rows],
        "Volume": [r.get("volume", 0) for r in rows],
    })
    adjusted = adjust(frame, splits).frame.copy()
    served, _ = resolve_operational_volume(base, adjusted["Date"],
                                           adjusted["Raw Volume"], splits)
    adjusted["Served Volume"] = served.to_numpy()
    adjusted.index = pd.to_datetime(adjusted["Date"])
    adjusted = adjusted[~adjusted.index.duplicated(keep="last")].sort_index()
    production = pd.DataFrame({
        "Open": adjusted["Open"], "High": adjusted["High"], "Low": adjusted["Low"],
        "Close": adjusted["Close"], "Volume": adjusted["Served Volume"],
    }, index=adjusted.index)
    split_dates = [pd.Timestamp(s["date"]) for s in splits if s.get("date")]
    return adjusted, production, split_dates


# --- measurements ----------------------------------------------------------------

def bands(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return {"n": 0}
    out = {"n": int(len(values)), "median": float(np.median(values))}
    for band in BANDS:
        out[f"<= {band}%"] = float((values <= band).mean() * 100)
    out[f"> {BANDS[-1]}%"] = float((values > BANDS[-1]).mean() * 100)
    return out


def basis_steps(ratio):
    """Dates where Mubasher / EODHD-adjusted changes persistently, collapsed."""

    values = ratio.to_numpy(float)
    if len(values) < 2 * STEP_WINDOW + 1:
        return []
    steps, run = [], []

    # The medians either side start to differ about half a window before the
    # real step, so the first date a run crosses the threshold is early by up
    # to ten sessions. A first version reported that edge, and every step then
    # looked unrelated to the corporate action ten sessions later. The step is
    # where the two medians differ most, so that is the date kept.
    def flush():
        if run:
            k, change = max(run, key=lambda item: abs(item[1]))
            steps.append((ratio.index[k], change))
            run.clear()

    for k in range(STEP_WINDOW, len(values) - STEP_WINDOW):
        before = np.median(values[k - STEP_WINDOW:k])
        after = np.median(values[k:k + STEP_WINDOW])
        if before <= 0 or not np.isfinite(before) or not np.isfinite(after):
            flush()
            continue
        change = (after / before - 1) * 100
        if abs(change) > STEP_PERCENT:
            run.append((k, change))
        else:
            flush()
    flush()
    return steps


def near(date, dates, index):
    if not len(dates):
        return False
    position = index.searchsorted(date)
    for other in dates:
        other_position = index.searchsorted(other)
        if abs(other_position - position) <= ACTION_NEAR_SESSIONS:
            return True
    return False


def closes_match(a, b, sessions, returns=True):
    """(level %, return %, n) over the last shared `sessions`, or (None, None, n).

    Level is the share of closes within 0.5%; return is the share of daily
    returns within half a point, which survives a price-basis difference that
    level does not.
    """
    index = a.index.intersection(b.index)[-sessions:]
    if len(index) < MATCH_MIN_SESSIONS:
        return None, None, len(index)
    x, y = a.loc[index], b.loc[index]
    level = float(((x / y - 1).abs() <= 0.005).mean() * 100)
    if not returns:
        return level, None, len(index)
    ret = float(((x.pct_change() - y.pct_change()).abs().dropna() <= 0.005).mean() * 100)
    return level, ret, len(index)


def signal_dates(frame, cfg):
    if frame is None or len(frame) <= warmup_bars(cfg) + 1:
        return None
    try:
        data = calculate_indicators(frame.copy())
    except Exception:                                       # noqa: BLE001
        return None
    table = measure(data, cfg)
    passed = table.index[table["Passed"].to_numpy(bool)]
    return set(pd.DatetimeIndex(passed).normalize())


def main() -> int:
    cfg = load_config()
    tiers = routed_symbols()
    active = active_symbols_with_isin()
    symbols = {s: tiers.get(s, "UNROUTED") for s in active}
    aliases = read_alias_registry()
    base = find_root()
    if base is None:
        print("MubasherTrade PRO data not found on this machine.")
        return 1
    connection, tables = mubasher_tables(base)
    by_isin = mubasher_isin_map(base)
    intraday_uri = f"file:{(base / INTRADAY_RELATIVE).as_posix()}?mode=ro"
    with sqlite3.connect(intraday_uri, uri=True) as intraday:
        intraday_tables = {n[1:].upper() for (n,) in intraday.execute(
            "SELECT name FROM sqlite_master WHERE type='table'") if n.startswith("_")}
    sessions = available_sessions(base)
    client = EODHDClient()
    live_lookback = warmup_bars(cfg)

    # The frozen Yahoo panel every backtest here runs on. Read from its local
    # cache only; a machine without it simply skips section G.
    yahoo, yahoo_full = {}, {}
    try:
        from scripts.research.panel import CACHE, load as load_panel
        if CACHE.exists():
            for name, group in load_panel().groupby("Symbol"):
                key = str(name).split(".")[0].upper()
                # The panel carries its indicators, so the rule runs on it as is.
                yahoo_full[key] = group.set_index(pd.to_datetime(group["Date"]))
                yahoo[key] = yahoo_full[key][["Close", "Volume"]]
    except Exception as error:                              # noqa: BLE001
        print(f"(Yahoo panel unavailable: {error})")

    per_symbol = []
    agreement = defaultdict(list)
    returns = defaultdict(list)
    missing_in = Counter()
    steps_total, steps_explained = [], Counter()
    open_stats = Counter()
    signal_rows = []
    tiers_missing = defaultdict(list)
    live = []
    arbiter = Counter()
    arbiter_agree = defaultdict(list)
    consistency = defaultdict(list)
    mub_frames, mub_events, eod_frames = {}, {}, {}
    precision, precision_symbols = Counter(), set()
    arbitration = {key: [0, 0] for key in ("both", "Mubasher only", "EODHD only")}
    step_departure = Counter()

    for symbol, tier in sorted(symbols.items()):
        # The same company can carry a different ticker in each source. The
        # ISIN decides, never the name.
        mub_ticker = symbol if symbol in tables else by_isin.get(active[symbol])
        if mub_ticker not in tables:
            mub_ticker = None
        record = {"symbol": symbol, "tier": tier, "mubasher_ticker": mub_ticker,
                  "in_mubasher": mub_ticker is not None,
                  "in_mubasher_intraday": (mub_ticker or symbol) in intraday_tables}
        mub, events = (mubasher_frame(connection, tables[mub_ticker])
                       if mub_ticker else (None, []))
        eod = eodhd_frames(client, symbol)
        record["in_eodhd_cache"] = eod is not None
        if mub is not None and len(mub):
            mub_frames[symbol], mub_events[symbol] = mub, events
        if eod is not None:
            eod_frames[symbol] = eod
        if mub is not None and len(mub):
            record.update(mub_first=str(mub.index[0].date()),
                          mub_last=str(mub.index[-1].date()), mub_rows=len(mub))
        if eod is not None:
            adjusted, production, split_dates = eod
            record.update(eod_first=str(adjusted.index[0].date()),
                          eod_last=str(adjusted.index[-1].date()),
                          eod_rows=len(adjusted))
            raw_open, raw_close = adjusted["Raw Open"], adjusted["Raw Close"]
            recent = adjusted.index >= pd.Timestamp("2024-01-01")
            carried = (raw_open == raw_close.shift(1))
            open_stats["eodhd_rows"] += int(recent.sum())
            open_stats["eodhd_open_is_prev_close"] += int((carried & recent).sum())
        if mub is not None and len(mub):
            recent = mub.index >= pd.Timestamp("2024-01-01")
            carried = (mub["Open"] == mub["Close"].shift(1))
            open_stats["mub_rows"] += int(recent.sum())
            open_stats["mub_open_is_prev_close"] += int((carried & recent).sum())

        if mub is None or eod is None:
            if mub is None:
                tiers_missing["mubasher"].append((symbol, tier))
            if eod is None:
                tiers_missing["eodhd"].append((symbol, tier))
            per_symbol.append(record)
            continue

        adjusted, production, split_dates = eod
        start = max(mub.index[0], adjusted.index[0], RESEARCH_START)
        end = min(mub.index[-1], adjusted.index[-1])
        m = mub[(mub.index >= start) & (mub.index <= end)]
        e = adjusted[(adjusted.index >= start) & (adjusted.index <= end)]
        common = m.index.intersection(e.index)
        record["matched_sessions"] = len(common)

        traded_e = e.index[e["Raw Volume"] > 0]
        traded_m = m.index[m["Volume"] > 0]
        only_e = traded_e.difference(m.index)
        only_m = traded_m.difference(e.index)
        missing_in["mubasher"] += len(only_e)
        missing_in["eodhd"] += len(only_m)
        record.update(traded_sessions_missing_in_mubasher=len(only_e),
                      traded_sessions_missing_in_eodhd=len(only_m))

        if len(common):
            mc, ec = m.loc[common], e.loc[common]
            era = np.where(common < pd.Timestamp("2023-01-01"), "2016-22", "2023-26")
            for label, mine, theirs in (("close vs raw", mc["Close"], ec["Raw Close"]),
                                        ("close vs adjusted", mc["Close"], ec["Close"]),
                                        ("high vs adjusted", mc["High"], ec["High"]),
                                        ("low vs adjusted", mc["Low"], ec["Low"])):
                diff = (mine / theirs - 1).abs() * 100
                agreement[(label, "all")].extend(diff.tolist())
                for tag in ("2016-22", "2023-26"):
                    agreement[(label, tag)].extend(diff[era == tag].tolist())
            both_traded = (mc["Volume"] > 0) & (ec["Raw Volume"] > 0)
            for label, theirs in (("volume vs raw", ec["Raw Volume"]),
                                  ("volume vs served", ec["Served Volume"])):
                diff = (mc["Volume"][both_traded] / theirs[both_traded] - 1).abs() * 100
                agreement[(label, "all")].extend(diff.tolist())
            record["close_vs_adjusted_within_1pct"] = float(
                ((mc["Close"] / ec["Close"] - 1).abs() <= 0.01).mean() * 100)

            # What the live rule actually reads.
            tail = common[-live_lookback:]
            record["live_lookback_within_1pct"] = float(
                ((mc.loc[tail, "Close"] / ec.loc[tail, "Close"] - 1).abs() <= 0.01)
                .mean() * 100)
            live.append(record["live_lookback_within_1pct"])

            # EGX quotes sub-pound shares to 0.001. A source that stores two
            # decimals moves a 0.115 close to 0.12 -- a 4% error on the level a
            # breakout is measured against.
            cheap = common[common >= pd.Timestamp("2023-01-01")]
            cheap = cheap[mc.loc[cheap, "Close"].to_numpy() < 1.0]
            if len(cheap):
                def third_decimal(values):
                    return np.abs(values * 100 - np.round(values * 100)) > 1e-6
                mine = mc.loc[cheap, "Close"].to_numpy(float)
                theirs = ec.loc[cheap, "Raw Close"].to_numpy(float)
                precision["rows"] += len(cheap)
                precision["mubasher_third"] += int(third_decimal(mine).sum())
                precision["eodhd_third"] += int(third_decimal(theirs).sum())
                precision["differ"] += int((np.abs(mine - theirs) >= 0.0005).sum())
                precision_symbols.add(symbol)

            # Turnover is what the exchange reports and no price adjustment
            # touches it, so price x volume against it tests each source's
            # volume on its own terms. VWAP x volume is exact when the two are
            # on one basis; the close is an approximation, carried to calibrate
            # the EODHD row, which has no VWAP.
            traded = (mc["Volume"] > 0) & (mc["Turnover"] > 0)
            with_vwap = traded & (mc["VWAP"] > 0)
            consistency["Mubasher VWAP x volume vs turnover"].extend(
                ((mc["VWAP"] * mc["Volume"] / mc["Turnover"] - 1).abs()[with_vwap]
                 * 100).tolist())
            consistency["Mubasher close x volume vs turnover"].extend(
                ((mc["Close"] * mc["Volume"] / mc["Turnover"] - 1).abs()[traded]
                 * 100).tolist())
            e_traded = traded & (ec["Raw Volume"] > 0)
            consistency["EODHD raw close x raw volume vs turnover"].extend(
                ((ec["Raw Close"] * ec["Raw Volume"] / mc["Turnover"] - 1).abs()[e_traded]
                 * 100).tolist())
            e_served = traded & (ec["Served Volume"] > 0)
            consistency["EODHD adj close x served volume vs turnover"].extend(
                ((ec["Close"] * ec["Served Volume"] / mc["Turnover"] - 1).abs()[e_served]
                 * 100).tolist())

            if symbol in yahoo:
                y = yahoo[symbol]
                shared = common.intersection(y.index)
                if len(shared):
                    yc = y.loc[shared, "Close"].astype(float)
                    m_ok = ((mc.loc[shared, "Close"] / yc - 1).abs() <= 0.01).to_numpy()
                    e_ok = ((ec.loc[shared, "Close"] / yc - 1).abs() <= 0.01).to_numpy()
                    tags = np.where(shared < pd.Timestamp("2023-01-01"),
                                    "2016-22", "2023-26")
                    for tag in ("2016-22", "2023-26"):
                        pick = tags == tag
                        arbiter_agree[("Mubasher", tag)].extend(m_ok[pick].tolist())
                        arbiter_agree[("EODHD", tag)].extend(e_ok[pick].tolist())
                    apart = ((mc.loc[shared, "Close"] / ec.loc[shared, "Close"] - 1)
                             .abs() > 0.01).to_numpy()
                    arbiter["disagree"] += int(apart.sum())
                    arbiter["Yahoo matches Mubasher only"] += int((apart & m_ok & ~e_ok).sum())
                    arbiter["Yahoo matches EODHD only"] += int((apart & e_ok & ~m_ok).sum())
                    arbiter["Yahoo matches both"] += int((apart & m_ok & e_ok).sum())
                    arbiter["Yahoo matches neither"] += int((apart & ~m_ok & ~e_ok).sum())

            # Daily returns on the same pair of dates in both.
            pairs = pd.Series(common[1:], index=common[:-1])
            mr = mc["Close"].to_numpy()[1:] / mc["Close"].to_numpy()[:-1] - 1
            er = ec["Close"].to_numpy()[1:] / ec["Close"].to_numpy()[:-1] - 1
            returns["all"].extend((np.abs(mr - er) * 100).tolist())

            ratio = (mc["Close"] / ec["Close"]).replace([np.inf, -np.inf], np.nan).dropna()
            found = basis_steps(ratio)
            mub_action_dates = defaultdict(list)
            for date, kind, _ in events:
                mub_action_dates[kind].append(date)
            record["basis_steps"] = len(found)
            for date, change in found:
                on_split = near(date, split_dates, ratio.index)
                kinds = [kind for kind, dates in mub_action_dates.items()
                         if near(date, dates, ratio.index)]
                if on_split and kinds:
                    steps_explained["EODHD split and Mubasher event"] += 1
                elif on_split:
                    steps_explained["EODHD split only"] += 1
                elif kinds:
                    steps_explained["Mubasher event only: " + "/".join(
                        sorted(SACT_TYPES.get(k, k) for k in set(kinds)))] += 1
                else:
                    steps_explained["neither source records an action"] += 1
                # Which series moved: each source's return across the step
                # against the third source's return over the same two dates.
                departs = "no Yahoo bars at both ends"
                if symbol in yahoo:
                    k = ratio.index.get_loc(date)
                    start_d = ratio.index[max(0, k - ACTION_NEAR_SESSIONS)]
                    end_d = ratio.index[min(len(ratio) - 1, k + ACTION_NEAR_SESSIONS)]
                    reference = yahoo[symbol]["Close"]
                    if start_d in reference.index and end_d in reference.index:
                        ref = float(reference[end_d]) / float(reference[start_d]) - 1
                        m_move = mc.at[end_d, "Close"] / mc.at[start_d, "Close"] - 1
                        e_move = ec.at[end_d, "Close"] / ec.at[start_d, "Close"] - 1
                        m_off = abs(m_move - ref) * 100 > STEP_PERCENT
                        e_off = abs(e_move - ref) * 100 > STEP_PERCENT
                        departs = {(True, False): "Mubasher departs from Yahoo",
                                   (False, True): "EODHD departs from Yahoo",
                                   (True, True): "both depart from Yahoo",
                                   (False, False): "neither departs from Yahoo"}[(m_off, e_off)]
                step_departure[departs] += 1
                steps_total.append((symbol, str(date.date()), round(change, 2),
                                    on_split, ",".join(sorted(set(kinds))), departs))

        # The rule, on each source, over the same window.
        window_start = RESEARCH_START
        mub_signals = signal_dates(mub, cfg)
        eod_signals = signal_dates(production, cfg)
        if mub_signals is not None and eod_signals is not None:
            cut = lambda s: {d for d in s if window_start <= d <= end}   # noqa: E731
            ms, es = cut(mub_signals), cut(eod_signals)
            both = ms & es
            index = common if len(common) else m.index
            loose = {d for d in ms - both
                     if any(abs(index.searchsorted(d) - index.searchsorted(x)) <= 1
                            for x in es - both)}
            recent = pd.Timestamp("2023-01-01")
            signal_rows.append({"symbol": symbol, "mubasher": len(ms), "eodhd": len(es),
                                "same_day": len(both), "within_one_session": len(loose),
                                "mubasher_2023": sum(d >= recent for d in ms),
                                "eodhd_2023": sum(d >= recent for d in es),
                                "same_day_2023": sum(d >= recent for d in both)})

            # Settle the disagreement with a source that is neither. Counted only
            # from the bar where the panel's own history is warm enough for the
            # rule, so a signal is never scored against a panel that could not
            # have fired yet.
            if symbol in yahoo_full and len(yahoo_full[symbol]) > live_lookback:
                panel_frame = yahoo_full[symbol]
                panel_table = measure(panel_frame, cfg)
                panel_signals = set(pd.DatetimeIndex(panel_frame.index[
                    panel_table["Passed"].to_numpy(bool)]).normalize())
                low_edge = panel_frame.index[live_lookback]
                high_edge = min(panel_frame.index[-1], end)
                inside = lambda s: {d for d in s if low_edge <= d <= high_edge}  # noqa: E731
                pm, pe, pp = inside(ms), inside(es), inside(panel_signals)
                for key, chosen in (("both", pm & pe), ("Mubasher only", pm - pe),
                                    ("EODHD only", pe - pm)):
                    arbitration[key][0] += len(chosen)
                    arbitration[key][1] += len(chosen & pp)
        per_symbol.append(record)

    # --- report ------------------------------------------------------------------
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(per_symbol)
    frame.to_csv(OUT / "mubasher_vs_eodhd_by_symbol.csv", index=False)
    pd.DataFrame(steps_total, columns=["symbol", "date", "change_percent",
                                       "eodhd_split_near", "mubasher_event_types",
                                       "yahoo_attribution"]
                 ).to_csv(OUT / "mubasher_vs_eodhd_basis_steps.csv", index=False)

    print(f"A. COVERAGE of the {len(symbols)} active symbols "
          f"({len(aliases)} registered EODHD aliases excluded: "
          f"{', '.join(f'{a}->{b}' for a, b in sorted(aliases.items()))})")
    renamed = frame[frame["in_mubasher"] & (frame["mubasher_ticker"] != frame["symbol"])]
    print("   carried by Mubasher under another ticker, matched by ISIN: "
          + (", ".join(f"{r.symbol}->{r.mubasher_ticker}" for r in renamed.itertuples())
             or "none"))
    print(f"   EODHD cache       {int(frame['in_eodhd_cache'].sum())}")
    print(f"   Mubasher history  {int(frame['in_mubasher'].sum())}")
    print(f"   Mubasher intraday {int(frame['in_mubasher_intraday'].sum())}")
    for source in ("mubasher", "eodhd"):
        rows = tiers_missing[source]
        print(f"   missing from {source}: {len(rows)}  "
              + ", ".join(f"{s}({t.split('_')[1]})" for s, t in rows))
    for column in ("mub_last", "eod_last"):
        if column in frame:
            print(f"   {column:<9} " + "  ".join(
                f"{k} x{v}" for k, v in frame[column].value_counts().head(5).items()))
    print(f"   Mubasher minute store sessions: {sessions[0] if sessions else None}"
          f" .. {sessions[-1] if sessions else None} ({len(sessions)})")
    if "mub_first" in frame and "eod_first" in frame:
        first = pd.to_datetime(frame["mub_first"], errors="coerce")
        first_e = pd.to_datetime(frame["eod_first"], errors="coerce")
        both = first.notna() & first_e.notna()
        print(f"   history starts earlier in Mubasher for "
              f"{int((first[both] < first_e[both]).sum())} of {int(both.sum())}, "
              f"in EODHD for {int((first_e[both] < first[both]).sum())}")

    print(f"\nB. AGREEMENT on shared sessions since {RESEARCH_START.date()} "
          f"(absolute difference, %)")
    print(f"   {'field':<22}{'era':<9}{'n':>10}{'median':>9}"
          + "".join(f"{'<=' + str(b) + '%':>9}" for b in BANDS) + f"{'>' + str(BANDS[-1]) + '%':>9}")
    for (label, tag), values in agreement.items():
        b = bands(values)
        if not b["n"]:
            continue
        print(f"   {label:<22}{tag:<9}{b['n']:>10,}{b['median']:>9.3f}"
              + "".join(f"{b['<= ' + str(x) + '%']:>8.2f}%" for x in BANDS)
              + f"{b['> ' + str(BANDS[-1]) + '%']:>8.2f}%")
    b = bands(returns["all"])
    print(f"   {'daily return (pts)':<22}{'all':<9}{b['n']:>10,}{b['median']:>9.3f}"
          + "".join(f"{b['<= ' + str(x) + '%']:>8.2f}%" for x in BANDS)
          + f"{b['> ' + str(BANDS[-1]) + '%']:>8.2f}%")

    print(f"\nC. TRADED SESSIONS one source lacks, inside both sources' shared span")
    print(f"   EODHD traded, Mubasher has no row: {missing_in['mubasher']:,}")
    print(f"   Mubasher traded, EODHD has no row: {missing_in['eodhd']:,}")
    if "traded_sessions_missing_in_mubasher" in frame:
        worst = frame.nlargest(5, "traded_sessions_missing_in_mubasher")[
            ["symbol", "traded_sessions_missing_in_mubasher"]].values.tolist()
        worst_e = frame.nlargest(5, "traded_sessions_missing_in_eodhd")[
            ["symbol", "traded_sessions_missing_in_eodhd"]].values.tolist()
        print(f"   worst for Mubasher: {worst}")
        print(f"   worst for EODHD:    {worst_e}")

    print(f"\nD. PRICE BASIS steps (> {STEP_PERCENT}% persistent over "
          f"{STEP_WINDOW} sessions each side)")
    compared = frame["basis_steps"].notna().sum() if "basis_steps" in frame else 0
    stepped = int((frame.get("basis_steps", pd.Series(dtype=float)) > 0).sum())
    print(f"   symbols compared {compared}, with at least one step {stepped}, "
          f"steps {len(steps_total)}")
    for reason, count in steps_explained.most_common():
        print(f"     {count:>4}  {reason}")
    print(f"   which series moved, against the Yahoo panel over "
          f"+-{ACTION_NEAR_SESSIONS} sessions:")
    for reason, count in step_departure.most_common():
        print(f"     {count:>4}  {reason}")
    if steps_total:
        busiest = Counter(step[1] for step in steps_total).most_common(5)
        print(f"   dates carrying the most steps: {busiest}")
    if "close_vs_adjusted_within_1pct" in frame:
        share = frame["close_vs_adjusted_within_1pct"].dropna()
        print(f"   symbols whose close sits within 1% of EODHD-adjusted on every "
              f"shared session: {int((share >= 100).sum())} of {len(share)}; "
              f"on >= 95%: {int((share >= 95).sum())}")
    if live:
        live_share = pd.Series(live)
        print(f"   over each symbol's last {live_lookback} shared sessions (what the "
              f"live rule reads): all within 1% for {int((live_share >= 100).sum())} "
              f"of {len(live_share)}; >= 95% for {int((live_share >= 95).sum())}; "
              f"< 50% for {int((live_share < 50).sum())}")
        worst_live = frame.nsmallest(8, "live_lookback_within_1pct")[
            ["symbol", "live_lookback_within_1pct"]].values.tolist()
        print(f"   lowest: {[(s, round(v, 1)) for s, v in worst_live]}")

    print("\nE. OPENING PRICE, sessions since 2024-01-01")
    if open_stats["eodhd_rows"]:
        print(f"   EODHD raw open == previous raw close: "
              f"{open_stats['eodhd_open_is_prev_close'] / open_stats['eodhd_rows'] * 100:.1f}%"
              f" of {open_stats['eodhd_rows']:,}")
    if open_stats["mub_rows"]:
        print(f"   Mubasher OP == previous close:        "
              f"{open_stats['mub_open_is_prev_close'] / open_stats['mub_rows'] * 100:.1f}%"
              f" of {open_stats['mub_rows']:,}")

    print(f"\nF. CONFIRMED_VOLUME_BREAKOUT on each source, {RESEARCH_START.date()} "
          f"to each symbol's last shared session")
    signals = pd.DataFrame(signal_rows)
    if len(signals):
        m_total, e_total = int(signals["mubasher"].sum()), int(signals["eodhd"].sum())
        same = int(signals["same_day"].sum())
        loose = int(signals["within_one_session"].sum())
        print(f"   symbols {len(signals)}   signals Mubasher {m_total:,}   EODHD {e_total:,}")
        print(f"   same symbol, same day: {same:,}  "
              f"({same / max(e_total, 1) * 100:.1f}% of EODHD's, "
              f"{same / max(m_total, 1) * 100:.1f}% of Mubasher's)")
        print(f"   Mubasher-only signals within one session of an EODHD-only one: {loose:,}")
        signals["disagree"] = signals["mubasher"] + signals["eodhd"] - 2 * signals["same_day"]
        print("   most disagreement: " + ", ".join(
            f"{r.symbol} M{r.mubasher}/E{r.eodhd}/both{r.same_day}"
            for r in signals.nlargest(8, "disagree").itertuples()))
        m23, e23 = int(signals["mubasher_2023"].sum()), int(signals["eodhd_2023"].sum())
        s23 = int(signals["same_day_2023"].sum())
        print(f"   from 2023: Mubasher {m23:,}   EODHD {e23:,}   same day {s23:,}  "
              f"({s23 / max(e23, 1) * 100:.1f}% of EODHD's, "
              f"{s23 / max(m23, 1) * 100:.1f}% of Mubasher's)")
        signals.to_csv(OUT / "mubasher_vs_eodhd_signals.csv", index=False)

    print("\nG. THIRD SOURCE -- the frozen Yahoo panel, close within 1%")
    if arbiter_agree:
        for (source, tag), values in sorted(arbiter_agree.items()):
            print(f"   {source:<9}{tag:<9}{np.mean(values) * 100:>7.2f}%  of {len(values):,}")
        total = max(arbiter["disagree"], 1)
        print(f"   sessions where Mubasher and EODHD differ by > 1%: {arbiter['disagree']:,}")
        for key in ("Yahoo matches Mubasher only", "Yahoo matches EODHD only",
                    "Yahoo matches both", "Yahoo matches neither"):
            print(f"     {key:<30}{arbiter[key] / total * 100:>6.1f}%")
    else:
        print("   Yahoo panel cache not present; skipped.")

    print("\nH. VOLUME against the exchange's reported turnover (absolute difference, %)")
    print(f"   {'check':<46}{'n':>10}{'median':>9}"
          + "".join(f"{'<=' + str(b) + '%':>9}" for b in BANDS) + f"{'>' + str(BANDS[-1]) + '%':>9}")
    for label, values in consistency.items():
        b = bands(values)
        if not b["n"]:
            continue
        print(f"   {label:<46}{b['n']:>10,}{b['median']:>9.3f}"
              + "".join(f"{b['<= ' + str(x) + '%']:>8.2f}%" for x in BANDS)
              + f"{b['> ' + str(BANDS[-1]) + '%']:>8.2f}%")

    print("\nI. IDENTITY BY DATA, NOT BY NAME")
    active_mubasher = {}
    for name, table in tables.items():
        if NOT_EQUITY.search(name):
            continue
        series = mub_frames.get(name)
        if series is None:
            series, _ = mubasher_frame(connection, table)
        if series is not None and len(series) and series.index[-1] >= pd.Timestamp("2025-01-01"):
            active_mubasher[name] = series["Close"]
    print(f"   routed symbols absent from Mubasher, against its {len(active_mubasher)} "
          f"active equity series (last {live_lookback} shared sessions):")
    for symbol in sorted(frame.loc[~frame["in_mubasher"], "symbol"]):
        if symbol not in eod_frames:
            continue
        adjusted = eod_frames[symbol][0]
        traded = adjusted.index[adjusted["Raw Volume"] > 0]
        after = int((adjusted.index > traded[-1]).sum()) if len(traded) else len(adjusted)
        matches = []
        for name, close in active_mubasher.items():
            level, ret, _ = closes_match(adjusted["Close"], close, live_lookback)
            if level is not None and max(level, ret) >= DUPLICATE_LEVEL:
                matches.append((name, round(level, 1), round(ret, 1)))
        matches.sort(key=lambda item: -max(item[1], item[2]))
        print(f"     {symbol:<6} EODHD last traded "
              f"{traded[-1].date() if len(traded) else None}, {after} rows after it | "
              f"Mubasher (ticker, level%, return%): {matches[:3] or 'no matching series'}")
    closes = {s: f[0]["Close"] for s, f in eod_frames.items()}
    ordered = sorted(closes)
    pairs = []
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            level, _, _ = closes_match(closes[a], closes[b], live_lookback, returns=False)
            if level is not None and level >= DUPLICATE_LEVEL:
                pairs.append(f"{a}={b} ({level:.1f}%)")
    print(f"   EODHD routed tickers carrying another routed ticker's series: "
          f"{len(pairs)} pairs  {pairs}")
    if "mub_last" in frame:
        file_last = frame["mub_last"].dropna().mode().iloc[0]
        stale = frame[frame["mub_last"].notna() & (frame["mub_last"] < file_last)]
        print(f"   routed symbols whose Mubasher history ends before the file's "
              f"{file_last}: {len(stale)}")
        for row in stale.sort_values("mub_last").itertuples():
            if row.symbol not in eod_frames:
                continue
            adjusted = eod_frames[row.symbol][0]
            traded = adjusted.index[adjusted["Raw Volume"] > 0]
            recent_turnover = float((adjusted["Raw Close"] * adjusted["Raw Volume"])
                                    .tail(20).median())
            print(f"     {row.symbol:<6} Mubasher ends {row.mub_last} | EODHD last traded "
                  f"{traded[-1].date() if len(traded) else None}, median turnover of its "
                  f"last 20 rows {recent_turnover:,.0f}")

    print(f"\nJ. THE LIVE WINDOW'S DISAGREEMENTS (< 50% of the last {live_lookback} "
          f"shared sessions within 1%)")
    limit = cfg.maximum_session_move_percent
    if "live_lookback_within_1pct" in frame:
        low = frame[frame["live_lookback_within_1pct"] < 50]
        for row in low.sort_values("live_lookback_within_1pct").itertuples():
            series, adjusted = mub_frames[row.symbol], eod_frames[row.symbol][0]
            window = series.index.intersection(adjusted.index)[-live_lookback:]
            mine = series.loc[window, "Close"].pct_change().abs() * 100
            theirs = adjusted.loc[window, "Close"].pct_change().abs() * 100
            third = ""
            if row.symbol in yahoo:
                shared = window.intersection(yahoo[row.symbol].index)
                if len(shared):
                    reference = yahoo[row.symbol].loc[shared, "Close"].astype(float)
                    third = (f" | Yahoo within 1%: Mubasher "
                             f"{((series.loc[shared, 'Close'] / reference - 1).abs() <= 0.01).mean() * 100:.0f}%"
                             f", EODHD "
                             f"{((adjusted.loc[shared, 'Close'] / reference - 1).abs() <= 0.01).mean() * 100:.0f}%"
                             f" (n={len(shared)})")
            events = [f"{d.date()} {SACT_TYPES.get(k, k)} {f}"
                      for d, k, f in mub_events.get(row.symbol, []) if d >= window[0]]
            splits = [str(d.date()) for d in eod_frames[row.symbol][2] if d >= window[0]]
            print(f"   {row.symbol:<6}{row.live_lookback_within_1pct:>6.1f}% | sessions moving "
                  f">= {limit:g}%: Mubasher {int((mine >= limit).sum())}, EODHD "
                  f"{int((theirs >= limit).sum())} | largest Mubasher {mine.max():.1f}%, "
                  f"EODHD {theirs.max():.1f}%{third}")
            print(f"          Mubasher events {events or '-'} | EODHD splits {splits or '-'}")

    print("\nK. SIGNALS ONE SOURCE PRODUCED -- does the Yahoo panel produce them too?")
    if any(count for count, _ in arbitration.values()):
        for key, (count, hits) in arbitration.items():
            print(f"   {key:<14}{count:>6} signals; the panel fires the same day on "
                  f"{hits} ({hits / max(count, 1) * 100:.1f}%)")
    else:
        print("   Yahoo panel cache not present; skipped.")

    print("\nL. PRICE PRECISION -- closes under 1 EGP, since 2023")
    if precision["rows"]:
        rows = precision["rows"]
        print(f"   {rows:,} shared sessions across {len(precision_symbols)} symbols")
        print(f"   close carries a third decimal: Mubasher "
              f"{precision['mubasher_third'] / rows * 100:.1f}%, EODHD raw "
              f"{precision['eodhd_third'] / rows * 100:.1f}%")
        print(f"   the two closes differ by 0.0005 or more: "
              f"{precision['differ'] / rows * 100:.1f}%")

    print(f"\nwrote {OUT.relative_to(PROJECT_ROOT)}")
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
