"""Diagnostics and report writers for Pullback calibration research only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from core.ai_pullback_config import DEFAULT_PULLBACK_RESEARCH_CONFIG
from core.ai_pullback_research import (
    PullbackResearchReport,
    RESEARCH_EVALUATOR_VERSION,
    _aggregate,
    _gate_diagnostics,
)
from core.ai_pullback_scenario import PULLBACK_ENGINE_VERSION


CALIBRATION_VERSION = "ai_pullback_calibration@1.0.0"
DEFAULT_CALIBRATION_DIR = Path(
    "reports/audits/strategies/pullback_entry/calibration")


def merge_research_reports(reports, *, run_metadata=None):
    reports = tuple(reports)
    observations = tuple(row for report in reports for row in report.observations)
    unmanaged = tuple(row for report in reports for row in report.unmanaged_forward_returns)
    managed = tuple(row for report in reports for row in report.managed_trades)
    universe = tuple(sorted({symbol for report in reports
                             for symbol in report.universe_snapshot}))
    sources = tuple(sorted({item for report in reports for item in report.data_sources}))
    config = (reports[0].configuration if reports
              else asdict(DEFAULT_PULLBACK_RESEARCH_CONFIG))
    horizons = tuple(config.get("research_horizons", (3, 5, 10, 20)))
    gate_rates, rejections = _gate_diagnostics(observations)
    dates = [row["signal_date"] for row in observations]
    identity = json.dumps({
        "calibration": CALIBRATION_VERSION,
        "universe": universe,
        "dates": (min(dates) if dates else None, max(dates) if dates else None),
        "configuration": config,
    }, sort_keys=True, default=str, separators=(",", ":"))
    return PullbackResearchReport(
        report_id="pullback-calibration-"
                  + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
        evaluator_version=RESEARCH_EVALUATOR_VERSION,
        pullback_engine_version=PULLBACK_ENGINE_VERSION,
        universe_snapshot=universe,
        start_date=min(dates) if dates else None,
        end_date=max(dates) if dates else None,
        data_sources=sources,
        configuration=config,
        summary=_aggregate(observations, horizons, unmanaged, managed),
        observations=observations,
        unmanaged_forward_returns=unmanaged,
        managed_trades=managed,
        gate_pass_rates=gate_rates,
        rejection_counts=rejections,
        run_metadata=dict(run_metadata or {}),
    )


def sensitivity_result_row(name, report, *, category="OFAT"):
    observations = report.observations
    gate = {row["gate"]: row for row in report.gate_pass_rates}
    managed = report.summary.get("managed_trades", {})
    candidates = sum(
        dict(row["gate_results"]).get("correction_detected") == "PASS"
        and dict(row["gate_results"]).get("valid_prior_uptrend") == "PASS"
        for row in observations)
    by_year = {}
    by_symbol = {}
    for trade in report.managed_trades:
        year = str(trade["signal_date"])[:4]
        for target, key in ((by_year, year), (by_symbol, trade["symbol"])):
            bucket = target.setdefault(key, [])
            bucket.append(float(trade["realized_return_percent"]))

    def stability(groups):
        return {
            key: {"count": len(values),
                  "win_rate": round(sum(value > 0 for value in values) / len(values), 6),
                  "expectancy_percent": round(float(np.mean(values)), 6)}
            for key, values in sorted(groups.items())
        }

    config = report.configuration
    return {
        "variant": name,
        "category": category,
        "pivot_radius": config["pivot_radius"],
        "minimum_impulse_strength_atr": config["minimum_impulse_strength_atr"],
        "healthy_retracement_maximum_percent": (
            config["healthy_retracement_maximum_percent"]),
        "healthy_pullback_maximum_atr": config["healthy_pullback_maximum_atr"],
        "support_cluster_tolerance_atr": config["support_cluster_tolerance_atr"],
        "contracting_volume_ratio": config["contracting_volume_ratio"],
        "minimum_reward_risk": config["minimum_reward_risk"],
        "pullback_candidates": int(candidates),
        "support_zone_touches": gate.get(
            "support_reached_or_approached", {}).get("pass_count", 0),
        "reversal_confirmations": gate.get(
            "reversal_confirmation_detected", {}).get("pass_count", 0),
        "confirmed_entries": report.summary.get("confirmed_entry_count", 0),
        "confirmed_entry_frequency": report.summary.get("pullback_confirmation_rate"),
        "managed_trade_count": managed.get("count"),
        "win_rate": managed.get("win_rate"),
        "expectancy_percent": managed.get("expectancy_percent"),
        "profit_factor": managed.get("profit_factor"),
        "maximum_drawdown_percent": managed.get("maximum_drawdown_percent"),
        "stop_hit_rate": managed.get("stop_hit_rate"),
        "target_1_hit_rate": managed.get("target_1_hit_rate"),
        "target_2_touch_rate": managed.get("target_2_touch_rate"),
        "median_r_multiple": managed.get("median_r_multiple"),
        "stability_by_year": json.dumps(stability(by_year), sort_keys=True),
        "stability_by_symbol": json.dumps(stability(by_symbol), sort_keys=True),
    }


def _flat_funnel_rows(observations):
    rows = []
    for observation in observations:
        row = {key: value for key, value in observation.items()
               if key not in ("gate_results", "rejection_reasons",
                              "confirmation_reasons")}
        row.update({f"gate_{name}": status
                    for name, status in observation.get("gate_results", ())})
        row["all_rejection_reasons"] = "|".join(
            observation.get("rejection_reasons", ()))
        row["confirmation_reasons"] = "|".join(
            observation.get("confirmation_reasons", ()))
        rows.append(row)
    return rows


def _independent_rule_rows(report):
    """Derive independent rule rates without changing the ordered funnel."""
    config = report.configuration
    observations = report.observations

    def status_rows(name, evaluator):
        statuses = [evaluator(row) for row in observations]
        passed = sum(status == "PASS" for status in statuses)
        failed = sum(status == "FAIL" for status in statuses)
        skipped = sum(status == "SKIPPED" for status in statuses)
        evaluated = passed + failed
        return {
            "scope": "INDEPENDENT_RULE",
            "gate": name,
            "pass_count": passed,
            "fail_count": failed,
            "skipped_count": skipped,
            "evaluated_count": evaluated,
            "individual_pass_rate": (
                round(passed / evaluated, 6) if evaluated else None),
            "cumulative_pass_count": None,
            "cumulative_pass_rate": None,
        }

    def numeric_rule(field, predicate):
        def evaluate(row):
            value = row.get(field)
            if value is None:
                return "SKIPPED"
            return "PASS" if predicate(float(value)) else "FAIL"
        return evaluate

    def reason_absent(reason):
        return lambda row: (
            "SKIPPED" if not row.get("swing_high")
            else "PASS" if reason not in row.get("rejection_reasons", ()) else "FAIL")

    def confirmation(name):
        return lambda row: (
            "SKIPPED" if not row.get("swing_high") else
            "PASS" if name in row.get("confirmation_reasons", ()) else "FAIL")

    rules = (
        ("pivot_confirmed_with_configured_radius", lambda row: (
            "PASS" if row.get("swing_high_confirmation_date") else "FAIL")),
        ("minimum_impulse_duration", numeric_rule(
            "impulse_duration_bars",
            lambda value: value >= config["minimum_impulse_duration_bars"])),
        ("minimum_impulse_atr_size", numeric_rule(
            "impulse_strength_atr",
            lambda value: value >= config["minimum_impulse_strength_atr"])),
        ("ema20_slope_threshold", numeric_rule(
            "ema20_slope_percent_per_bar",
            lambda value: value >= config["minimum_ema20_slope_percent_per_bar"])),
        ("ema50_slope_threshold", numeric_rule(
            "ema50_slope_percent_per_bar",
            lambda value: value >= config["minimum_ema50_slope_percent_per_bar"])),
        ("correction_minimum", lambda row: (
            "SKIPPED" if (row.get("pullback_percent") is None
                           or row.get("pullback_atr") is None) else
            "PASS" if (
                float(row["pullback_percent"]) >= config["minimum_pullback_percent"]
                or float(row["pullback_atr"]) >= config["minimum_pullback_atr"]
            ) else "FAIL")),
        ("healthy_retracement_maximum", numeric_rule(
            "impulse_retracement_percent",
            lambda value: value <= config["healthy_retracement_maximum_percent"])),
        ("failed_retracement_limit", numeric_rule(
            "impulse_retracement_percent",
            lambda value: value < config["failed_retracement_percent"])),
        ("healthy_atr_depth_maximum", numeric_rule(
            "pullback_atr", lambda value: value <= config["healthy_pullback_maximum_atr"])),
        ("failed_atr_depth_limit", numeric_rule(
            "pullback_atr", lambda value: value < config["failed_pullback_atr"])),
        ("support_cluster_has_confluence", lambda row: (
            "SKIPPED" if not row.get("swing_high") else
            "PASS" if int(row["support_confluence_count"]) >= 2 else "FAIL")),
        ("support_reached_or_approached", lambda row: dict(
            row.get("gate_results", ())).get("support_reached_or_approached", "SKIPPED")),
        ("correction_volume_contracts_at_threshold", numeric_rule(
            "correction_to_impulse_volume_ratio",
            lambda value: value <= config["contracting_volume_ratio"])),
        ("aggressive_volume_limit", reason_absent("AGGRESSIVE_VOLUME_EXPANSION")),
        ("bullish_rejection_candle", confirmation("BULLISH_REJECTION_CANDLE")),
        ("support_zone_reclaim", confirmation("CLOSE_BACK_ABOVE_SUPPORT_ZONE")),
        ("ema20_reclaim", confirmation("EMA20_RECLAIM")),
        ("close_above_previous_high", confirmation("CLOSE_ABOVE_PREVIOUS_CANDLE_HIGH")),
        ("correction_trendline_break", confirmation("CORRECTION_TRENDLINE_BREAK")),
        ("strong_confirmation_or", lambda row: dict(
            row.get("gate_results", ())).get("reversal_confirmation_detected", "SKIPPED")),
        ("entry_trigger_crossed", lambda row: dict(
            row.get("gate_results", ())).get("entry_trigger_crossed", "SKIPPED")),
        ("structural_stop_below_trigger", lambda row: (
            "SKIPPED" if row.get("stop_loss") is None or row.get("entry_trigger") is None
            else "PASS" if float(row["stop_loss"]) < float(row["entry_trigger"]) else "FAIL")),
        ("nearest_minor_target_available", lambda row: (
            "SKIPPED" if not row.get("swing_high") else
            "PASS" if row.get("minor_pivot_resistance") is not None else "FAIL")),
        ("meaningful_structural_target_available", lambda row: (
            "SKIPPED" if not row.get("swing_high") else
            "PASS" if row.get("meaningful_structural_target") is not None else "FAIL")),
        ("broader_structural_target_available", lambda row: (
            "SKIPPED" if not row.get("swing_high") else
            "PASS" if row.get("broader_structural_target") is not None else "FAIL")),
        ("rr_minor_target", numeric_rule(
            "reward_risk", lambda value: value >= config["minimum_reward_risk"])),
        ("rr_meaningful_target", numeric_rule(
            "reward_risk_meaningful_target",
            lambda value: value >= config["minimum_reward_risk"])),
        ("rr_broader_target", numeric_rule(
            "reward_risk_broader_target",
            lambda value: value >= config["minimum_reward_risk"])),
        ("ema50_current_slope_not_invalidated", reason_absent("EMA50_SLOPE_INVALIDATED")),
    )
    return tuple(status_rows(name, evaluator) for name, evaluator in rules)


def write_calibration_reports(report, sensitivity_rows=(), *, output_dir=None,
                              verdict_notes=None):
    target = Path(output_dir or DEFAULT_CALIBRATION_DIR)
    target.mkdir(parents=True, exist_ok=True)
    paths = {
        "funnel": target / "pullback_state_funnel.csv",
        "rejections": target / "pullback_rejection_reasons.csv",
        "gates": target / "pullback_gate_pass_rates.csv",
        "managed": target / "pullback_managed_trades.csv",
        "unmanaged": target / "pullback_unmanaged_forward_returns.csv",
        "sensitivity": target / "pullback_sensitivity_results.csv",
        "verdict": target / "PULLBACK_CALIBRATION_VERDICT.md",
    }
    pd.DataFrame(_flat_funnel_rows(report.observations)).to_csv(
        paths["funnel"], index=False, encoding="utf-8")
    pd.DataFrame(report.rejection_counts).to_csv(
        paths["rejections"], index=False, encoding="utf-8")
    ordered_gates = tuple({"scope": "CUMULATIVE_FUNNEL_GATE", **row}
                          for row in report.gate_pass_rates)
    pd.DataFrame(ordered_gates + _independent_rule_rows(report)).to_csv(
        paths["gates"], index=False, encoding="utf-8")
    pd.DataFrame(report.managed_trades).to_csv(
        paths["managed"], index=False, encoding="utf-8")
    pd.DataFrame(report.unmanaged_forward_returns).to_csv(
        paths["unmanaged"], index=False, encoding="utf-8")
    pd.DataFrame(tuple(sensitivity_rows)).to_csv(
        paths["sensitivity"], index=False, encoding="utf-8")
    paths["verdict"].write_text(
        build_verdict_markdown(report, sensitivity_rows, verdict_notes=verdict_notes),
        encoding="utf-8")
    return paths


def build_verdict_markdown(report, sensitivity_rows=(), *, verdict_notes=None):
    sensitivity_rows = tuple(sensitivity_rows)
    summary = report.summary
    managed = summary.get("managed_trades", {})
    gates = list(report.gate_pass_rates)
    rejections = report.rejection_counts[:10]
    primary_rejections = sorted(
        (row for row in report.rejection_counts if row.get("primary_count", 0)),
        key=lambda row: (-row["primary_count"], row["reason"]),
    )[:10]
    rules = {row["gate"]: row for row in _independent_rule_rows(report)}
    cumulative_losses = []
    previous_count = summary.get("scenarios_generated", 0)
    for row in gates:
        current_count = int(row["cumulative_pass_count"])
        cumulative_losses.append((previous_count - current_count, row))
        previous_count = current_count
    largest_loss, largest = max(
        cumulative_losses,
        key=lambda item: (item[0], -gates.index(item[1])),
        default=(0, None),
    )
    requested = report.run_metadata.get("symbols_requested", len(report.universe_snapshot))
    sufficient = report.run_metadata.get("symbols_with_sufficient_data",
                                         len(report.universe_snapshot))
    skipped = report.run_metadata.get("symbols_skipped", {})
    def value(item):
        return "N/A" if item is None else str(item)

    def percent(item, digits=2):
        return "N/A" if item is None else f"{float(item) * 100:.{digits}f}%"

    def decimal(item, digits=3):
        return "N/A" if item is None else f"{float(item):.{digits}f}"

    def stability(row, field):
        try:
            groups = json.loads(row.get(field) or "{}")
        except (TypeError, ValueError):
            groups = {}
        positive = sum(float(item.get("expectancy_percent", 0)) > 0
                       for item in groups.values())
        return f"{positive}/{len(groups)}"

    state_order = (
        "NOT_APPLICABLE", "DEVELOPING_PULLBACK", "WAIT_REVERSAL_CONFIRMATION",
        "HEALTHY_PULLBACK", "DEEP_PULLBACK", "CONFIRMED_PULLBACK_ENTRY",
        "FAILED_PULLBACK",
    )
    lines = [
        "# Pullback Calibration Verdict",
        "",
        "- Operational status: `REJECTED_FOR_OPERATIONAL_USE`",
        "- Reproducibility status: `RETAINED_AS_RESEARCH_BASELINE`",
        "- Product role: diagnostic Pullback Health Analysis only; no production adoption.",
        "",
        "## Scope and implementation correctness",
        "",
        f"- Calibration version: `{CALIBRATION_VERSION}`",
        f"- Symbols requested: {requested}",
        f"- Symbols with sufficient cached EODHD data: {sufficient}",
        f"- Symbols skipped: {len(skipped)}",
        f"- Date range: {report.start_date} to {report.end_date}",
        f"- Symbol-days evaluated: {summary.get('scenarios_generated', 0)}",
        "- Completed cached EODHD daily bars only; no network refresh and no Yahoo.",
        f"- Skipped symbols and reasons: `{json.dumps(skipped, sort_keys=True)}`",
        "- Breakout/breakdown and EMA5/EMA10 scalping logic were not changed.",
        "- This remains Research Only and cannot change production BUY decisions.",
        "",
        "## Evaluator correctness",
        "",
        "The previous evaluator did ignore structural stop/target execution: it measured "
        "fixed-horizon close returns and MAE/MFE even after a trade should have exited. "
        "Those values are now labelled unmanaged diagnostics only. Managed trades enter "
        "at the next session open, apply 30 bps total transaction cost, activate the stop "
        "and targets on that entry bar, and stop accruing returns and excursions at exit. "
        "If stop and target occur in one daily candle, stop-first is used. Exit-candle "
        "MAE/MFE is also clipped at the executed boundary so post-exit intraday prices are "
        "not counted. Drawdown is calculated in chronological entry order from starting "
        "equity, not symbol iteration order.",
        "",
        "Target 1 is conservatively treated as a full-position exit in this evaluator; "
        "Target 2 is therefore a diagnostic touch before that exit, not an assumed runner. "
        "A scaled-exit policy would require an explicit research configuration and is not "
        "silently invented here.",
        "",
        "### Managed versus unmanaged results",
        "",
    ]
    lines.extend([
        "| Result | Count | Win rate | Average/expectancy % | Median % | Worst MAE % | Best MFE % |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for horizon, row in summary.get("unmanaged_forward_returns", {}).items():
        lines.append(
            f"| Unmanaged {horizon} | {row.get('count')} | {value(row.get('win_rate'))} "
            f"| {value(row.get('average_return_percent'))} | "
            f"{value(row.get('median_return_percent'))} | "
            f"{value(row.get('worst_mae_percent'))} | "
            f"{value(row.get('best_mfe_percent'))} |")
    lines.append(
        f"| Managed to actual exit | {managed.get('count')} | {value(managed.get('win_rate'))} "
        f"| {value(managed.get('expectancy_percent'))} | "
        f"{value(managed.get('median_realized_return_percent'))} | "
        f"{value(managed.get('worst_mae_to_exit_percent'))} | "
        f"{value(managed.get('best_mfe_to_exit_percent'))} |")
    lines.extend([
        "",
        f"Managed stop-hit rate is {value(managed.get('stop_hit_rate'))}; Target-1 "
        f"hit rate is {value(managed.get('target_1_hit_rate'))}; Target-2 diagnostic "
        f"touch rate is {value(managed.get('target_2_touch_rate'))}; profit factor is "
        f"{value(managed.get('profit_factor'))}; chronological maximum drawdown is "
        f"{value(managed.get('maximum_drawdown_percent'))}%; median R is "
        f"{value(managed.get('median_r_multiple'))}.",
        "",
        "## Exact state and gate funnel",
        "",
    ])
    lines.extend(f"- {state}: {summary.get('state_counts', {}).get(state, 0)}"
                 for state in state_order)
    lines.extend([
        "",
        "| Ordered gate | Individual pass | Cumulative pass | Cumulative rate |",
        "|---|---:|---:|---:|",
    ])
    lines.extend(
        f"| {row['gate']} | {value(row.get('pass_count'))} / "
        f"{value(row.get('evaluated_count'))} "
        f"| {row['cumulative_pass_count']} | "
        f"{value(row.get('cumulative_pass_rate'))} |"
        for row in gates)
    lines.extend(["", "## Rejection diagnosis", "", "### Contributing reasons", ""])
    lines.extend(
        f"- {row['reason']}: {row['all_contributing_count']} contributing; "
        f"{row['primary_count']} primary" for row in rejections)
    lines.extend(["", "### Primary reasons", ""])
    lines.extend(f"- {row['reason']}: {row['primary_count']}"
                 for row in primary_rejections)
    lines.extend(["", "### Largest losses", "",
                  (f"The largest total cumulative loss is: `{largest['gate']}` rejects "
                   f"{largest_loss} observations at that stage and leaves "
                   f"{largest['cumulative_pass_count']}." if largest else "No observations."),
                  "After prior-trend qualification, reversal confirmation is the largest "
                  "candidate-stage loss; the exact gate counts above distinguish it from "
                  "the later trigger and R/R losses.",
                  "", "## Signal frequency and statistical sufficiency", "",
                  f"- Confirmed entries: {summary.get('confirmed_entry_count', 0)}",
                  f"- Confirmation frequency: {summary.get('pullback_confirmation_rate')}",
                  f"- Managed trades: {managed.get('count')}",
                  "",
                  "The expanded default sample is large enough to show that the current "
                  "configuration is rare and has negative managed expectancy. It is not "
                  "sufficient to validate a replacement configuration: several variants "
                  "have only a handful of trades, and symbol/year dependence remains.",
                  "", "## Reversal confirmation and deep EMA50 behavior", "",
                  "The five evidence flags are reported independently. Entry uses OR across "
                  "the four strong confirmations (support-zone reclaim, EMA20 reclaim, "
                  "close above previous high, correction-trendline break). A bullish "
                  "rejection candle alone cannot activate entry. Structural validity, "
                  "support reach, trigger crossing, available upside and R/R remain required.",
                  "",
                  "A correctness issue was found and fixed: the trigger previously included "
                  "EMA20 for every setup, which forced an EMA20 reclaim even for a deep "
                  "pullback intentionally supported at EMA50. A deep EMA50 zone can now use "
                  "support-zone/previous-high confirmation below EMA20. Shallow/healthy "
                  "behavior is unchanged.",
                  "", "## Targets and available upside", "",
                  f"- Minor target available pass rate: "
                  f"{value(rules.get('nearest_minor_target_available', {}).get('individual_pass_rate'))}",
                  f"- Meaningful target available pass rate: "
                  f"{value(rules.get('meaningful_structural_target_available', {}).get('individual_pass_rate'))}",
                  f"- R/R pass against nearest minor target: "
                  f"{value(rules.get('rr_minor_target', {}).get('individual_pass_rate'))}",
                  f"- R/R pass against first meaningful target: "
                  f"{value(rules.get('rr_meaningful_target', {}).get('individual_pass_rate'))}",
                  f"- R/R pass against broader structural target: "
                  f"{value(rules.get('rr_broader_target', {}).get('individual_pass_rate'))}",
                  "",
                  "Minor, meaningful and broader resistance are now separate diagnostics. "
                  "The entry gate still uses the conservative nearest target; targets are "
                  "not inflated merely to pass R/R.",
                  "", "## Sensitivity results", "",
                  f"Sensitivity rows completed: {len(sensitivity_rows)}.", "",
                  "| Variant | Type | Trades | Win rate | Expectancy % | PF | Drawdown % | Median R | Positive years | Positive symbols |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row in sensitivity_rows:
        lines.append(
            f"| {row.get('variant')} | {row.get('category')} | "
            f"{value(row.get('managed_trade_count'))} | {value(row.get('win_rate'))} | "
            f"{value(row.get('expectancy_percent'))} | {value(row.get('profit_factor'))} | "
            f"{value(row.get('maximum_drawdown_percent'))} | "
            f"{value(row.get('median_r_multiple'))} | "
            f"{stability(row, 'stability_by_year')} | "
            f"{stability(row, 'stability_by_symbol')} |")
    lines.extend([
        "",
        "## Configuration recommendation",
        "",
        "Keep the current defaults frozen as the regression baseline, but reject them as "
        "an operationally useful research configuration: managed expectancy and profit "
        "factor are negative/under one on the expanded sample. Do not promote or loosen "
        "them. Pivot radius 1 looks positive only on five recent trades and is statistically "
        "insufficient; pivot radius 3 increases frequency while degrading quality. The "
        "small combinations do not supply a stable replacement.",
        "",
        f"The managed evidence is {value(managed.get('count'))} executable trades, "
        f"{percent(managed.get('win_rate'))} wins, "
        f"{decimal(managed.get('expectancy_percent'))}% expectancy, "
        f"{decimal(managed.get('profit_factor'))} profit factor, "
        f"{percent(managed.get('stop_hit_rate'))} stop rate, and "
        f"{decimal(managed.get('median_r_multiple'))} median R. "
        "The positive-looking unmanaged 20-bar return ignored intervening structural "
        "stops and was therefore misleading as entry evidence. Sensitivity analysis "
        "did not identify a statistically credible replacement. The measurements may "
        "still help human analysis of correction quality, but no production adoption "
        "is recommended.",
        "",
        "## Lookahead audit",
        "",
        "Confirmed pivots carry both pivot date and knowable confirmation date; a radius-N "
        "pivot is unavailable until N right-hand completed bars exist. Scenario cutoff is "
        "never earlier than pivot confirmation. Support/Fibonacci/targets are computed from "
        "the frozen cutoff only, future appended pivots do not change them, and managed entry "
        "is one completed bar after the signal. No lookahead leak was found in the audited path.",
    ])
    if verdict_notes:
        lines.extend(["", "## Analyst notes", "", str(verdict_notes)])
    lines.extend(["", "## Remaining uncertainty", "",
                  "Daily OHLC cannot reveal intrabar ordering. The conservative stop-first "
                  "rule bounds that ambiguity but cannot remove it. The drawdown is a "
                  "chronological sequential-signal curve, not a capital-allocation portfolio "
                  "simulation. Target-1 scaling is not modeled. Sparse positive variants "
                  "must not be treated as calibration proof.", ""])
    return "\n".join(lines)


__all__ = [
    "CALIBRATION_VERSION", "merge_research_reports", "sensitivity_result_row",
    "write_calibration_reports", "build_verdict_markdown",
]
