"""Independent conservative intraday setup detectors."""

from __future__ import annotations

import pandas as pd

from scalping.config import ScalpingConfig
from scalping.decision import assess_actionability, spread_percent
from scalping.models import MarketSnapshot, Opportunity, SetupType


REQUIRED = {"Open", "High", "Low", "Close", "Volume"}


def sanitize_intraday_bars(bars: pd.DataFrame):
    """Reject impossible source candles without filling or inventing prices.

    Rubix can emit pre-open heartbeat rows with zero OHLC values. Those rows
    are transport evidence, not tradable candles, and must never enter an
    indicator denominator or opening-range calculation.
    """

    if bars is None or bars.empty or not REQUIRED.issubset(bars.columns):
        return pd.DataFrame(columns=sorted(REQUIRED))
    frame = bars.sort_index().copy()
    for column in REQUIRED:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    prices = frame[["Open", "High", "Low", "Close"]]
    valid = (
        prices.notna().all(axis=1)
        & prices.gt(0).all(axis=1)
        & frame["Volume"].notna()
        & frame["Volume"].ge(0)
        & frame["High"].ge(prices[["Open", "Low", "Close"]].max(axis=1))
        & frame["Low"].le(prices[["Open", "High", "Close"]].min(axis=1))
    )
    cleaned = frame.loc[valid].copy()
    cleaned = cleaned[~cleaned.index.duplicated(keep="last")].sort_index()
    cleaned.attrs["invalid_bars_removed"] = int((~valid).sum())
    cleaned.attrs["source_bar_count"] = int(len(frame))
    return cleaned


def detect_setups(bars: pd.DataFrame, snapshot: MarketSnapshot, config: ScalpingConfig):
    frame = sanitize_intraday_bars(bars)
    if frame.empty:
        return []
    minimum = max(config.breakout_lookback_bars + 1, config.opening_range_minutes + 1)
    if len(frame) < minimum:
        return []
    valid, blocked = assess_actionability(snapshot, config)
    current = frame.iloc[-1]
    prior = frame.iloc[:-1]
    ask = float(snapshot.ask or current["Close"])
    bid = float(snapshot.bid or current["Close"])
    volume = float(snapshot.volume or current["Volume"])
    spread = spread_percent(bid, ask) or 0.0
    rolling_volume = prior["Volume"].tail(20).mean()
    relative_volume = float(current["Volume"] / rolling_volume) if rolling_volume > 0 else 0.0
    lookback = prior.tail(config.momentum_lookback_bars)
    momentum_base = float(lookback.iloc[0]["Close"]) if len(lookback) else 0.0
    momentum = (
        (float(current["Close"]) / momentum_base - 1.0) * 100.0
        if momentum_base > 0 else 0.0
    )
    resistance = float(prior["High"].tail(60).max())
    # A clean breakout above the observed resistance has no known overhead
    # level inside the lookback; otherwise the known resistance must sit past
    # the fixed target.
    room_to_target = (
        float(current["Close"]) > resistance
        or resistance >= ask * (1.0 + config.take_profit_percent / 100.0)
    )
    common = (
        relative_volume >= config.minimum_relative_volume
        and momentum >= config.minimum_momentum_percent
        and room_to_target
    )
    opportunities = []

    def add(setup, reasons, setup_score):
        opportunities.append(Opportunity(
            ticker=snapshot.ticker, setup=setup, timestamp=snapshot.timestamp,
            signal_price=float(current["Close"]), ask=ask, bid=bid, volume=volume,
            spread_percent=spread, score=float(setup_score), reasons=tuple(reasons),
            freshness=snapshot.freshness, actionable=valid and common,
            blocked_reason=(" | ".join(blocked) if blocked else (
                None if common else "TECHNICAL_GATE_NOT_MET"
            )),
        ))

    breakout_high = float(prior["High"].tail(config.breakout_lookback_bars).max())
    if float(current["Close"]) > breakout_high:
        add(SetupType.MOMENTUM_BREAKOUT, (
            "Close above short-term breakout high",
            f"Momentum {momentum:.2f}%", f"Relative volume {relative_volume:.2f}x",
            "Resistance room supports the fixed target" if room_to_target else "Insufficient target room",
        ), 70 + min(15, momentum) + min(15, relative_volume * 3))

    local = frame.index.tz_convert("Africa/Cairo") if frame.index.tz is not None else frame.index
    session_date = local[-1].date()
    session = frame[pd.Index(local.date) == session_date]
    opening = session.head(config.opening_range_minutes)
    if len(session) > config.opening_range_minutes and len(opening):
        opening_high = float(opening["High"].max())
        if float(current["Close"]) > opening_high:
            add(SetupType.OPENING_RANGE_BREAKOUT, (
                "Close above opening range high", f"Opening high {opening_high:.3f}",
                f"Relative volume {relative_volume:.2f}x",
            ), 72 + min(14, momentum) + min(14, relative_volume * 3))

    typical = (session["High"] + session["Low"] + session["Close"]) / 3.0
    cumulative_volume = session["Volume"].cumsum().replace(0, pd.NA)
    vwap = (typical * session["Volume"]).cumsum() / cumulative_volume
    if len(session) >= 2 and pd.notna(vwap.iloc[-1]) and pd.notna(vwap.iloc[-2]):
        reclaimed = float(session.iloc[-2]["Close"]) <= float(vwap.iloc[-2]) and float(current["Close"]) > float(vwap.iloc[-1])
        pullback = float(current["Low"]) <= float(vwap.iloc[-1]) <= float(current["Close"])
        if reclaimed or pullback:
            add(SetupType.VWAP_RECLAIM_FIRST_PULLBACK, (
                "VWAP reclaim" if reclaimed else "First pullback held VWAP",
                f"VWAP {float(vwap.iloc[-1]):.3f}", f"Momentum {momentum:.2f}%",
            ), 68 + min(16, momentum) + min(16, relative_volume * 3))

    return opportunities
