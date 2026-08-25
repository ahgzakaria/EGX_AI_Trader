"""Volume breakout with a momentum filter — the one thing that measured well.

Everything else tried on this market failed the same test. Intraday scalping
starts 0.72% behind: the round trip costs 0.80% while liquid EGX names drift
+0.078% between open and close, so the cost is more than ten times the whole
average move and no exit rule tested closes that gap. Re-weighting the daily
breakout score, moving its volume gate, fitting the weights jointly, and adding
momentum, relative strength and volatility-regime features all failed to beat
what they replaced once run out of sample.

This did not. Over 165,000 stock-days of the sixty most-traded names, trained
before 2024-01-01 and validated after, holding twenty days and net of the
round trip:

    filter                                  train lift   valid lift   win%
    breakout on 20-day high, volume >=2.5x       +3.17        +2.79     59
      and 12-1 momentum in the top third         +3.90        +5.22     62

"lift" is the return above owning every name in the universe on the same days,
which is the only honest benchmark: the validation era was a strong bull
market and absolute returns there flatter anything.

The momentum filter earns its place rather than being fitted. Tightening it
strengthens the result monotonically -- top 75% gives +3.52, top half +4.44,
top third +5.22 -- and inverting it weakens it, with the bottom half at +1.49
and the bottom quarter at +1.14. Momentum measured alone on this universe is
worth nothing; it works here by separating breakouts that continue from ones
that do not, which is what the frontier-market literature reports.

**What this is not.** It emits research candidates, never orders. It reports no
fill, holds no position, and knows nothing about an account. Three years of
its measurements had five losing years out of eighteen and the median trade
returns 0.88%: the profit is in a thin tail of large winners, which is what a
breakout strategy is, and it is not a machine that prints money.

Re-derive any figure here with `scripts/research/swing_candidates.py` and
`scripts/research/breakout_filters.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

import pandas as pd

#: Trading days in a month, for the momentum window.
MONTH = 21

#: Where the median historical trade actually went inside the holding window,
#: measured over the 1,897 signals the four conditions produced across 25 years
#: on the 5M-turnover universe. Percentages against the entry close.
#:
#: These are NOT a target and NOT a stop. The strategy has neither: seven exit
#: rules were tested against the fixed hold -- trailing stops at three widths,
#: hard stops at two, a longer hold, a trend-break exit -- and none beat it, so
#: there is no measured level to propose. What there is, is a distribution, and
#: showing its quantiles is the difference between reporting a measurement and
#: inventing a price.
#:
#: The lower quartile matters most: 63% of trades fall more than 5% at some
#: point and 39% fall more than 10%, so a position that is down is the normal
#: case rather than a broken one.
EXCURSION_QUANTILES = {
    "worst_point": {"lower": -13.94, "median": -7.89, "upper": -2.99},
    "best_point": {"lower": 6.34, "median": 13.65, "upper": 28.31},
    "at_day_20": {"lower": -7.05, "median": 2.17, "upper": 15.74},
}

#: Share of trades that fell at least this far at some point in the window.
DRAWDOWN_FREQUENCY = {5: 63.4, 10: 39.5, 15: 22.2, 20: 12.4}

#: The engine's own name, stored with every candidate so a stored result stays
#: interpretable after the rules move.
ENGINE_VERSION = "swing-volume-breakout/1.0.0"

MEASUREMENT_PROVENANCE = (
    "measured over data/frozen_eodhd_seed: 60 symbols by median turnover, "
    "165k stock-days, trained before 2024-01-01 and validated after, 20-day "
    "hold, net of a 0.80% round trip. Validation lift over owning the "
    "universe: +5.22%, 62% of trades positive."
)


@dataclass(frozen=True)
class SwingConfig:
    """Every threshold the strategy consults, with why it is where it is."""

    #: Median daily turnover a name must clear to be considered at all, in EGP.
    #: The universe is bounded by liquidity rather than by a count, because a
    #: count is arbitrary and a floor scales with the position it has to
    #: absorb. Swept by floor with no gate changed, the annual lift -- trades
    #: times lift, which is what a portfolio earns -- peaks here at +277%
    #: against +215% at 10M and +252% at 3M, and the two eras agree (+2.99%
    #: training, +3.35% validation). At a 100,000 EGP position this floor also
    #: means never taking more than 2% of a name's daily turnover; ranked by
    #: count instead, the 200th name trades 20,000 EGP a day and the 0.80% cost
    #: assumed for it is fiction. Widening the opportunity set is not loosening
    #: a threshold, and none was loosened.
    minimum_daily_turnover_egp: float = 5_000_000.0

    #: A close above the highest high of this many prior sessions.
    breakout_lookback: int = 20

    #: Volume relative to its own recent average. Splitting breakouts into
    #: disjoint volume bands over the archive: below 1.5x they return -0.11%
    #: over twenty days, worse than not trading; the 1.5-2.5x band returns
    #: +2.26%, no better than sitting out; above 2.5x, +5.73% at a 59% win
    #: rate. The gate goes where the edge starts.
    minimum_volume_ratio: float = 2.5
    volume_average_window: int = 20

    #: The close must sit above its own average over this many sessions. Not a
    #: market filter -- one of those was measured and does nothing, cutting the
    #: return while leaving the fall at -19.5%, because the drawdown comes from
    #: forty positions moving together rather than from any one breaking down.
    #: This is the name's own long trend, and it is the only exit- or
    #: entry-side change of eight tested that improved both eras at once:
    #: +2.99% to +3.66% training, +3.35% to +3.44% validation, with the worst
    #: fall from -19.5% to -16.7%. It removes 8% of trades, and those trades
    #: carry a lift of -2.64% on their own.
    long_trend_window: int = 200

    #: Cross-sectional rank of 12-1 momentum, the standard construction: the
    #: eleven months ending one month ago, skipping the most recent month
    #: because it tends to reverse. Kept at the top third, where the monotone
    #: response peaks before the sample thins.
    minimum_momentum_rank: float = 0.67
    momentum_skip_months: int = 1
    momentum_window_months: int = 12

    #: How long the measurement held for. Not an instruction to exit.
    measured_holding_days: int = 20

    #: Round trip in percent: broker fee schedule plus slippage, both sides.
    #: The spread is measured per name and charged on top.
    round_trip_cost_percent: float = 0.4638

    #: Below this many bars a symbol cannot be ranked or measured.
    minimum_history_bars: int = 260


@dataclass(frozen=True)
class SwingCandidate:
    """One symbol that met every condition on one session."""

    symbol: str
    session_date: str
    close: float
    breakout_level: float
    volume_ratio: float
    momentum_12_1: float
    momentum_rank: float
    atr_percent: Optional[float]

    @property
    def extension_percent(self) -> float:
        """How far past the level the close already sits.

        A breakout that closed well beyond its level is a worse entry than one
        that just cleared it, and this says by how much rather than filtering
        on it: no threshold here has been measured.
        """

        if not self.breakout_level:
            return 0.0
        return (self.close - self.breakout_level) / self.breakout_level * 100.0


@dataclass(frozen=True)
class SwingScan:
    """What one pass over the universe found, and what it could not read."""

    session_date: str
    candidates: tuple = ()
    symbols_considered: int = 0
    symbols_skipped: dict = field(default_factory=dict)
    config: SwingConfig = field(default_factory=SwingConfig)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)


def _indicators(frame: pd.DataFrame, config: SwingConfig) -> Optional[dict]:
    """Everything one symbol contributes, or ``None`` if it cannot contribute."""

    if frame is None or len(frame) < config.minimum_history_bars:
        return None
    columns = {c.lower(): c for c in frame.columns}
    try:
        close = frame[columns["close"]].astype(float)
        high = frame[columns["high"]].astype(float)
        low = frame[columns["low"]].astype(float)
        volume = frame[columns["volume"]].astype(float)
    except (KeyError, ValueError, TypeError):
        return None
    if close.empty or close.iloc[-1] <= 0:
        return None

    # Shifted by one so today's own bar never contributes to the level it must
    # break, which would make every close a breakout of itself.
    level = high.rolling(config.breakout_lookback).max().shift(1).iloc[-1]
    average_volume = volume.rolling(config.volume_average_window).mean().iloc[-1]
    if pd.isna(level) or pd.isna(average_volume) or average_volume <= 0:
        return None

    skip = config.momentum_skip_months * MONTH
    window = config.momentum_window_months * MONTH
    if len(close) <= window:
        return None
    then, recent = close.iloc[-window], close.iloc[-1 - skip]
    if then <= 0:
        return None

    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(14).mean().iloc[-1]
    long_average = close.rolling(config.long_trend_window).mean().iloc[-1]

    return {
        "close": float(close.iloc[-1]),
        "long_trend_average": float(long_average) if pd.notna(long_average) else None,
        "level": float(level),
        "volume_ratio": float(volume.iloc[-1] / average_volume),
        "momentum": float((recent / then - 1) * 100.0),
        "atr_percent": (float(atr / close.iloc[-1] * 100.0)
                        if pd.notna(atr) and close.iloc[-1] else None),
    }


def scan(histories: dict, *, session_date=None,
         config: Optional[SwingConfig] = None) -> SwingScan:
    """Find every symbol meeting all three conditions on the latest bar.

    ``histories`` maps symbol to a daily OHLCV frame ending at the session
    being scanned. The momentum rank is cross-sectional, so a symbol's
    eligibility depends on the rest of the universe that day and the whole
    universe has to be read before any candidate can be named.
    """

    config = config or SwingConfig()
    day = (session_date.isoformat() if isinstance(session_date, date)
           else str(session_date or ""))

    measured, skipped = {}, {}
    for symbol, frame in histories.items():
        values = _indicators(frame, config)
        if values is None:
            skipped[symbol] = "INSUFFICIENT_HISTORY"
            continue
        measured[symbol] = values

    if not measured:
        return SwingScan(session_date=day, symbols_considered=len(histories),
                         symbols_skipped=skipped, config=config)

    # Rank across everything readable, not only across the breakouts: the
    # filter asks where a name sits in the market, and ranking inside the
    # winners would answer a different question.
    ordered = sorted(measured.items(), key=lambda kv: kv[1]["momentum"])
    ranks = {symbol: (index + 1) / len(ordered)
             for index, (symbol, _) in enumerate(ordered)}

    candidates = []
    for symbol, values in measured.items():
        if values["close"] <= values["level"]:
            continue
        if values["volume_ratio"] < config.minimum_volume_ratio:
            skipped[symbol] = "VOLUME_BELOW_GATE"
            continue
        if ranks[symbol] < config.minimum_momentum_rank:
            skipped[symbol] = "MOMENTUM_RANK_BELOW_GATE"
            continue
        average = values["long_trend_average"]
        if average is None or values["close"] <= average:
            # A breakout inside a long downtrend is a bounce in something that
            # is still falling. Measured over 25 years these are 159 trades
            # with a lift of -2.64%: not a smaller edge, a negative one.
            skipped[symbol] = "BELOW_LONG_TREND"
            continue
        candidates.append(SwingCandidate(
            symbol=symbol,
            session_date=day,
            close=values["close"],
            breakout_level=values["level"],
            volume_ratio=values["volume_ratio"],
            momentum_12_1=values["momentum"],
            momentum_rank=ranks[symbol],
            atr_percent=values["atr_percent"],
        ))

    candidates.sort(key=lambda c: -c.volume_ratio)
    return SwingScan(session_date=day, candidates=tuple(candidates),
                     symbols_considered=len(histories),
                     symbols_skipped=skipped, config=config)


# --- the boundary where this stops being pure ---------------------------
#
# Everything above takes a dictionary and returns a result, so it is testable
# without a network, a database or a clock. Everything below reaches for real
# data and is therefore the part that can fail in ways a test will not see.


def load_universe_histories(symbols: Optional[Iterable[str]] = None,
                            *, limit: Optional[int] = None,
                            on_error=None) -> dict:
    """Daily history for each symbol, through the router the system trades on.

    That router carries EODHD history plus the Rubix daily tail, so the latest
    bar is the session that just closed rather than the one the vendor has
    published. A symbol that cannot be read is reported through ``on_error``
    and left out; one unreadable name must not empty the scan.
    """

    from core.research_router import get_current_research_history

    if symbols is None:
        from core.universe import active_symbols

        symbols = sorted(active_symbols())
    symbols = list(symbols)
    if limit:
        symbols = symbols[:limit]

    histories = {}
    for symbol in symbols:
        try:
            result = get_current_research_history(symbol)
            frame = result[0] if isinstance(result, tuple) else result
        except Exception as error:                               # noqa: BLE001
            if on_error is not None:
                on_error(symbol, f"{type(error).__name__}: {str(error)[:120]}")
            continue
        if frame is not None and len(frame):
            histories[symbol] = frame
    return histories


def most_traded(histories: dict, count: int = None,
                minimum_turnover: float = None) -> dict:
    """The names liquid enough to actually trade, which bounds the universe.

    The bound is liquidity, not a count. A count is arbitrary and goes stale;
    a turnover floor scales with the position it has to absorb and adapts as
    the exchange does.

    Swept by floor, with no gate touched (validation era, 20-day hold):

        floor   names  trades/yr   lift/trade   annual   concurrent   win
         20M      53       30        +3.98%      +120%      2.4       61%
         10M      95       58        +3.74%      +215%      4.6       57%
          5M     139       83        +3.35%      +277%      6.6       54%
          3M     162       97        +2.61%      +252%      7.7       53%
          2M     176      104        +2.60%      +269%      8.3       53%
          1M     183      110        +2.35%      +258%      8.8       53%

    Five million is the peak, and it is a peak on the number that matters to a
    portfolio -- annual lift, which is trades times lift, not lift alone.
    Optimising lift per trade instead picks 10M and leaves a quarter of the
    year's edge on the table for the sake of a prettier per-trade figure.

    It is also where execution stays real. At a 100,000 EGP position a 5M floor
    means never taking more than 2% of a name's daily turnover. Ranked by count
    instead, the 200th name trades 20,000 EGP a day -- a backtest can buy it,
    you cannot, and the 0.80% cost assumed there is fiction.

    Below 5M the annual lift stops improving while the win rate slides toward a
    coin and every fill gets harder. ``count`` remains available as an explicit
    override for research that needs a fixed population.
    """
    if minimum_turnover is None:
        minimum_turnover = SwingConfig().minimum_daily_turnover_egp

    turnovers = {}
    for symbol, frame in histories.items():
        columns = {c.lower(): c for c in frame.columns}
        try:
            value = (frame[columns["close"]] * frame[columns["volume"]]).tail(250)
            turnovers[symbol] = float(value.median())
        except (KeyError, ValueError, TypeError):
            continue
    liquid = {s: t for s, t in turnovers.items() if t >= minimum_turnover}
    ranked = sorted(liquid, key=liquid.get, reverse=True)
    if count is not None:
        # Research override only. The live scan passes nothing and takes every
        # name that clears the floor.
        ranked = ranked[:count]
    return {symbol: histories[symbol] for symbol in ranked}
