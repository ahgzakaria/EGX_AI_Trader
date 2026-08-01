"""Lookahead-safe historical evaluator for the AI pullback research scenario.

This module is intentionally isolated from production scoring and BUY decisions.  It
evaluates each cutoff using only bars available at that timestamp, enters one completed
bar later, applies transaction costs and can write a reproducible JSON/CSV audit bundle.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from core import ai_analysis_evidence as evidence
from core.ai_pullback_config import (
    DEFAULT_PULLBACK_RESEARCH_CONFIG,
    PullbackResearchConfig,
)
from core.ai_pullback_scenario import PULLBACK_ENGINE_VERSION, evaluate_pullback_scenario
from core.ai_stock_analysis_contract import PullbackState, ScenarioState


RESEARCH_EVALUATOR_VERSION = "ai_pullback_research@2.0.0"
DEFAULT_REPORT_DIR = Path("reports/audits/strategies/pullback_entry")


@dataclass(frozen=True)
class PullbackResearchReport:
    report_id: str
    evaluator_version: str
    pullback_engine_version: str
    universe_snapshot: tuple[str, ...]
    start_date: str | None
    end_date: str | None
    data_sources: tuple[tuple[str, str], ...]
    configuration: dict
    summary: dict
    observations: tuple[dict, ...]
    unmanaged_forward_returns: tuple[dict, ...] = ()
    managed_trades: tuple[dict, ...] = ()
    gate_pass_rates: tuple[dict, ...] = ()
    rejection_counts: tuple[dict, ...] = ()
    run_metadata: dict = field(default_factory=dict)


def _series(frame):
    close = frame["Close"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    return (
        evidence._ema(close, 20),
        evidence._ema(close, 50),
        evidence._atr_wilder(high, low, close),
    )


def _prepared_series(frame):
    close = frame["Close"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    volume = frame["Volume"].astype(float)
    macd, macd_signal, macd_hist = evidence._macd(close)
    return {
        "ema20": evidence._ema(close, 20),
        "ema50": evidence._ema(close, 50),
        "ema200": evidence._ema(close, 200),
        "sma20": evidence._sma(close, 20),
        "sma50": evidence._sma(close, 50),
        "sma200": evidence._sma(close, 200),
        "atr14": evidence._atr_wilder(high, low, close),
        "rsi14": evidence._rsi_wilder(close),
        "macd_hist": macd_hist,
        "average_volume20": volume.rolling(evidence.AVG_VOLUME_WINDOW,
                                            min_periods=1).mean(),
    }


def _breakout_ready_at(frame, prepared, position, volume_safe):
    close = frame["Close"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    sma_vals = {period: evidence._last(prepared[f"sma{period}"].iloc[:position + 1])
                for period in evidence.SMA_PERIODS}
    ema_vals = {period: evidence._last(prepared[f"ema{period}"].iloc[:position + 1])
                for period in evidence.EMA_PERIODS}
    last_close = float(close.iloc[position])
    trend, _ = evidence._classify_trend(last_close, sma_vals, ema_vals)
    rsi = evidence._last(prepared["rsi14"].iloc[:position + 1])
    momentum, _ = evidence._classify_momentum(
        rsi, evidence._last(prepared["macd_hist"].iloc[:position + 1]))
    average = (_safe_float(prepared["average_volume20"].iloc[position])
               if volume_safe else None)
    last_volume = float(frame["Volume"].iloc[position])
    ratio = last_volume / average if average and average > 0 else None
    scenarios = evidence._scenarios(
        last_close=last_close,
        channel_high=float(high.iloc[max(0, position - evidence.CHANNEL_WINDOW + 1):
                                     position + 1].max()),
        channel_low=float(low.iloc[max(0, position - evidence.CHANNEL_WINDOW + 1):
                                   position + 1].min()),
        atr_val=_safe_float(prepared["atr14"].iloc[position]),
        trend=trend.value,
        momentum=momentum.value,
        volume_safe=volume_safe,
        volume_ratio=ratio,
        usable=True,
    )
    return bool(scenarios and scenarios[0].state == ScenarioState.READY_WITH_CONDITIONS)


def _safe_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _maximum_drawdown(returns):
    if not returns:
        return None
    equity = np.concatenate((
        np.asarray([1.0]),
        np.cumprod(1.0 + np.asarray(returns, dtype=float) / 100.0),
    ))
    peaks = np.maximum.accumulate(equity)
    drawdowns = (equity / peaks - 1.0) * 100.0
    return round(float(drawdowns.min()), 4)


def _unmanaged_forward_rows(frame, signal_position, scenario, symbol, horizons):
    """Signal-close to future-close diagnostics; no stop/target execution is implied."""
    signal_close = float(frame["Close"].iloc[signal_position])
    rows = []
    for horizon in horizons:
        end = signal_position + horizon
        path = frame.iloc[signal_position + 1:end + 1]
        if end >= len(frame) or path.empty:
            continue
        rows.append({
            "symbol": symbol,
            "signal_date": pd.Timestamp(frame.index[signal_position]).date().isoformat(),
            "horizon_bars": int(horizon),
            "signal_close": round(signal_close, 6),
            "horizon_close": round(float(frame["Close"].iloc[end]), 6),
            "close_to_close_return_percent": round(
                (float(frame["Close"].iloc[end]) / signal_close - 1.0) * 100.0, 6),
            "mae_percent": round(
                (float(path["Low"].min()) / signal_close - 1.0) * 100.0, 6),
            "mfe_percent": round(
                (float(path["High"].max()) / signal_close - 1.0) * 100.0, 6),
            "managed_exit_applied": False,
        })
    return tuple(rows)


def simulate_managed_pullback_trade(
    frame: pd.DataFrame,
    *,
    signal_position: int,
    scenario,
    symbol: str,
    maximum_holding_bars: int,
    transaction_cost_bps: float,
):
    """Simulate one long trade with stop/targets active from the delayed entry bar.

    Entry is the next session open.  When a daily candle touches the stop and any target,
    the stop is assumed first.  When both targets trade without a stop, target 1 is the
    conservative full-position exit; target 2 is recorded as a diagnostic touch only.
    """
    entry_position = signal_position + 1
    if entry_position >= len(frame):
        return None
    entry = float(frame["Open"].iloc[entry_position])
    stop = _safe_float(scenario.stop_loss)
    target_1 = _safe_float(scenario.target_1)
    target_2 = _safe_float(scenario.target_2)
    if stop is None or target_1 is None or not (stop < entry < target_1):
        return None
    end_position = min(len(frame) - 1, entry_position + int(maximum_holding_bars))
    risk_amount = entry - stop
    exit_position = end_position
    exit_price = float(frame["Close"].iloc[end_position])
    outcome = "TIME_EXIT"
    first_touch = "NONE"
    ambiguous_same_bar = False
    target_2_touched = False
    for position in range(entry_position, end_position + 1):
        row = frame.iloc[position]
        opened = float(row["Open"])
        high = float(row["High"])
        low = float(row["Low"])
        stop_touched = low <= stop
        target_1_touched = high >= target_1
        target_2_bar = target_2 is not None and high >= target_2
        if stop_touched and (target_1_touched or target_2_bar):
            outcome = "STOP_SAME_BAR_AMBIGUOUS"
            first_touch = "STOP_CONSERVATIVE"
            ambiguous_same_bar = True
            exit_price = min(opened, stop)
            exit_position = position
            break
        if stop_touched:
            outcome = "STOP"
            first_touch = "STOP"
            exit_price = min(opened, stop)
            exit_position = position
            break
        if target_1_touched:
            outcome = "TARGET_1"
            first_touch = "TARGET_1"
            target_2_touched = bool(target_2_bar)
            exit_price = target_1
            exit_position = position
            break
    path_before_exit = frame.iloc[entry_position:exit_position]
    adverse_prices = list(path_before_exit["Low"].astype(float))
    favorable_prices = list(path_before_exit["High"].astype(float))
    exit_row = frame.iloc[exit_position]
    exit_open = float(exit_row["Open"])
    if outcome in ("STOP", "STOP_SAME_BAR_AMBIGUOUS"):
        # Daily OHLC cannot order the exit candle.  For a long, use the
        # conservative boundary sequence: the adverse stop occurs before any
        # unobserved favorable excursion.  A gap below the stop exits at open.
        adverse_prices.append(exit_price)
        favorable_prices.append(exit_open)
    elif outcome == "TARGET_1":
        # The stop was not touched, so count the candle's adverse excursion,
        # then cap favorable excursion at the target where the trade exited.
        adverse_prices.append(float(exit_row["Low"]))
        favorable_prices.append(target_1)
    else:
        adverse_prices.append(float(exit_row["Low"]))
        favorable_prices.append(float(exit_row["High"]))
    gross_return = (exit_price / entry - 1.0) * 100.0
    cost_percent = float(transaction_cost_bps) / 100.0
    net_return = gross_return - cost_percent
    risk_percent = risk_amount / entry * 100.0
    return {
        "symbol": symbol,
        "signal_date": pd.Timestamp(frame.index[signal_position]).date().isoformat(),
        "entry_date": pd.Timestamp(frame.index[entry_position]).date().isoformat(),
        "exit_date": pd.Timestamp(frame.index[exit_position]).date().isoformat(),
        "entry_price": round(entry, 6),
        "stop_loss": round(stop, 6),
        "target_1": round(target_1, 6),
        "target_2": round(target_2, 6) if target_2 is not None else None,
        "exit_price": round(exit_price, 6),
        "outcome": outcome,
        "first_touch_outcome": first_touch,
        "same_bar_ambiguity": ambiguous_same_bar,
        "same_bar_policy": "STOP_FIRST_CONSERVATIVE",
        "intrabar_excursion_policy": "ADVERSE_BOUNDARY_FIRST_AND_STOP_AT_EXIT",
        "target_1_hit": outcome == "TARGET_1",
        "target_2_touched_before_exit": target_2_touched,
        "stop_hit": outcome in ("STOP", "STOP_SAME_BAR_AMBIGUOUS"),
        "holding_bars": int(exit_position - entry_position + 1),
        "gross_return_percent": round(gross_return, 6),
        "transaction_cost_bps": float(transaction_cost_bps),
        "realized_return_percent": round(net_return, 6),
        "r_multiple": round(net_return / risk_percent, 6) if risk_percent > 0 else None,
        "mae_to_exit_percent": round(
            (min(adverse_prices) / entry - 1.0) * 100.0, 6),
        "mfe_to_exit_percent": round(
            (max(favorable_prices) / entry - 1.0) * 100.0, 6),
        "path_stopped_at_exit": True,
    }


def _gate_diagnostics(observations):
    gate_names = tuple(name for name, _ in observations[0]["gate_results"]) if observations else ()
    rows = []
    cumulative_alive = set(range(len(observations)))
    for gate in gate_names:
        statuses = [dict(row["gate_results"]).get(gate, "SKIPPED") for row in observations]
        passed = sum(status == "PASS" for status in statuses)
        failed = sum(status == "FAIL" for status in statuses)
        skipped = sum(status == "SKIPPED" for status in statuses)
        cumulative_alive = {
            index for index in cumulative_alive if statuses[index] == "PASS"}
        rows.append({
            "gate": gate,
            "pass_count": passed,
            "fail_count": failed,
            "skipped_count": skipped,
            "evaluated_count": passed + failed,
            "individual_pass_rate": (
                round(passed / (passed + failed), 6) if passed + failed else None),
            "cumulative_pass_count": len(cumulative_alive),
            "cumulative_pass_rate": (
                round(len(cumulative_alive) / len(observations), 6)
                if observations else None),
        })
    reason_counts = {}
    primary_counts = {}
    for row in observations:
        primary = row.get("primary_rejection_reason")
        if primary:
            primary_counts[primary] = primary_counts.get(primary, 0) + 1
            reason_counts.setdefault(primary, 0)
        for reason in row.get("rejection_reasons", ()):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    rejections = tuple({
        "reason": reason,
        "all_contributing_count": count,
        "primary_count": primary_counts.get(reason, 0),
    } for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])))
    return tuple(rows), rejections


def _aggregate(observations, horizons, unmanaged_rows, managed_trades):
    confirmed = [row for row in observations if row["pullback_confirmed"]]
    state_counts = {
        state: sum(row["state"] == state for row in observations)
        for state in sorted({row["state"] for row in observations})
    }
    total = len(observations)
    breakout_count = sum(bool(row["breakout_confirmed"]) for row in observations)
    overlap_count = sum(bool(row["pullback_confirmed"]
                             and row["breakout_confirmed"])
                        for row in observations)
    summary = {
        "scenarios_generated": total,
        "state_counts": state_counts,
        "confirmed_entry_count": len(confirmed),
        "pullback_confirmation_rate": (round(len(confirmed) / total, 6)
                                       if total else None),
        "breakout_confirmed_count": breakout_count,
        "breakout_confirmation_rate": (round(breakout_count / total, 6)
                                       if total else None),
        "overlap_count": overlap_count,
        "overlap_rate": (round(overlap_count / total, 6) if total else None),
    }
    for horizon in horizons:
        selected = [row for row in unmanaged_rows if row["horizon_bars"] == horizon]
        values = [row["close_to_close_return_percent"] for row in selected]
        summary.setdefault("unmanaged_forward_returns", {})[f"{horizon}_bars"] = {
            "count": len(values),
            "win_rate": (round(sum(value > 0 for value in values) / len(values), 6)
                         if values else None),
            "average_return_percent": (round(float(np.mean(values)), 6)
                                       if values else None),
            "median_return_percent": (round(float(np.median(values)), 6)
                                      if values else None),
            "worst_mae_percent": (
                round(min(row["mae_percent"] for row in selected), 6)
                if selected else None),
            "best_mfe_percent": (
                round(max(row["mfe_percent"] for row in selected), 6)
                if selected else None),
        }
    ordered_managed = sorted(
        managed_trades,
        key=lambda row: (str(row.get("entry_date", "")), str(row.get("symbol", ""))),
    )
    realized = [row["realized_return_percent"] for row in ordered_managed]
    r_multiples = [row["r_multiple"] for row in ordered_managed
                   if row.get("r_multiple") is not None]
    gains = sum(value for value in realized if value > 0)
    losses = abs(sum(value for value in realized if value < 0))
    first_touch_counts = {
        outcome: sum(row["first_touch_outcome"] == outcome for row in managed_trades)
        for outcome in sorted({row["first_touch_outcome"] for row in managed_trades})
    }
    summary.update({
        "managed_trades": {
            "count": len(managed_trades),
            "stop_hit_rate": (
                round(sum(row["stop_hit"] for row in managed_trades)
                      / len(managed_trades), 6) if managed_trades else None),
            "target_1_hit_rate": (
                round(sum(row["target_1_hit"] for row in managed_trades)
                      / len(managed_trades), 6) if managed_trades else None),
            "target_2_touch_rate": (
                round(sum(row["target_2_touched_before_exit"] for row in managed_trades)
                      / len(managed_trades), 6) if managed_trades else None),
            "win_rate": (round(sum(value > 0 for value in realized) / len(realized), 6)
                         if realized else None),
            "average_realized_return_percent": (
                round(float(np.mean(realized)), 6) if realized else None),
            "median_realized_return_percent": (
                round(float(np.median(realized)), 6) if realized else None),
            "profit_factor": (round(gains / losses, 6) if losses > 0 else None),
            "average_holding_bars": (
                round(float(np.mean([row["holding_bars"] for row in managed_trades])), 6)
                if managed_trades else None),
            "maximum_drawdown_percent": _maximum_drawdown(realized),
            "expectancy_percent": (
                round(float(np.mean(realized)), 6) if realized else None),
            "average_r_multiple": (
                round(float(np.mean(r_multiples)), 6) if r_multiples else None),
            "median_r_multiple": (
                round(float(np.median(r_multiples)), 6) if r_multiples else None),
            "worst_mae_to_exit_percent": (
                round(min(row["mae_to_exit_percent"] for row in managed_trades), 6)
                if managed_trades else None),
            "best_mfe_to_exit_percent": (
                round(max(row["mfe_to_exit_percent"] for row in managed_trades), 6)
                if managed_trades else None),
            "first_touch_counts": first_touch_counts,
        },
        "by_depth": {
            label: {
                "count": sum(row["depth_classification"] == depth for row in confirmed),
                "managed_trade_count": sum(
                    row.get("depth_classification") == depth for row in managed_trades),
                "average_managed_return_percent": (
                    round(float(np.mean([
                        row["realized_return_percent"] for row in managed_trades
                        if row.get("depth_classification") == depth
                    ])), 6)
                    if any(row.get("depth_classification") == depth
                           for row in managed_trades) else None),
            }
            for label, depth in (("HEALTHY_PULLBACK", "HEALTHY"),
                                 ("DEEP_PULLBACK", "DEEP"))
        },
    })
    return summary


def evaluate_pullback_history(
    histories: Mapping[str, pd.DataFrame],
    *,
    config: PullbackResearchConfig = DEFAULT_PULLBACK_RESEARCH_CONFIG,
) -> PullbackResearchReport:
    """Walk all symbols cutoff-by-cutoff without ever exposing a future bar to a signal."""
    observations = []
    unmanaged_returns = []
    managed_trades = []
    sources = []
    all_dates = []
    max_horizon = max(config.research_horizons)
    for symbol in sorted(str(item).upper() for item in histories):
        original = histories[symbol]
        if original is None or getattr(original, "empty", True):
            continue
        frame = original.copy().sort_index()
        frame.attrs = dict(getattr(original, "attrs", {}))
        provider = str(frame.attrs.get("market_data", {}).get("provider", "UNKNOWN"))
        sources.append((symbol, provider))
        if provider.lower() == "yahoo":
            continue
        prepared = _prepared_series(frame)
        volume_safe = bool(frame.attrs.get("market_data", {}).get(
            "volume_safe_for_lookback", True))
        start = config.minimum_history_bars - 1
        stop = len(frame) - max_horizon - 1
        analysis_window = max(
            config.minimum_history_bars,
            config.structure_lookback_bars
            + config.maximum_impulse_lookback_bars
            + 2 * config.pivot_radius
            + config.slope_lookback_bars,
        )
        for position in range(start, max(start, stop + 1)):
            window_start = max(0, position + 1 - analysis_window)
            frozen = frame.iloc[window_start:position + 1].copy()
            frozen.attrs = dict(frame.attrs)
            cutoff = pd.Timestamp(frozen.index[-1]).date().isoformat()
            scenario = evaluate_pullback_scenario(
                frozen,
                ema20=prepared["ema20"].iloc[window_start:position + 1],
                ema50=prepared["ema50"].iloc[window_start:position + 1],
                atr14=prepared["atr14"].iloc[window_start:position + 1],
                volume_safe=volume_safe,
                data_cutoff=cutoff,
                config=config,
                normalized_completed_frame=True,
            )
            breakout = _breakout_ready_at(frame, prepared, position, volume_safe)
            confirmed = scenario.state == PullbackState.CONFIRMED_PULLBACK_ENTRY
            row = {
                "symbol": symbol,
                "signal_date": cutoff,
                "state": scenario.state.value,
                "depth_classification": scenario.depth_classification,
                "pullback_confirmed": confirmed,
                "breakout_confirmed": breakout,
                "execution_delay_bars": 1,
                "transaction_cost_bps": config.transaction_cost_bps,
                "prior_trend_status": scenario.prior_trend_status,
                "swing_high": scenario.swing_high,
                "swing_high_date": scenario.swing_high_date,
                "swing_high_confirmation_date": scenario.swing_high_confirmation_date,
                "impulse_low": scenario.impulse_low,
                "impulse_duration_bars": scenario.impulse_duration_bars,
                "impulse_strength_atr": scenario.impulse_strength_atr,
                "ema20_slope_percent_per_bar": scenario.ema20_slope_percent_per_bar,
                "ema50_slope_percent_per_bar": scenario.ema50_slope_percent_per_bar,
                "pullback_percent": scenario.pullback_percent,
                "pullback_atr": scenario.pullback_atr,
                "impulse_retracement_percent": scenario.impulse_retracement_percent,
                "support_zone_low": scenario.support_zone_low,
                "support_zone_high": scenario.support_zone_high,
                "support_confluence_count": len(scenario.support_confluence),
                "support_reached": scenario.support_reached,
                "volume_behaviour": scenario.volume_behaviour,
                "correction_to_impulse_volume_ratio": (
                    scenario.correction_to_impulse_volume_ratio),
                "confirmation_status": scenario.confirmation_status,
                "confirmation_reasons": scenario.confirmation_reasons,
                "entry_trigger": scenario.entry_trigger,
                "stop_loss": scenario.stop_loss,
                "minor_pivot_resistance": scenario.minor_pivot_resistance,
                "meaningful_structural_target": scenario.meaningful_structural_target,
                "broader_structural_target": scenario.broader_structural_target,
                "reward_risk": scenario.reward_risk,
                "reward_risk_meaningful_target": scenario.reward_risk_meaningful_target,
                "reward_risk_broader_target": scenario.reward_risk_broader_target,
                "gate_results": scenario.gate_results,
                "primary_rejection_reason": scenario.primary_rejection_reason,
                "rejection_reasons": scenario.rejection_reasons,
            }
            if confirmed:
                unmanaged_returns.extend(_unmanaged_forward_rows(
                    frame, position, scenario, symbol, config.research_horizons))
                managed = simulate_managed_pullback_trade(
                    frame,
                    signal_position=position,
                    scenario=scenario,
                    symbol=symbol,
                    maximum_holding_bars=max_horizon,
                    transaction_cost_bps=config.transaction_cost_bps,
                )
                if managed is not None:
                    managed["depth_classification"] = scenario.depth_classification
                    managed_trades.append(managed)
            observations.append(row)
            all_dates.append(cutoff)

    configuration = asdict(config)
    identity = json.dumps({
        "universe": sorted(str(symbol).upper() for symbol in histories),
        "dates": (min(all_dates) if all_dates else None,
                  max(all_dates) if all_dates else None),
        "configuration": configuration,
        "evaluator": RESEARCH_EVALUATOR_VERSION,
    }, sort_keys=True, default=str, separators=(",", ":"))
    report_id = "pullback-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    gate_rates, rejection_counts = _gate_diagnostics(observations)
    return PullbackResearchReport(
        report_id=report_id,
        evaluator_version=RESEARCH_EVALUATOR_VERSION,
        pullback_engine_version=PULLBACK_ENGINE_VERSION,
        universe_snapshot=tuple(sorted(str(symbol).upper() for symbol in histories)),
        start_date=min(all_dates) if all_dates else None,
        end_date=max(all_dates) if all_dates else None,
        data_sources=tuple(sources),
        configuration=configuration,
        summary=_aggregate(
            observations, config.research_horizons, unmanaged_returns, managed_trades),
        observations=tuple(observations),
        unmanaged_forward_returns=tuple(unmanaged_returns),
        managed_trades=tuple(managed_trades),
        gate_pass_rates=gate_rates,
        rejection_counts=rejection_counts,
    )


def save_pullback_research_report(
    report: PullbackResearchReport,
    output_dir: str | Path = DEFAULT_REPORT_DIR,
) -> tuple[Path, Path]:
    """Write a reproducible summary and observation ledger under the audit directory."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / f"{report.report_id}.json"
    csv_path = target / f"{report.report_id}_observations.csv"
    json_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame(report.observations).to_csv(csv_path, index=False, encoding="utf-8")
    return json_path, csv_path


__all__ = [
    "PullbackResearchReport", "evaluate_pullback_history",
    "save_pullback_research_report", "RESEARCH_EVALUATOR_VERSION",
]
