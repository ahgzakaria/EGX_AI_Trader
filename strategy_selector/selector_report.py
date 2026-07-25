"""Report serialization helpers for Phase 11 research artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def save_selector_artifacts(output_dir, summary, decisions, market_rows, robustness, probabilities):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary).to_csv(output_dir / "phase11_selector_summary.csv", index=False)
    pd.DataFrame(decisions).to_csv(output_dir / "phase11_selector_decisions.csv", index=False)
    pd.DataFrame(market_rows).to_csv(output_dir / "phase11_market_regimes.csv", index=False)
    pd.DataFrame(robustness).to_csv(output_dir / "phase11_selector_robustness.csv", index=False)
    (output_dir / "phase11_selector_probabilities.json").write_text(
        json.dumps({"performance": probabilities}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
