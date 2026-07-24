"""Deterministic daily, weekly, and monthly forward-testing reports."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


REPORT_ROOT = Path("reports/forward_testing")


class ForwardReportGenerator:
    """Build reports only from persisted facts, never from transient UI state."""

    def __init__(self, service):
        self.service = service
        self.database = service.database

    def generate_all(self, timestamp, run_id, run_directory=None):
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        daily_name = f"DAILY_REPORT_{timestamp:%Y%m%d}.md"
        iso = timestamp.isocalendar()
        # Period-to-date reports include their as-of date. This preserves every
        # historical version while avoiding a stale Monday/week-start report.
        weekly_name = (
            f"WEEKLY_REPORT_{iso.year}_W{iso.week:02d}_ASOF_{timestamp:%Y%m%d}.md"
        )
        monthly_name = f"MONTHLY_REPORT_{timestamp:%Y%m}_ASOF_{timestamp:%Y%m%d}.md"
        paths = {
            "daily": self._write_once(
                daily_name, self._daily(timestamp, run_id), run_directory
            ),
            "weekly": self._write_once(
                weekly_name, self._period(timestamp, "week"), run_directory
            ),
            "monthly": self._write_once(
                monthly_name, self._period(timestamp, "month"), run_directory
            ),
        }
        # Each session receives a machine-readable outcome snapshot in its Run
        # directory; root-level dated Markdown history is never overwritten.
        if run_directory:
            outcome = pd.DataFrame(self.service.outcome_summary())
            outcome.to_csv(
                run_directory / "signal_outcomes.csv",
                index=False,
                encoding="utf-8-sig",
            )
        return {key: str(value) for key, value in paths.items()}

    def _write_once(self, filename, content, run_directory):
        destination = REPORT_ROOT / filename
        if not destination.exists():
            # Exclusive creation is the filesystem-level no-rewrite guarantee.
            with destination.open("x", encoding="utf-8") as handle:
                handle.write(content)
        if run_directory:
            run_copy = run_directory / filename
            if not run_copy.exists():
                run_copy.write_text(content, encoding="utf-8")
        return destination

    def _daily(self, timestamp, run_id):
        day = timestamp.date().isoformat()
        signals = self.database.rows(
            "SELECT * FROM signals WHERE run_id=? ORDER BY ranking,ticker", (run_id,)
        )
        closed = self.database.rows(
            "SELECT * FROM paper_positions WHERE exit_date=? ORDER BY ticker", (day,)
        )
        portfolio = self.service.portfolio_status()
        outcomes = self.service.outcome_summary()
        ai = [row["ai_probability"] for row in signals if row["ai_probability"] is not None]
        alerts = self.database.rows(
            "SELECT * FROM alerts WHERE substr(created_at,1,10)=? ORDER BY created_at", (day,)
        )
        lines = [
            f"# Daily Forward Testing Report — {day}", "",
            f"- Run ID: {run_id}",
            f"- Generated at: {timestamp.isoformat()}",
            f"- New signals: {len(signals)}",
            f"- New BUY signals: {sum(row['signal_type']=='BUY' for row in signals)}",
            f"- Closed trades: {len(closed)}", "",
            "## New Signals", "",
            self._signal_table(signals), "", "## Closed Trades", "",
            self._closed_table(closed), "", "## Portfolio Status", "",
            f"- Cash: {portfolio['cash']:.2f}",
            f"- Equity: {portfolio['equity']:.2f}",
            f"- Open positions: {portfolio['open_positions']}",
            f"- Exposure: {portfolio['exposure_pct']:.2f}%",
            f"- Current drawdown: {portfolio['current_drawdown_pct']:.2f}%",
            f"- Open risk: {portfolio['open_risk']:.2f}",
            f"- Sector allocation: {json.dumps(portfolio['sector_allocation'], sort_keys=True)}",
            "", "## Performance", "", self._outcome_table(outcomes), "",
            "## AI Statistics", "",
            f"- Predictions available: {len(ai)}",
            f"- Average probability: {sum(ai)/len(ai):.2f}%" if ai else "- Average probability: N/A",
            "", "## Alerts", "",
        ]
        lines.extend(
            f"- {row['alert_type']}: {row['message']}" for row in alerts
        )
        if not alerts:
            lines.append("No alerts.")
        return "\n".join(lines) + "\n"

    def _period(self, timestamp, period_type):
        if period_type == "week":
            period = pd.Period(timestamp.date(), freq="W")
            title = f"Weekly Forward Testing Report — {period}"
        else:
            period = pd.Period(timestamp.date(), freq="M")
            title = f"Monthly Forward Testing Report — {period}"
        start, end = period.start_time.date().isoformat(), period.end_time.date().isoformat()
        signals = self.database.rows(
            "SELECT * FROM signals WHERE signal_date BETWEEN ? AND ? ORDER BY signal_date,ranking",
            (start, end),
        )
        closed = self.database.rows(
            "SELECT * FROM paper_positions WHERE exit_date BETWEEN ? AND ? ORDER BY exit_date",
            (start, end),
        )
        profit = sum(float(row["realized_profit"] or 0) for row in closed)
        return "\n".join([
            f"# {title}", "",
            f"- Period: {start} to {end}",
            f"- Signals: {len(signals)}",
            f"- BUY signals: {sum(row['signal_type']=='BUY' for row in signals)}",
            f"- Closed trades: {len(closed)}",
            f"- Realized profit: {profit:.2f}", "",
            "## Signal Outcomes", "", self._outcome_table(self.service.outcome_summary()),
            "", "## Closed Trades", "", self._closed_table(closed), "",
        ])

    @staticmethod
    def _signal_table(rows):
        if not rows:
            return "No new signals."
        lines = ["| Time | Ticker | Signal | Price | Score | Confidence | RR | AI | Regime | Rank |",
                 "|---|---|---:|---:|---:|---:|---:|---:|---|---:|"]
        lines.extend(
            f"| {row['signal_time']} | {row['ticker']} | {row['signal_type']} | "
            f"{row['price']} | {row['score']} | {row['confidence']} | {row['rr']} | "
            f"{row['ai_probability']} | {row['market_regime']} | {row['ranking']} |"
            for row in rows
        )
        return "\n".join(lines)

    @staticmethod
    def _closed_table(rows):
        if not rows:
            return "No closed trades."
        lines = ["| Ticker | Entry | Exit | Reason | Profit | Holding Days |",
                 "|---|---:|---:|---|---:|---:|"]
        lines.extend(
            f"| {row['ticker']} | {row['entry_price']} | {row['exit_price']} | "
            f"{row['exit_reason']} | {row['realized_profit']} | {row['holding_days']} |"
            for row in rows
        )
        return "\n".join(lines)

    @staticmethod
    def _outcome_table(rows):
        if not rows:
            return "No evaluated signals yet."
        lines = ["| Horizon | Signals | Win Rate | Avg Return | Median | Expectancy | MAE | MFE |",
                 "|---:|---:|---:|---:|---:|---:|---:|---:|"]
        lines.extend(
            f"| {row['horizon_days']} | {row['signals']} | {row['win_rate']:.2f}% | "
            f"{row['average_return']:.2f}% | {row['median_return']:.2f}% | "
            f"{row['expectancy']:.2f}% | {row['average_mae']:.2f}% | "
            f"{row['average_mfe']:.2f}% |" for row in rows
        )
        return "\n".join(lines)
