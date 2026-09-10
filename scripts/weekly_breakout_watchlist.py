r"""The week's pre-breakout watchlist, for reading by hand after Thursday's close.

Prints a table sized for a screen and writes the same rows to
`reports/watchlist/`. Nothing is scheduled -- this is run when it is wanted.

The selection lives in `strategy_momentum_breakout/watch.py`, which reads the
rule through `signal.measure` and `config.load()`. This file formats; it decides
nothing and it invents no number. The one presentation choice, how near a name
must sit to its trigger, is swept and printed so the default can be read off
candidate counts.

    venv\Scripts\python.exe scripts\weekly_breakout_watchlist.py
    venv\Scripts\python.exe scripts\weekly_breakout_watchlist.py --reach 0.5
    venv\Scripts\python.exe scripts\weekly_breakout_watchlist.py --no-sweep
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.environment import load_project_environment

load_project_environment()

from strategy_momentum_breakout.config import load as load_config     # noqa: E402
from strategy_momentum_breakout.watch import (                        # noqa: E402
    DEFAULT_REACH_ATR, HEADER_NOTE, REACH_MULTIPLES, STRUCTURAL_GATES,
    TRIGGER_GATES, sweep_reach, watch, write_csv)

RULE = "=" * 100


def wrap(text: str, width: int = 96, indent: str = "  ") -> str:
    words, lines, current = text.split(), [], indent
    for word in words:
        if len(current) + len(word) + 1 > width and current.strip():
            lines.append(current)
            current = indent + word
        else:
            current = f"{current} {word}" if current.strip() else current + word
    if current.strip():
        lines.append(current)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reach", type=float, default=DEFAULT_REACH_ATR,
                        help="how many ATR below the trigger still counts as near")
    parser.add_argument("--limit", type=int, default=None,
                        help="only the first N symbols, for a quick look")
    parser.add_argument("--no-sweep", action="store_true",
                        help="skip the reach sweep")
    parser.add_argument("--capital", type=float, default=None,
                        help="account size for the position columns "
                             "(defaults to the configured initial_capital)")
    args = parser.parse_args(argv)

    cfg = load_config()
    symbols = None
    if args.limit:
        from core.universe import active_symbols

        symbols = sorted(active_symbols())[:args.limit]

    from strategy_momentum_breakout.watch import _histories

    unreadable = {}
    histories = _histories(symbols,
                           on_error=lambda s, r: unreadable.setdefault(s, r))
    result = watch(histories=histories, cfg=cfg, reach_atr=args.reach,
                   capital=args.capital)
    result.unreadable = unreadable

    print(RULE)
    print("  PRE-BREAKOUT WATCHLIST — CONFIRMED_VOLUME_BREAKOUT")
    print(RULE)
    print(f"  run date            {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  data closed on      {result.session_date or 'unknown'}")
    print(f"  candidates          {result.count}")
    print(f"  reach multiple      {args.reach:g} x ATR   "
          f"(presentation filter, not a measured threshold)")
    print(f"  capital             {result.capital:,.0f} EGP at "
          f"{cfg.risk_percent:g}% risk per trade"
          + ("" if args.capital is not None else "  (configured default)"))
    print(f"  universe considered {result.considered}"
          + (f", {len(result.unreadable)} unreadable and skipped"
             if result.unreadable else ""))
    print()
    print(wrap(HEADER_NOTE))
    print(RULE)

    if not args.no_sweep:
        print("\n  Reach sweep — how many names each multiple returns, so the")
        print("  default is chosen from counts rather than from taste:")
        for multiple, count in sweep_reach(histories=histories, cfg=cfg,
                                           multiples=REACH_MULTIPLES).items():
            mark = "  <- in use" if abs(multiple - args.reach) < 1e-9 else ""
            print(f"      {multiple:>4.1f} x ATR   {count:>3} candidates{mark}")

    print("\n  Gate funnel — what refused each symbol first. An empty list here")
    print("  means nothing is set up, not that something is broken.")
    print(f"      {'structural gates (must pass now)':<44}")
    for gate in STRUCTURAL_GATES:
        print(f"        {gate:<40}{result.funnel.get(gate, 0):>5}")
    already = "already fired (scan.py handles those)"
    print(f"      {already:<44}{result.funnel.get('AlreadyTriggered', 0):>5}")
    print(f"        trigger gates: {', '.join(TRIGGER_GATES)}")
    for other in ("OutOfReach", "InvalidRisk", "InsufficientHistory", "Unusable"):
        print(f"      {other:<44}{result.funnel.get(other, 0):>5}")

    if not result.count:
        print("\n  Nothing is within reach of a trigger this week.")
        print(RULE)
        return 0

    print("\n" + RULE)
    print("  Ordered nearest-to-trigger first. This is a reading order, not a")
    print("  ranking claim: nothing here has been measured to predict which")
    print("  set-up goes on to fire, and no hit rate is reported because none")
    print("  has been measured.")
    print(RULE)

    header = (f"  {'symbol':<9}{'close':>9}{'trigger':>9}{'to go':>8}{'ATR':>6}"
              f"{'stop':>9}{'risk%':>7}{'cost%':>7}{'net 2R%':>9}"
              f"{'shares':>9}{'value':>12}{'% cap':>7}  spread")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for c in result.candidates:
        cost = ("  n/a" if c.round_trip_cost_percent is None
                else f"{c.round_trip_cost_percent:>7.3f}")
        net = ("  n/a" if c.net_2r_after_cost_percent is None
               else f"{c.net_2r_after_cost_percent:>9.2f}")
        print(f"  {c.symbol:<9}{c.close:>9.3f}{c.prior_high:>9.3f}"
              f"{c.distance_percent:>7.2f}%{c.distance_atr:>6.2f}"
              f"{c.stop_loss_today:>9.3f}{c.risk_percent:>7.2f}{cost}{net}"
              f"{c.shares_at_risk:>9,}{c.position_value:>12,.0f}"
              f"{c.position_percent:>7.1f}  {c.spread_source}")

    print("\n  On the trigger day, at the close, all three must be true:")
    for c in result.candidates[:5]:
        print(f"    {c.symbol:<9} {c.trigger_sentence}")
    if result.count > 5:
        print(f"    ... the remaining {result.count - 5} are in the CSV.")

    print(f"\n  Entry on every row: {cfg.entry_mode} — the session AFTER the")
    print(f"  trigger closes. Exit: {cfg.holding_bars} sessions or the stop;")
    print("  this strategy has no target.")
    print("  The stop shown is today's. The base low rolls, so it will have")
    print("  moved by the time the trigger fires and must be recomputed then.")
    print("  'net 2R%' uses an UNMEASURED 2R yardstick minus that symbol's own")
    print("  round trip, so a name whose cost eats its move is visible here.")
    if result.count:
        print()
        print(wrap(result.candidates[0].sizing_note))

    # `watch.write_csv` is the one place a watchlist file is produced. The
    # Streamlit page calls the same function, so a column cannot come to
    # mean one thing in a file and another on a screen.
    out = write_csv(result)
    print()
    print(f"  wrote {out.relative_to(PROJECT_ROOT)}")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
