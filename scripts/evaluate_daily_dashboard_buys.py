"""What happened to every BUY the Daily Dashboard actually issued.

    python -m scripts.evaluate_daily_dashboard_buys

The forward tester recorded 59 BUY decisions between 2026-07-09 and today, but
its own evaluator followed five of them and its paper portfolio opened five
positions, so the record answers nothing about the other fifty-four.

This replays each recorded decision through the SAME frozen machinery the
backtest and the paper portfolio use -- ``EntryManager`` for the fill,
``ExitManager`` for the exit, ``TradingCosts`` per symbol for the price paid and
received, ``PositionSizer`` for the size -- against the measured Mubasher record.
Nothing here is a second version of the rules, and nothing recomputes a signal:
the stop, the band and both targets are the ones stored on the decision.

It uses only candles strictly after the session the decision was computed on,
and it never calls a trade a loss or a win before it has finished: a fill with
its holding window still running is OPEN and is marked at the last close, a
decision whose band was never touched is NO_FILL, and one too recent to have
had its full entry window is PENDING.

Two views, because they answer different questions:

* **per decision** -- every distinct trade sized independently at the
  configured risk, as if capital were unlimited. Did the signals work?
* **the account** -- the same trades through the paper portfolio's own
  admission rules (position count, portfolio heat, cash), in date order. What
  would the configured account actually have made?

The five positions the paper portfolio did close are replayed too, and printed
beside what it recorded, so the replay can be checked against the record rather
than trusted.
"""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

DB = PROJECT_ROOT / "data" / "forward_testing.db"
OUT_DIR = PROJECT_ROOT / "reports" / "daily_dashboard_forward"
OUT = OUT_DIR / "buy_outcomes.csv"
LEDGER = OUT_DIR / "account_ledger.csv"

TRADED = ("CLOSED", "OPEN")


def _frames(tickers, expected):
    from core.mubasher_live_history import READY, mubasher_live_history
    from indicators.technical import calculate_indicators

    frames = {}
    for ticker in tickers:
        frame, status, _ = mubasher_live_history(ticker.split(".")[0], min_bars=60,
                                                 not_after=expected)
        if status == READY and frame is not None:
            frames[ticker] = calculate_indicators(frame.copy())
    return frames


def replay(signal, frame, cfg):
    """One decision through the frozen entry and exit managers."""
    from backtesting.context import BacktestContext
    from backtesting.costs import TradingCosts
    from backtesting.managers.entry_manager import EntryManager
    from backtesting.managers.exit_manager import ExitManager
    from core.market_data import MarketData
    from portfolio.sizing import PositionSizer

    day = pd.Timestamp(signal["signal_date"])
    matches = [i for i, d in enumerate(frame.index) if pd.Timestamp(d).normalize() == day]
    base = {"signal_date": signal["signal_date"], "ticker": signal["ticker"],
            "regime": signal["market_regime"], "score": signal["score"],
            "declared_rr": signal["rr"], "buy_low": signal["buy_low"],
            "buy_high": signal["buy_high"], "stop": signal["stop_loss"],
            "target1": signal["target1"], "target2": signal["target2"]}
    if not matches:
        return {**base, "outcome": "NO_DATA",
                "note": "the decision's session is not in the measured record"}

    data = MarketData(frame)
    costs = TradingCosts(symbol=signal["ticker"])
    context = BacktestContext(
        symbol=signal["ticker"], data=data, signal_index=matches[0],
        signal={"BuyLow": float(signal["buy_low"]), "BuyHigh": float(signal["buy_high"]),
                "StopLoss": float(signal["stop_loss"]),
                "Target1": float(signal["target1"]), "Target2": float(signal["target2"])})

    sessions_after = data.length - 1 - matches[0]
    if not EntryManager(costs).find_entry(context):
        waited = int(cfg.ENTRY_WAIT_DAYS)
        if sessions_after < waited:
            return {**base, "outcome": "PENDING",
                    "note": f"{sessions_after} of {waited} entry sessions have happened"}
        return {**base, "outcome": "NO_FILL",
                "note": f"the band was not touched within {waited} sessions"}

    sizing = PositionSizer(cfg.INITIAL_CAPITAL, cfg.RISK_PERCENT).calculate(
        context.entry_price, float(signal["stop_loss"]))
    shares = int(sizing["Shares"])
    filled = {**base, "entry_date": context.entry_date,
              "entry_price": context.entry_price, "shares": shares,
              "position_value": round(shares * context.entry_price, 2),
              "risk_amount": round(shares * abs(context.entry_price
                                                - float(signal["stop_loss"])), 2)}

    if ExitManager(costs).manage(context, allow_timeout=False):
        per_share = costs.net_profit(context.entry_price, float(context.exit_price))
        return {**filled, "outcome": "CLOSED", "exit_date": context.exit_date,
                "exit_price": context.exit_price, "exit_reason": context.exit_reason,
                "net_return_pct": round(per_share / context.entry_price * 100, 2),
                "net_profit_egp": round(per_share * shares, 2),
                "sessions_held": int(sum(
                    1 for d in frame.index
                    if pd.Timestamp(context.entry_date) <= pd.Timestamp(d)
                    <= pd.Timestamp(context.exit_date)))}

    # Still inside its holding window. Marked, never scored.
    last_close = float(data.close[-1])
    mark = costs.exit_price(last_close)
    per_share = costs.net_profit(context.entry_price, mark)
    return {**filled, "outcome": "OPEN",
            "exit_date": str(pd.Timestamp(frame.index[-1]).date()),
            "exit_price": mark, "exit_reason": "marked at last close",
            "net_return_pct": round(per_share / context.entry_price * 100, 2),
            "net_profit_egp": round(per_share * shares, 2)}


