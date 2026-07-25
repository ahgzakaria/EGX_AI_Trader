"""Reproducible daily Markdown summary for advisory output only."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os

import pandas as pd


def write_daily_report(rows, market_health, sectors, output_root="reports/decision_support", now=None):
    now = now or datetime.now().astimezone()
    directory = Path(output_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"DAILY_DECISION_SUPPORT_{now:%Y%m%d}.md"
    frame = pd.DataFrame(rows or [])
    top = frame.head(10) if not frame.empty else frame
    blocked = (
        frame[frame["QualityGate"] == "BLOCKED"]
        if not frame.empty and "QualityGate" in frame else frame.iloc[0:0]
    )
    best_sector = "Unavailable"
    worst_sector = "Unavailable"
    if sectors is not None and not sectors.empty and not (sectors["Sector"] == "Unknown").all():
        best_sector = str(sectors.iloc[0]["Sector"])
        worst_sector = str(sectors.iloc[-1]["Sector"])
    lines = [
        f"# Decision Support Daily Report — {now:%Y-%m-%d}", "",
        "> Analysis and paper alerts only. No order execution.", "",
        "## Today's Market Summary", "",
        f"- Market bias: {market_health.get('market_bias', 'N/A')}",
        f"- Overall market score: {market_health.get('overall_market_score', 'N/A')}/10",
        f"- Advances / Declines: {market_health.get('advances', 0)} / {market_health.get('declines', 0)}",
        f"- Advance/Decline: {market_health.get('advance_decline', 0)}",
        f"- Volume strength: {market_health.get('volume_strength', 'N/A')}",
        f"- Best sector: {best_sector}", f"- Weakest sector: {worst_sector}", "",
        "## Top Opportunities", "",
    ]
    if top.empty:
        lines.append("No current opportunities were available.")
    else:
        for _, row in top.iterrows():
            lines.append(
                f"- #{int(row['EdgeRank'])} {row['Ticker']} — Edge {row['EdgeScore']:.2f}/10 — "
                f"{row['Recommendation']}"
            )
    lines.extend(["", "## Rejected / Blocked Opportunities", ""])
    lines.append(f"- Count: {len(blocked)}")
    for _, row in blocked.head(20).iterrows():
        lines.append(f"- {row['Ticker']}: {'; '.join(row.get('Warnings') or [])}")
    lines.extend(["", "## Risk Summary", "", "- User retains every Buy/Sell/Wait/Ignore decision.", "- No recommendation was executed automatically."])
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path
