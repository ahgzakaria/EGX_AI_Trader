"""Names positioned to trigger CONFIRMED_VOLUME_BREAKOUT, one session early.

`scan.py` answers "what fired at this close". This answers "what is set up to
fire", which is the session before that, so a person can watch a short list by
hand instead of waiting for the alert.

It is the same rule read differently, not a second rule. Every level, every gate
and every threshold comes from `signal.measure` and `config.load()` -- the same
table the backtest walks -- so a name on this list and a signal counted in the
backtest are the same arithmetic by construction rather than by care, which is
the principle `scan.py` states and the reason nothing here is a literal.

**The split.** The rule's seven gates fall into two kinds:

* **structural** -- PriceIntegrity, Liquidity, LongTermTrend, Calm. These
  describe the instrument, they move slowly, and a candidate must satisfy every
  one of them *now*.
* **trigger** -- Breakout, VolumeConfirmation, ClosePosition. These describe one
  session's behaviour. A candidate must satisfy *none* of them yet: a name that
  has already broken out is `scan.py`'s business, not this list's.

**The reach filter is not a measured threshold.** Keeping only names whose close
sits below the trigger but within some multiple of ATR of it is a presentation
choice -- it decides how long the list is, nothing in this repository has
validated it, and every row says so. The multiple is swept so the default can be
chosen from candidate counts rather than from taste.

**No hit rate is reported and none should be added.** Nothing here measures how
often a set-up name goes on to fire, and this module will not carry a number
that has not been measured.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from indicators.technical import calculate_indicators
from strategy_momentum_breakout.config import BreakoutConfig, load as load_config
from strategy_momentum_breakout.signal import GATES, evaluate, measure, warmup_bars

#: Which of the rule's own gates describe the instrument, and which describe one
#: session. Both tuples are subsets of `signal.GATES` and are checked against it
#: at import, so a gate added to the rule cannot silently go unclassified here.
STRUCTURAL_GATES = ("PriceIntegrity", "Liquidity", "LongTermTrend", "Calm")
TRIGGER_GATES = ("Breakout", "VolumeConfirmation", "ClosePosition")

assert set(STRUCTURAL_GATES) | set(TRIGGER_GATES) == set(GATES), (
    "every gate in signal.GATES must be classified as structural or trigger")

#: Swept, not chosen. `sweep_reach` reports what each returns, and the default
#: below was read off those counts over the live universe on 2026-09-09:
#: 0.5 ATR returned 0 names, 1.0 returned 3, 1.5 returned 10. One is the
#: smallest multiple that returns a list at all, which is the only defensible
#: reason to prefer it -- it is a choice about list length, not about edge, and
#: the sweep is printed every run so a week that returns nothing at 1.0 can be
#: widened deliberately rather than silently.
REACH_MULTIPLES = (0.5, 1.0, 1.5)
DEFAULT_REACH_ATR = 1.0

#: The reward multiple on the optional, unmeasured target column. The strategy
#: has no target -- it exits on time or on the stop -- so this exists only
#: because a reader asked for a yardstick, and every column carrying it says
#: NOT MEASURED.
UNMEASURED_TARGET_R = 2.0

HEADER_NOTE = (
    "Watchlist for manual monitoring. The rule behind it is the only one in "
    "this repo with surviving out-of-sample lift (+1.36% valid era). The "
    "automated system built on it captured 6% of bull-regime upside and lost "
    "to buy-and-hold — see INVESTIGATION_SUMMARY.md. This list allocates "
    "attention; it is not a claim of edge."
)


@dataclass(frozen=True)
class Candidate:
    """One name whose structure is in place and whose trigger has not fired."""

    symbol: str
    session_date: str
    close: float
    prior_high: float
    distance_percent: float
    distance_atr: float
    trigger_sentence: str
    entry_plan: str
    stop_loss_today: float
    stop_note: str
    risk_percent: float
    holding_bars: int
    exit_rule: str
    target_not_measured: float
    target_note: str
    turnover_egp: float
    atr_percent: float
    spread_percent: float
    spread_source: str
    round_trip_cost_percent: float
    net_2r_after_cost_percent: float


@dataclass
class WatchResult:
    session_date: str = ""
    reach_atr: float = DEFAULT_REACH_ATR
    candidates: list = field(default_factory=list)
    #: How many symbols each stage was the *first* to refuse, so an empty week
    #: reads as "nothing is set up" rather than as a fault. Same discipline as
    #: `scan.ScanResult.funnel`.
    funnel: dict = field(default_factory=dict)
    considered: int = 0
    unreadable: dict = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.candidates)


def _costs_for(symbol: str, cfg: BreakoutConfig):
    """That symbol's spread and where it came from, with commission from config.

    `TradingCosts` owns the resolution order -- the measured table first, then
    the conservative rate for names too thinly quoted to measure. Reading it
    here rather than re-implementing it keeps one spread table in the project.

    The round trip reported is commission both sides plus one full spread. The
    backtest additionally charges slippage, which is why `modelled_round_trip`
    is carried alongside: this column answers "what does crossing cost", and
    that one answers "what does the simulator charge".
    """
    try:
        from backtesting.costs import TradingCosts

        costs = TradingCosts(symbol=symbol)
        spread = float(costs.spread_percent)
        source = str(costs.spread_source)
        modelled = float(costs.round_trip_percent())
    except Exception:                                       # noqa: BLE001
        return None, "unavailable", None, None
    round_trip = 2 * float(cfg.commission) * 100 + spread
    return spread, source, round(round_trip, 4), modelled


def _trigger_sentence(prior_high: float, cfg: BreakoutConfig) -> str:
    """What must be true at the close on the day it fires, in his words.

    Every number in it is read from the config, so the sentence cannot drift
    away from the gates that will actually be applied.
    """
    return (f"close above {prior_high}, "
            f"volume >= {cfg.minimum_volume_ratio}x its "
            f"{cfg.turnover_window}-bar average, "
            f"close in the top {round((1 - cfg.minimum_close_position) * 100)}% "
            f"of the bar's range")


def _histories(symbols=None, on_error=None):
    """The same router the live system trades on. Borrowed from `scan.py`."""

    from strategy_momentum_breakout.scan import _histories as scan_histories

    return scan_histories(symbols, on_error=on_error)


def watch(histories=None, cfg: BreakoutConfig | None = None, symbols=None,
          reach_atr: float = DEFAULT_REACH_ATR, on_error=None) -> WatchResult:
    """Every readable symbol whose structure holds and whose trigger has not fired."""

    cfg = cfg or load_config()
    unreadable = {}
    if histories is None:
        histories = _histories(
            symbols,
            on_error=on_error or (lambda s, r: unreadable.setdefault(s, r)))

    result = WatchResult(considered=len(histories), reach_atr=reach_atr,
                         unreadable=unreadable)
    funnel = {"Unusable": 0, "InsufficientHistory": 0,
              **{gate: 0 for gate in STRUCTURAL_GATES},
              "AlreadyTriggered": 0, "OutOfReach": 0, "InvalidRisk": 0}
    sessions = []

    for symbol, frame in histories.items():
        try:
            data = calculate_indicators(frame)
        except Exception:                                   # noqa: BLE001
            funnel["Unusable"] += 1
            continue
        if len(data) <= warmup_bars(cfg):
            funnel["InsufficientHistory"] += 1
            continue

        last = len(data) - 1
        table = measure(data, cfg)
        row = table.iloc[last]
        sessions.append(str(data.index[last])[:10])

        decision = evaluate(data, last, cfg, measured=table)
        checks = decision["Checks"]

        # Structural first, in the rule's own order, so the funnel names the
        # most fundamental thing wrong rather than the last thing checked.
        failed_structural = [g for g in STRUCTURAL_GATES if not checks[g]]
        if failed_structural:
            funnel[failed_structural[0]] += 1
            continue

        if any(checks[g] for g in TRIGGER_GATES):
            funnel["AlreadyTriggered"] += 1
            continue

        close = float(row["Close"])
        prior_high = float(row["PriorHigh"])
        atr = float(data["ATR"].iloc[last])
        if not (close > 0 and prior_high > 0 and atr > 0):
            funnel["Unusable"] += 1
            continue

        distance = prior_high - close
        # Below the trigger, and near it. A close already above PriorHigh cannot
        # reach here -- the Breakout gate would have caught it above.
        if distance <= 0 or distance > reach_atr * atr:
            funnel["OutOfReach"] += 1
            continue

        stop = float(row["StopLoss"])
        if not stop < close:
            funnel["InvalidRisk"] += 1
            continue

        risk_percent = (close - stop) / close * 100
        spread, source, round_trip, modelled = _costs_for(symbol, cfg)
        target = round(close + UNMEASURED_TARGET_R * (close - stop),
                       cfg.price_precision)
        gross_2r = UNMEASURED_TARGET_R * risk_percent
        net_2r = (round(gross_2r - round_trip, 2)
                  if round_trip is not None else None)

        result.candidates.append(Candidate(
            symbol=symbol,
            session_date=str(data.index[last])[:10],
            close=round(close, cfg.price_precision),
            prior_high=round(prior_high, cfg.price_precision),
            distance_percent=round(distance / close * 100, 2),
            distance_atr=round(distance / atr, 2),
            trigger_sentence=_trigger_sentence(
                round(prior_high, cfg.price_precision), cfg),
            entry_plan=f"{cfg.entry_mode} (the session AFTER the trigger closes)",
            stop_loss_today=round(stop, cfg.price_precision),
            stop_note=(f"provisional: the {cfg.stop_window}-bar base low rolls, "
                       f"so recompute on the trigger day"),
            risk_percent=round(risk_percent, 2),
            holding_bars=cfg.holding_bars,
            exit_rule=(f"{cfg.holding_bars} sessions or the stop. "
                       f"This strategy has no target."),
            target_not_measured=target,
            target_note=(f"NOT MEASURED: entry + {UNMEASURED_TARGET_R:g}R on "
                         f"today's stop distance. The strategy names no target."),
            turnover_egp=round(float(row["TurnoverEGP"]), 0),
            atr_percent=round(float(row["ATRPercent"]), 2),
            spread_percent=spread,
            spread_source=source,
            round_trip_cost_percent=round_trip,
            net_2r_after_cost_percent=net_2r,
        ))

    result.funnel = funnel
    # The session most rows agree on, not the newest single row -- `scan.py`'s
    # rule, and for its reason: a universe whose symbols carry different last
    # bars must not be headlined by its outlier.
    if sessions:
        result.session_date = max(set(sessions), key=sessions.count)
    # Nearest to the trigger first. A reading order, not a ranking claim:
    # nothing here has been measured to predict which set-up goes on to fire.
    result.candidates.sort(key=lambda c: c.distance_atr)
    return result


def sweep_reach(histories=None, cfg: BreakoutConfig | None = None, symbols=None,
                multiples=REACH_MULTIPLES) -> dict:
    """Candidate count at each reach multiple, so the default is chosen on counts.

    The histories are loaded once and reused, so the sweep costs one pass over
    the universe rather than one per multiple.
    """
    cfg = cfg or load_config()
    if histories is None:
        histories = _histories(symbols)
    return {m: watch(histories=histories, cfg=cfg, reach_atr=m).count
            for m in multiples}


def as_frame(result: WatchResult) -> pd.DataFrame:
    return pd.DataFrame([asdict(c) for c in result.candidates])
