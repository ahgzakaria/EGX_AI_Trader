"""Daily kill switches and fill-independent account-risk sizing."""

from __future__ import annotations

from datetime import timedelta

from scalping.config import ScalpingConfig
from scalping.models import DailyRiskState


def position_size(capital, entry_price, config: ScalpingConfig, available_liquidity=None):
    cash_risk = float(capital) * config.risk_per_trade_percent / 100.0
    stop_distance = float(entry_price) * config.stop_loss_percent / 100.0
    by_risk = int(cash_risk // stop_distance) if stop_distance > 0 else 0
    exposure_cap = float(capital) * config.max_exposure_per_symbol_percent / 100.0
    by_exposure = int(exposure_cap // float(entry_price)) if entry_price > 0 else 0
    quantity = max(0, min(by_risk, by_exposure))
    if available_liquidity is not None:
        quantity = min(quantity, max(0, int(float(available_liquidity))))
    return quantity


def can_open(state: DailyRiskState, ticker, now, config: ScalpingConfig):
    if not config.enabled:
        return False, "MODULE_DISABLED"
    if config.mode != "PAPER_ONLY":
        return False, "UNSUPPORTED_MODE"
    if state.open_positions >= config.max_open_positions:
        return False, "MAX_OPEN_POSITIONS"
    if state.trades_count >= config.max_trades_per_day:
        return False, "MAX_DAILY_TRADES"
    if state.consecutive_losses >= config.max_consecutive_losses:
        return False, "CONSECUTIVE_LOSS_KILL_SWITCH"
    loss_limit = state.starting_equity * config.max_daily_loss_percent / 100.0
    if state.realized_pnl <= -loss_limit:
        return False, "DAILY_LOSS_KILL_SWITCH"
    if state.portfolio_heat >= state.starting_equity * config.portfolio_heat_percent / 100.0:
        return False, "PORTFOLIO_HEAT_LIMIT"
    previous = state.last_exit_at.get(ticker)
    if previous and now - previous < timedelta(minutes=config.revenge_cooldown_minutes):
        return False, "REVENGE_REENTRY_COOLDOWN"
    return True, None


def apply_close(state: DailyRiskState, ticker, result):
    state.realized_pnl += result.realized_pnl
    state.open_positions = max(0, state.open_positions - 1)
    state.consecutive_losses = state.consecutive_losses + 1 if result.realized_pnl < 0 else 0
    state.last_exit_at[ticker] = result.exited_at
    state.symbol_exposure.pop(ticker, None)
    return state