def fold_repeats(rows):
    """Mark a BUY re-issued while the same name's replayed position is open.

    The dashboard re-issues a BUY on every session a name still qualifies, so
    EBSC appears five times. A second signal whose fill falls inside the first
    trade's life is the same trade seen again, not a second trade, and
    counting it would score one outcome twice.
    """
    busy_until = defaultdict(str)
    for row in sorted(rows, key=lambda r: (r.get("entry_date") or "", r["ticker"])):
        if row.get("outcome") in TRADED:
            row["repeat_of_open"] = row["entry_date"] <= busy_until[row["ticker"]]
            if not row["repeat_of_open"]:
                busy_until[row["ticker"]] = (
                    "9999" if row["outcome"] == "OPEN" else row.get("exit_date") or "9999")
        else:
            row["repeat_of_open"] = False
    return rows


def simulate_account(rows, *, capital, risk_percent, max_open_positions,
                     max_heat_percent, ranking=None):
    """The distinct trades through the paper portfolio's own admission rules.

    Mirrors ``forward_testing.service``: positions that exit on a date are
    closed before anything is opened on it; candidates on one date are taken
    in signal-ranking order; a candidate is refused for ``MaxPositions`` when
    the book is full, ``Heat`` when its risk would take open risk past the
    configured share of capital, and ``Capital`` when there is not the cash to
    buy it. Sizing stays on the initial capital, exactly as the paper
    portfolio sizes it.

    Returns ``(ledger, summary)``. Nothing here reads a price.
    """
    ranking = ranking or {}
    effective_max = min(int(max_open_positions),
                        math.floor(max_heat_percent / risk_percent)
                        if risk_percent > 0 else int(max_open_positions))
    max_heat = capital * max_heat_percent / 100.0
    trades = sorted(
        (r for r in rows if r.get("outcome") in TRADED and not r.get("repeat_of_open")),
        key=lambda r: (r["entry_date"], ranking.get((r["ticker"], r["signal_date"]), 999),
                       r["ticker"]))
    dates = sorted({t["entry_date"] for t in trades}
                   | {t["exit_date"] for t in trades if t["outcome"] == "CLOSED"})

    open_book, ledger, realized = [], [], 0.0
    for day in dates:
        for position in [p for p in open_book
                         if p["outcome"] == "CLOSED" and p["exit_date"] <= day]:
            open_book.remove(position)
            realized += float(position["net_profit_egp"])
        for trade in (t for t in trades if t["entry_date"] == day):
            invested = sum(float(p["position_value"]) for p in open_book)
            cash = capital + realized - invested
            open_risk = sum(float(p["risk_amount"]) for p in open_book)
            reason = None
            if len(open_book) >= effective_max:
                reason = "MaxPositions"
            elif open_risk + float(trade["risk_amount"]) > max_heat:
                reason = "Heat"
            elif int(trade["shares"]) <= 0 or float(trade["position_value"]) > cash:
                reason = "Capital"
            ledger.append({**trade, "admitted": reason is None,
                           "refused_for": reason or "",
                           "cash_before": round(cash, 2),
                           "open_positions_before": len(open_book)})
            if reason is None:
                open_book.append(trade)

    taken = [r for r in ledger if r["admitted"]]
    closed = [r for r in taken if r["outcome"] == "CLOSED"]
    still_open = [r for r in taken if r["outcome"] == "OPEN"]
    closed_pnl = sum(float(r["net_profit_egp"]) for r in closed)
    open_mark = sum(float(r["net_profit_egp"]) for r in still_open)
    best = max(closed, key=lambda r: float(r["net_profit_egp"]), default=None)
    refused = defaultdict(int)
    for row in ledger:
        if not row["admitted"]:
            refused[row["refused_for"]] += 1
    return ledger, {
        "effective_max_positions": effective_max,
        "admitted": len(taken), "refused": dict(refused),
        "closed": len(closed),
        "wins": sum(1 for r in closed if float(r["net_return_pct"]) > 0),
        "closed_pnl_egp": round(closed_pnl, 2),
        "open_mark_egp": round(open_mark, 2),
        "equity_egp": round(capital + closed_pnl + open_mark, 2),
        "return_pct": round((closed_pnl + open_mark) / capital * 100, 2),
        "best_trade": (best["ticker"], best["entry_date"], float(best["net_profit_egp"]))
        if best else None,
        "closed_pnl_without_best_egp": round(
            closed_pnl - (float(best["net_profit_egp"]) if best else 0.0), 2),
    }


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for row in rows for k in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    from backtesting.config import load as load_backtest_config
    from core.research_router import _expected_completed_session

    cfg = load_backtest_config()
    expected = _expected_completed_session()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    signals = [dict(r) for r in con.execute(
        "SELECT * FROM signals WHERE signal_type='BUY' ORDER BY signal_date, ticker")]
    frames = _frames(sorted({s["ticker"] for s in signals}), expected)

    rows = []
    for signal in signals:
        frame = frames.get(signal["ticker"])
        rows.append(replay(signal, frame, cfg) if frame is not None else {
            "signal_date": signal["signal_date"], "ticker": signal["ticker"],
            "outcome": "NO_DATA", "note": "no measured record for this ticker"})
    fold_repeats(rows)
    _write(OUT, rows)

    ranking = {(s["ticker"], s["signal_date"]): s["ranking"] for s in signals}
    ledger, account = simulate_account(
        rows, capital=float(cfg.INITIAL_CAPITAL), risk_percent=float(cfg.RISK_PERCENT),
        max_open_positions=int(cfg.MAX_OPEN_POSITIONS),
        max_heat_percent=float(cfg.MAX_PORTFOLIO_RISK_PERCENT), ranking=ranking)
    _write(LEDGER, ledger)

    # --- the check against what the paper portfolio actually recorded
    print("replay vs the paper portfolio's own record")
    for p in con.execute("""SELECT s.signal_date, p.ticker, p.entry_date, p.entry_price,
                                   p.exit_date, p.exit_price, p.exit_reason
                            FROM paper_positions p JOIN signals s USING(signal_id)
                            WHERE p.status='CLOSED' ORDER BY s.signal_date"""):
        mine = next((r for r in rows if r["ticker"] == p["ticker"]
                     and r["signal_date"] == p["signal_date"]), {})
        print(f"  {p['ticker']:8} {p['signal_date']}  recorded {p['entry_date']} "
              f"{p['entry_price']:>9} -> {p['exit_date']} {p['exit_price']:>9} "
              f"{p['exit_reason']:<16} | replay {mine.get('outcome')} "
              f"{mine.get('entry_date', '')} {mine.get('exit_date', '')} "
              f"{mine.get('exit_reason', '')}")

    print("\nthe account")
    for key, value in account.items():
        print(f"  {key:30} {value}")
    print(f"\nwrote {OUT.relative_to(PROJECT_ROOT)} and {LEDGER.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
