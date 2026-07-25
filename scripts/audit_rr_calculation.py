"""Independent, read-only mathematical audit of Swing/Daily RR geometry.

The production strategy is never mutated.  Every value is reconstructed from
archived raw OHLCV, compared with the current engine and the sealed RC1/Phase-8
implementation, and accompanied by a chart for visual inspection.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from indicators.technical import calculate_indicators
from services.dataset_archive import load_archived_frames
from strategy.entry import entry_signal
from strategy.support import support_resistance


CURRENT_RUN = PROJECT / "reports" / "RUN_20260719_191032"
PHASE8_RUN = PROJECT / "reports" / "RUN_20260714_125023"
TRACE_PATH = PROJECT / "reports" / "buy_signal_full_trace.csv"
OUTPUT_CSV = PROJECT / "reports" / "audits" / "rr_audit.csv"
SUMMARY_JSON = PROJECT / "reports" / "rr_audit_summary.json"
CHART_DIR = PROJECT / "reports" / "rr_audit_charts"
CHART_INDEX = CHART_DIR / "INDEX.md"
RC1 = PROJECT / "EGX_AI_Trader_RC1"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load archived module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PHASE8_ENTRY = _load_module("phase8_entry", RC1 / "strategy" / "entry.py")
PHASE8_SUPPORT = _load_module("phase8_support", RC1 / "strategy" / "support.py")
PHASE8_TECHNICAL = _load_module(
    "phase8_technical", RC1 / "indicators" / "technical.py"
)


def _independent_atr(raw: pd.DataFrame, window: int = 14) -> pd.Series:
    """Reimplement Wilder ATR without calling ta or project indicator code."""

    high = pd.to_numeric(raw["High"], errors="raise").astype(float)
    low = pd.to_numeric(raw["Low"], errors="raise").astype(float)
    close = pd.to_numeric(raw["Close"], errors="raise").astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    values = np.zeros(len(raw), dtype=float)
    if len(raw) >= window:
        values[window - 1] = float(true_range.iloc[:window].mean())
        for i in range(window, len(raw)):
            values[i] = (
                values[i - 1] * (window - 1) + float(true_range.iloc[i])
            ) / window
    return pd.Series(values, index=raw.index, name="ATR_INDEPENDENT")


def _independent_ema(close: pd.Series, window: int) -> pd.Series:
    """Reimplement the EMA convention used by ta.EMAIndicator."""

    return pd.to_numeric(close, errors="raise").astype(float).ewm(
        span=window,
        min_periods=window,
        adjust=False,
    ).mean()


def _independent_geometry(raw: pd.DataFrame) -> dict:
    """Calculate every RR input directly from raw OHLCV."""

    i = len(raw) - 1
    if i < 19:
        raise ValueError("RR audit requires at least 20 bars")
    start = max(0, i - 19)
    close = float(raw["Close"].iloc[i])
    atr = float(_independent_atr(raw).iloc[i])
    support_raw = float(raw["Low"].iloc[start : i + 1].min())
    # Entry targets deliberately use only prior bars, excluding today's High.
    target_resistance_raw = float(raw["High"].iloc[start:i].max())
    # The separately displayed/quality resistance includes today's High.
    displayed_resistance_raw = float(raw["High"].iloc[start : i + 1].max())

    buy_low = round(max(support_raw, close - atr * 0.30), 2)
    entry = round(close, 2)
    stop = round(support_raw - atr * 0.30, 2)
    risk = entry - stop
    if risk <= 0:
        target1 = entry
        target2 = entry
        reward = 0.0
        rr = 0.0
    else:
        target1 = round(target_resistance_raw, 2)
        target2 = round(target_resistance_raw + atr * 2, 2)
        reward = target2 - entry
        rr = round(reward / risk, 2)

    raw_stop = support_raw - atr * 0.30
    raw_target2 = target_resistance_raw + atr * 2
    raw_risk = close - raw_stop
    raw_reward = raw_target2 - close
    raw_rr = raw_reward / raw_risk if raw_risk > 0 else 0.0
    return {
        "window_start": raw.index[start],
        "date": raw.index[i],
        "close": close,
        "atr": atr,
        "support_raw": support_raw,
        "support_display": round(support_raw, 2),
        "target_resistance_raw": target_resistance_raw,
        "displayed_resistance_raw": displayed_resistance_raw,
        "displayed_resistance": round(displayed_resistance_raw, 2),
        "buy_low": buy_low,
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "risk": risk,
        "reward": reward,
        "rr": rr,
        "raw_stop": raw_stop,
        "raw_target2": raw_target2,
        "raw_risk": raw_risk,
        "raw_reward": raw_reward,
        "raw_rr_before_level_rounding": raw_rr,
    }


def _is_close(left, right, tolerance: float = 1e-9) -> bool:
    try:
        return bool(np.isclose(float(left), float(right), rtol=0, atol=tolerance))
    except (TypeError, ValueError):
        return False


def _engine_values(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    frame = calculate_indicators(raw.copy())
    return frame, entry_signal(frame, len(frame) - 1), support_resistance(
        frame, len(frame) - 1
    )


def _phase8_values(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    frame = PHASE8_TECHNICAL.calculate_indicators(raw.copy())
    return frame, PHASE8_ENTRY.entry_signal(frame, len(frame) - 1), (
        PHASE8_SUPPORT.support_resistance(frame, len(frame) - 1)
    )


def _plot_chart(
    ticker: str,
    frame: pd.DataFrame,
    geometry: dict,
    rubix_last,
    output: Path,
) -> None:
    tail = frame.tail(80)
    fig, ax = plt.subplots(figsize=(14, 8))
    ax.plot(tail.index, tail["Close"], color="#111827", linewidth=2.0, label="Close")
    ax.plot(tail.index, tail["EMA20"], color="#2563eb", linewidth=1.4, label="EMA20")
    ax.plot(tail.index, tail["EMA50"], color="#f59e0b", linewidth=1.4, label="EMA50")
    ax.plot(tail.index, tail["EMA200"], color="#7c3aed", linewidth=1.4, label="EMA200")

    levels = [
        ("Entry", geometry["entry"], "#111827", "-"),
        ("Stop", geometry["stop"], "#dc2626", "-"),
        ("Target1 / prior resistance", geometry["target1"], "#059669", "--"),
        ("Target2", geometry["target2"], "#16a34a", "-"),
        ("Support", geometry["support_display"], "#b91c1c", ":"),
        (
            "Displayed resistance (includes current bar)",
            geometry["displayed_resistance"],
            "#0891b2",
            ":",
        ),
    ]
    for label, value, color, style in levels:
        ax.axhline(value, color=color, linestyle=style, linewidth=1.2, label=f"{label}: {value:.2f}")

    right = tail.index[-1]
    ax.fill_between(
        tail.index,
        geometry["stop"],
        geometry["entry"],
        color="#ef4444",
        alpha=0.10,
        label=f"Risk distance: {geometry['risk']:.2f}",
    )
    reward_low, reward_high = sorted((geometry["entry"], geometry["target2"]))
    ax.fill_between(
        tail.index,
        reward_low,
        reward_high,
        color="#22c55e" if geometry["reward"] >= 0 else "#f97316",
        alpha=0.10,
        label=f"Reward distance: {geometry['reward']:.2f}",
    )
    if pd.notna(rubix_last):
        ax.scatter(
            [right],
            [float(rubix_last)],
            marker="x",
            s=80,
            color="#6b7280",
            label=f"Rubix Last (not used): {float(rubix_last):.2f}",
            zorder=5,
        )

    ax.set_title(
        f"{ticker} — RR={geometry['rr']:.2f} = "
        f"({geometry['target2']:.2f} - {geometry['entry']:.2f}) / "
        f"({geometry['entry']:.2f} - {geometry['stop']:.2f})\n"
        f"Reward={geometry['reward']:.2f}, Risk={geometry['risk']:.2f}; "
        "completed daily OHLCV only",
        fontsize=13,
    )
    ax.set_ylabel("EGP")
    ax.grid(alpha=0.18)
    handles, labels = ax.get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    ax.legend(unique.values(), unique.keys(), loc="best", fontsize=8, ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output, dpi=135)
    plt.close(fig)


def main() -> None:
    trace = pd.read_csv(TRACE_PATH)
    # These are the symbols that actually reached Risk in the waterfall and
    # failed there; MarketRegime early exits with Risk=N/A are excluded.
    risk_symbols = trace.loc[trace["failed_gate"] == "Risk", "ticker"].tolist()
    live = trace.set_index("ticker")
    current_frames, current_manifest = load_archived_frames(CURRENT_RUN)
    phase8_frames, phase8_manifest = load_archived_frames(PHASE8_RUN)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    chart_links: list[str] = [
        "# RR Audit Charts",
        "",
        "Every chart uses completed daily OHLCV. Rubix Last is marked only as an informational point and is not used in the geometry.",
        "",
    ]
    for ticker in risk_symbols:
        raw = current_frames[ticker]
        frame, engine_entry, engine_support = _engine_values(raw)
        phase8_frame_same, phase8_entry_same, phase8_support_same = _phase8_values(raw)
        independent = _independent_geometry(raw)
        archived_raw = phase8_frames.get(ticker)
        if archived_raw is not None and len(archived_raw) >= 20:
            _, phase8_entry_archived, phase8_support_archived = _phase8_values(archived_raw)
            phase8_archived_geometry = _independent_geometry(archived_raw)
            phase8_archived_date = phase8_archived_geometry["date"]
        else:
            phase8_entry_archived = {}
            phase8_support_archived = {}
            phase8_archived_geometry = {}
            phase8_archived_date = None

        independent_ema20 = float(_independent_ema(raw["Close"], 20).iloc[-1])
        independent_ema50 = float(_independent_ema(raw["Close"], 50).iloc[-1])
        independent_ema200 = float(_independent_ema(raw["Close"], 200).iloc[-1])
        rubix_last = live.at[ticker, "current_live_quote"]
        safe_name = ticker.replace(".", "_").replace("/", "_")
        chart = CHART_DIR / f"{safe_name}.png"
        _plot_chart(ticker, frame, independent, rubix_last, chart)
        chart_links.extend([f"## {ticker}", "", f"![{ticker}]({chart.name})", ""])

        support_match = _is_close(engine_support["support"], independent["support_display"])
        displayed_resistance_match = _is_close(
            engine_support["resistance"], independent["displayed_resistance"]
        )
        atr_match = _is_close(frame["ATR"].iloc[-1], independent["atr"], 1e-10)
        entry_match = _is_close(engine_entry["BuyHigh"], independent["entry"])
        stop_match = _is_close(engine_entry["StopLoss"], independent["stop"])
        target1_match = _is_close(engine_entry["Target1"], independent["target1"])
        target2_match = _is_close(engine_entry["Target2"], independent["target2"])
        rr_match = _is_close(engine_entry["RR"], independent["rr"])
        phase8_same_candles_match = all(
            _is_close(engine_entry[key], phase8_entry_same[key])
            for key in ("BuyLow", "BuyHigh", "StopLoss", "Target1", "Target2", "RR")
        ) and all(
            _is_close(engine_support[key], phase8_support_same[key])
            for key in ("support", "resistance")
        ) and _is_close(frame["ATR"].iloc[-1], phase8_frame_same["ATR"].iloc[-1], 1e-12)

        rows.append({
            "ticker": ticker,
            "date": str(pd.Timestamp(independent["date"]).date()),
            "window_start": str(pd.Timestamp(independent["window_start"]).date()),
            "current_close": independent["close"],
            "rubix_last_informational_only": rubix_last,
            "entry": engine_entry["BuyHigh"],
            "buy_low": engine_entry["BuyLow"],
            "stop": engine_entry["StopLoss"],
            "target1": engine_entry["Target1"],
            "target2": engine_entry["Target2"],
            "support_displayed": engine_support["support"],
            "support_raw_used_by_entry": independent["support_raw"],
            "resistance_displayed_includes_current_bar": engine_support["resistance"],
            "target_resistance_raw_excludes_current_bar": independent["target_resistance_raw"],
            "atr_engine": float(frame["ATR"].iloc[-1]),
            "rr_engine": engine_entry["RR"],
            "risk_distance": independent["risk"],
            "reward_distance": independent["reward"],
            "risk_distance_percent_of_entry": (
                independent["risk"] / independent["entry"] * 100
                if independent["entry"] else None
            ),
            "reward_distance_percent_of_entry": (
                independent["reward"] / independent["entry"] * 100
                if independent["entry"] else None
            ),
            "support_below_close_percent": (
                (independent["close"] - independent["support_raw"])
                / independent["close"] * 100
                if independent["close"] else None
            ),
            "prior_resistance_vs_entry_percent": (
                (independent["target_resistance_raw"] - independent["entry"])
                / independent["entry"] * 100
                if independent["entry"] else None
            ),
            "independent_entry": independent["entry"],
            "independent_stop": independent["stop"],
            "independent_target1": independent["target1"],
            "independent_target2": independent["target2"],
            "independent_support": independent["support_display"],
            "independent_displayed_resistance": independent["displayed_resistance"],
            "independent_atr": independent["atr"],
            "independent_rr": independent["rr"],
            "rr_before_intermediate_level_rounding": independent["raw_rr_before_level_rounding"],
            "rounding_changes_risk_gate": bool(
                (independent["raw_rr_before_level_rounding"] >= 1.5)
                != (independent["rr"] >= 1.5)
            ),
            "ema20_engine": float(frame["EMA20"].iloc[-1]),
            "ema20_independent": independent_ema20,
            "ema50_engine": float(frame["EMA50"].iloc[-1]),
            "ema50_independent": independent_ema50,
            "ema200_engine": float(frame["EMA200"].iloc[-1]),
            "ema200_independent": independent_ema200,
            "ema20_verified": _is_close(frame["EMA20"].iloc[-1], independent_ema20, 1e-10),
            "ema50_verified": _is_close(frame["EMA50"].iloc[-1], independent_ema50, 1e-10),
            "ema200_verified": _is_close(frame["EMA200"].iloc[-1], independent_ema200, 1e-10),
            "support_verified": support_match,
            "resistance_verified": displayed_resistance_match,
            "atr_verified": atr_match,
            "entry_verified": entry_match,
            "stop_verified": stop_match,
            "target1_verified": target1_match,
            "target2_verified": target2_match,
            "rr_verified": rr_match,
            "phase8_same_candles_rr": phase8_entry_same["RR"],
            "phase8_same_candles_all_values_match": phase8_same_candles_match,
            "phase8_archived_dataset_date": (
                str(pd.Timestamp(phase8_archived_date).date())
                if phase8_archived_date is not None else None
            ),
            "phase8_archived_dataset_rr": phase8_entry_archived.get("RR"),
            "phase8_archived_dataset_close": phase8_archived_geometry.get("close"),
            "phase8_archived_dataset_support": phase8_support_archived.get("support"),
            "phase8_archived_dataset_resistance": phase8_support_archived.get("resistance"),
            "phase8_archived_dataset_atr": phase8_archived_geometry.get("atr"),
            "phase8_archived_dataset_entry": phase8_entry_archived.get("BuyHigh"),
            "phase8_archived_dataset_stop": phase8_entry_archived.get("StopLoss"),
            "phase8_archived_dataset_target1": phase8_entry_archived.get("Target1"),
            "phase8_archived_dataset_target2": phase8_entry_archived.get("Target2"),
            "rr_formula": (
                f"({independent['target2']} - {independent['entry']}) / "
                f"({independent['entry']} - {independent['stop']}) = {independent['rr']}"
            ),
            "risk_boolean": f"1.5 <= {independent['rr']} <= 100.0 is False",
            "chart_path": str(chart.relative_to(PROJECT)).replace("\\", "/"),
        })

    result = pd.DataFrame(rows).sort_values(["rr_engine", "ticker"])
    result.to_csv(OUTPUT_CSV, index=False)
    CHART_INDEX.write_text("\n".join(chart_links), encoding="utf-8")
    verification_columns = [
        "support_verified", "resistance_verified", "atr_verified",
        "entry_verified", "stop_verified", "target1_verified",
        "target2_verified", "rr_verified", "ema20_verified",
        "ema50_verified", "ema200_verified",
        "phase8_same_candles_all_values_match",
    ]
    summary = {
        "current_source_run": CURRENT_RUN.name,
        "current_dataset_hash": current_manifest.get("dataset_hash"),
        "phase8_source_run": PHASE8_RUN.name,
        "phase8_dataset_hash": phase8_manifest.get("dataset_hash"),
        "actual_sequential_risk_rejections": len(result),
        "all_independent_calculations_verified": bool(result[verification_columns].all().all()),
        "verification_failures": {
            column: result.loc[~result[column], "ticker"].tolist()
            for column in verification_columns
            if not result[column].all()
        },
        "phase8_same_candles_divergences": result.loc[
            ~result["phase8_same_candles_all_values_match"], "ticker"
        ].tolist(),
        "rounding_gate_changes": result.loc[
            result["rounding_changes_risk_gate"], "ticker"
        ].tolist(),
        "negative_reward_count": int((result["reward_distance"] < 0).sum()),
        "target2_below_or_equal_entry_count": int((result["reward_distance"] <= 0).sum()),
        "median_rr": float(result["rr_engine"].median()),
        "minimum_rr": float(result["rr_engine"].min()),
        "maximum_failed_rr": float(result["rr_engine"].max()),
        "median_risk_distance_percent_of_entry": float(
            result["risk_distance_percent_of_entry"].median()
        ),
        "median_reward_distance_percent_of_entry": float(
            result["reward_distance_percent_of_entry"].median()
        ),
        "median_support_below_close_percent": float(
            result["support_below_close_percent"].median()
        ),
        "current_close_above_prior_resistance_count": int(
            (result["prior_resistance_vs_entry_percent"] < 0).sum()
        ),
        "phase8_archived_dataset_rr_changed_count": int(
            (~np.isclose(
                pd.to_numeric(result["rr_engine"], errors="coerce"),
                pd.to_numeric(result["phase8_archived_dataset_rr"], errors="coerce"),
                rtol=0,
                atol=1e-9,
                equal_nan=True,
            )).sum()
        ),
        "charts_created": len(list(CHART_DIR.glob("*.png"))),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
