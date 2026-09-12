"""Configuration for CONFIRMED_VOLUME_BREAKOUT, and where each number came from.

Every threshold below was swept one notch either side of its chosen value with
everything else held fixed, and the lift over owning the same pool on the same
days stayed positive across the whole sweep in both eras. The sweeps are in
`docs/audits/strategies/CONFIRMED_VOLUME_BREAKOUT.md`; a threshold whose
neighbours collapse is a fitted number, and none of these do.

The file is read live, like the rest of the project's settings, so a change
takes effect on the next scan without a restart. It is deliberately *not* part
of `config/settings.json`: that file configures the Daily Dashboard strategy,
and the two are separate strategies with separate calibrations. Sharing a
settings section is how `min_rr 3.0` came to be calibrated in one context and
inherited into another that nobody had measured.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("settings.json")


@dataclass(frozen=True)
class BreakoutConfig:
    strategy_name: str = "CONFIRMED_VOLUME_BREAKOUT"
    enabled_by_default: bool = False
    decision_support_only: bool = True

    #: A signal in a name that trades less in a day than one position is worth
    #: is a signal you cannot act on. Swept 1M/2M/5M/10M: validation lift
    #: +1.89/+1.92/+1.65/+0.91. The floor does not create the result, which is
    #: the point of measuring it -- but see the limitation in the audit: a fixed
    #: EGP floor is progressively easier to clear across a decade in which the
    #: currency lost roughly three quarters of its value.
    minimum_turnover_egp: float = 2_000_000
    turnover_window: int = 20

    #: The name's own long-term trend, not the index's. This strategy has no
    #: index gate, and the reason is measured rather than circumstantial:
    #:
    #: When this was written `^CASE30` was served by no provider here, so the
    #: Daily Dashboard's `require_market_analyzer` gate defaulted to "allow" on
    #: every bar in backtest and live alike. That is no longer true -- the index
    #: is in the frozen record and in the measured store, from MubasherTrade
    #: PRO -- and nothing about this strategy changes, because the absence of
    #: the series was never the argument:
    #:
    #: An equal-weighted breadth index built from the universe *was* measured as
    #: a replacement and did not earn its place: it removed 68 of 971 trades and
    #: left the validation lift unchanged at +2.33%. `Close > EMA200` is
    #: measured better (+2.57%), costs nothing, and is readable off the symbol's
    #: own chart. (Those two figures are from the candidate as it stood at that
    #: comparison; every number quoted against the rule *as shipped* comes from
    #: `scripts/research/shipped_rule_evidence.py`, which imports this module
    #: rather than restating it.)
    trend_reference: str = "EMA200"
    #: EMA200 is mostly its own seed until it has seen two hundred bars, so no
    #: bar before that is judged. Configurable only so a test can build a short
    #: frame; production has no reason to move it.
    trend_warmup_bars: int = 200

    #: Calm relative to the name's own past year, not to its peers. Swept
    #: 120/250/500 bars: validation lift +1.15/+1.92/+1.95.
    calm_window: int = 250

    #: The trigger. Swept 10/15/20/30/40 bars: validation lift stays between
    #: +1.54% and +2.06%, so the base length is a plateau, not a peak.
    breakout_window: int = 20
    #: Swept 1.5/2.0/2.5/3.0/4.0: +1.01/+1.25/+1.92/+1.41/+2.45.
    minimum_volume_ratio: float = 2.5
    #: The breakout has to *hold* into the close, not just print intraday.
    #: Swept 0.5/0.6/0.7/0.8/0.9: +1.60/+1.97/+1.92/+0.74/+0.97.
    minimum_close_position: float = 0.70

    #: The signal is a close, so it is known only after the close. The first
    #: price anybody can actually pay is the next session's. Measuring from the
    #: signal's own close overstates the lift by about 1.3 points, because the
    #: day after a volume breakout returns +1.06% against a +0.13% baseline --
    #: a continuation you have to buy, not one you collect.
    #:
    #: The open is never used. In this project's data it is carried forward from
    #: the previous close on 96-98% of bars and is not a tradeable price.
    entry_mode: str = "NEXT_CLOSE"

    #: Under the base that was just broken. Deliberately wide: stop distance was
    #: swept and the response is monotone and brutal -- at 1.5 ATR (5.5% risk
    #: per share) the validation lift is -1.36%, at 2.5 ATR -0.47%, at 4 ATR
    #: +1.41%, and under the base (20.4% risk per share) +1.92%. A stop inside
    #: this market's daily noise converts winners into losers. Risk is
    #: controlled by position size, which falls automatically as the stop
    #: widens.
    #:
    #: No stop at all measures better still (+2.57%). It is not taken, because
    #: an undefined loss on a single position is not a risk policy and a panel
    #: of 190 symbols that all still exist cannot price the one that goes to
    #: zero.
    stop_window: int = 20
    stop_atr_buffer: float = 0.3

    #: Swept 10/15/20/25/30/40 bars: validation lift
    #: +1.10/+1.22/+1.92/+0.92/+0.41/-0.25. Twenty is the peak, with a mechanism
    #: on each side: below it the drift does not cover the ~1.4% round trip,
    #: above it the breakout's edge has decayed, which `entry_day_decay.py`
    #: measures independently on the raw trigger.
    #:
    #: An earlier sweep reported this rising monotonically to +3.04% at thirty
    #: bars and the cap was nearly set there. It was wrong: the benchmark was
    #: built once at twenty bars and reused, so a forty-bar trade was compared
    #: against a twenty-bar benchmark and credited with the market's own extra
    #: drift. See CONFIRMED_VOLUME_BREAKOUT.md section 5.
    holding_bars: int = 20

    #: EGX enforces a daily price limit -- ±10% on most listings, wider on a few
    #: and suspended in defined circumstances, but never anything like this. A
    #: single session that moves more than 30% either way is therefore not a
    #: market move: it is a split, a bonus issue, a resumption after suspension,
    #: or bad data. It matters because this project backtests on **unadjusted**
    #: prices -- `Close` differs from `Adj Close` on 24 of 40 sampled symbols --
    #: so a two-for-one split arrives in the data as a 50% collapse.
    #:
    #: Ninety-nine bars of 382,646 breach it, and untreated they were not a
    #: rounding error but the entire tail of the result in both directions:
    #:
    #: * five trades closed as stops at -30.6%, -36.6%, -49.4%, -53.0% and
    #:   -67.8%, the last reported as the strategy's largest loser. A holder of
    #:   a split stock has their position adjusted by the broker; it does not
    #:   halve;
    #: * and the strategy's largest *winner*, +78,117 EGP on EHDR.CA, sat across
    #:   2025-10-21, a single session printing +394.9%.
    #:
    #: Removing only the losses would have been marking one's own homework. The
    #: guard is symmetric, and it costs the headline return more than it saves:
    #: +157.73% becomes +113.12%. What it buys is a result that is about the
    #: rule rather than about the data feed -- best trade 10,678 rather than
    #: 78,117, worst -3,801 rather than -18,231, Sharpe 1.28 rather than 0.86.
    #:
    #: A bar beyond this threshold does not trigger a stop: the position closes
    #: at the last clean price, labelled `CorporateAction`. And no signal is
    #: taken while one sits inside the windows the rule reads, because a
    #: dislocated price contaminates the twenty-day high and the volume average
    #: alike.
    maximum_session_move_percent: float = 30.0

    #: Portfolio settings are owned here rather than read from
    #: `config/settings.json -> backtest`. Owning them is the point: `min_rr
    #: 3.0` was calibrated against one configuration and would have been
    #: inherited into another nobody had measured, and §4 of
    #: `MIN_RR_AS_RISK_CONTROL.md` is the demonstration that such combinations
    #: do not inherit their parts' results.
    #:
    #: **Read the cap off the arithmetic, not off this file.** The simulator
    #: holds `min(max_open_positions, floor(max_portfolio_risk_percent /
    #: risk_percent))` positions. At 1.0 / 15.0 / 15 both terms are 15, which is
    #: deliberate -- the two limits agree, so neither is silently overriding the
    #: other.
    #:
    #: These were 2.0 / 10.0 / 10 until 2026-08-29, which produced a cap of
    #: **five**, not ten, and refused 628 of 927 signals. Swept in
    #: `CAPACITY_IS_THE_CONSTRAINT.md`, spreading the same total risk across
    #: fifteen positions instead of five dominates that on both axes at once,
    #: marked to market:
    #:
    #:                        taken    return   maxDD   worst day
    #:     2.0 / 10.0 / 10    32%     +107.7%   15.2%    -10.10%
    #:     1.0 / 15.0 / 15    68%     +133.1%   11.5%     -5.02%
    #:
    #: Those drawdowns are marked to market. `backtesting/equity.py` booked
    #: profit only at exit until 2026-08-29 and would have reported 14.22% and
    #: 8.79% for the same two runs -- see DRAWDOWN_WAS_UNDERSTATED.md.
    #:
    #: Three things had to be true before that was worth doing, and each was
    #: measured rather than assumed:
    #:
    #: * the refused trades were **not** the weak ones -- they measured better
    #:   than the taken ones, at t = -0.89, so the cap was rationing at random;
    #: * the flat drawdown is real diversification and not a measurement
    #:   artifact -- `backtesting/equity.py` booked profit only at exit and never
    #:   marked open positions to market, which hid more the more positions were
    #:   held, so every figure above was re-measured on a daily mark. That defect
    #:   was then fixed project-wide;
    #: * and the worst single session **falls** as positions are added, 10.1% to
    #:   5.0%. Concentration produces the tail day, not capacity.
    #:
    #: Twenty and thirty positions measure better still and were not taken: at
    #: 96-100% of equity committed there is no cash buffer, and this backtest
    #: cannot price that -- no gap risk beyond the daily close, no margin, no
    #: forced liquidation. Fifteen is the widest setting that still funds itself,
    #: at 82% peak deployment and zero signals refused for want of cash.
    initial_capital: float = 100_000
    risk_percent: float = 1.0
    max_open_positions: int = 15
    max_portfolio_risk_percent: float = 15.0
    allow_overlapping_trades: bool = True

    #: Three decimals, not two. `strategy/entry.py` rounds every price to two,
    #: which on the sixteen universe names trading under 1 EGP distorts the
    #: price by 2.31% on average -- and the reward/risk ratio is a quotient of
    #: two rounded differences, so the error compounds instead of cancelling.
    price_precision: int = 3

    commission: float = 0.001819
    slippage: float = 0.0005


def load(path: Path | None = None) -> BreakoutConfig:
    """Read the configuration file, falling back to the measured defaults.

    Unknown keys are ignored rather than raising, so a file written by a newer
    version does not stop an older one from running.
    """
    source = path or CONFIG_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BreakoutConfig()
    known = {field.name for field in fields(BreakoutConfig)}
    return BreakoutConfig(**{k: v for k, v in raw.items() if k in known})
