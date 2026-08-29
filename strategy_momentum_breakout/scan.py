"""Today's CONFIRMED_VOLUME_BREAKOUT signals, over the live universe.

Reads history through `core.research_router.get_current_research_history`, the
same router the rest of the live system trades on, so the latest bar is the
session that just closed rather than whatever the vendor has published. One
unreadable symbol is reported and skipped; it must not empty the scan.

The scan runs `signal.measure` -- the same function the backtest walks -- so a
signal shown here and a signal counted in the backtest are the same signal by
construction rather than by care.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from indicators.technical import calculate_indicators
from strategy_momentum_breakout.config import BreakoutConfig, load as load_config
from strategy_momentum_breakout.signal import GATES, evaluate, warmup_bars


@dataclass(frozen=True)
class Signal:
    """One symbol that met every condition on the session just closed."""

    symbol: str
    session_date: str
    close: float
    prior_high: float
    volume_ratio: float
    close_position: float
    turnover_egp: float
    atr_percent: float
    calm_reference: float
    stop_loss: float
    risk_percent: float
    reasons: list


@dataclass
class ScanResult:
    session_date: str = ""
    signals: list = field(default_factory=list)
    #: How many symbols each gate was the *first* to refuse. A funnel, so a
    #: quiet day can be read as "nothing broke out" rather than as a fault.
    funnel: dict = field(default_factory=dict)
    considered: int = 0
    unreadable: dict = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.signals)


def _histories(symbols=None, on_error=None):
    from core.research_router import get_current_research_history

    if symbols is None:
        from core.universe import active_symbols

        symbols = sorted(active_symbols())

    out = {}
    for symbol in symbols:
        try:
            result = get_current_research_history(symbol)
            frame = result[0] if isinstance(result, tuple) else result
        except Exception as error:                          # noqa: BLE001
            if on_error is not None:
                on_error(symbol, f"{type(error).__name__}: {str(error)[:120]}")
            continue
        if frame is not None and len(frame):
            out[symbol] = frame
    return out


def scan(histories=None, cfg: BreakoutConfig | None = None,
         symbols=None, on_error=None) -> ScanResult:
    """Evaluate the last bar of every readable symbol."""
    cfg = cfg or load_config()
    unreadable = {}
    if histories is None:
        histories = _histories(
            symbols,
            on_error=on_error or (lambda s, r: unreadable.setdefault(s, r)))

    result = ScanResult(considered=len(histories), unreadable=unreadable)
    funnel = {reason: 0 for reason in GATES + ("InsufficientHistory",
                                               "InvalidRisk", "Unusable")}
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
        decision = evaluate(data, last, cfg)
        sessions.append(str(data.index[last])[:10])
        if decision["Signal"] != "BUY":
            reason = decision["RejectReason"] or "Unusable"
            funnel[reason] = funnel.get(reason, 0) + 1
            continue

        m = decision["Measurements"]
        result.signals.append(Signal(
            symbol=symbol,
            session_date=str(data.index[last])[:10],
            close=m["Close"],
            prior_high=m["PriorHigh"],
            volume_ratio=m["VolumeRatio"],
            close_position=m["ClosePosition"],
            turnover_egp=m["TurnoverEGP"],
            atr_percent=m["ATRPercent"],
            calm_reference=m["CalmReference"],
            stop_loss=decision["StopLoss"],
            risk_percent=decision["ReferenceRiskPercent"],
            reasons=decision["Reasons"],
        ))

    result.funnel = funnel
    # The session most rows agree on, not the newest single row: a scan whose
    # symbols carry different last bars must not be headlined by its outlier.
    if sessions:
        result.session_date = max(set(sessions), key=sessions.count)
    # Strongest confirmation first. This is a reading order, not a ranking
    # claim: nothing here has been measured to predict which signal does best.
    result.signals.sort(key=lambda s: -s.volume_ratio)
    return result


def as_frame(result: ScanResult) -> pd.DataFrame:
    return pd.DataFrame([asdict(s) for s in result.signals])
