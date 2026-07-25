"""Deterministic split-adjustment engine for EODHD daily history.

Produces three explicitly-named series from EODHD raw OHLCV + the EODHD splits feed:

  * RAW_UNADJUSTED      — provider-reported OHLCV, untouched (kept as Raw *).
  * SPLIT_ADJUSTED      — adjusted for stock splits ONLY (candidate to match the
                          project's current Yahoo split-adjusted Close behavior).
  * TOTAL_RETURN_ADJUSTED — split + cash-distribution adjusted (from EODHD
                          adjusted_close); NEVER a drop-in for strategy Close.

Rules: all OHLC in a row use the SAME split factor; price ordering (Low ≤ Open,Close ≤
High) is preserved; a split affects only dates strictly BEFORE its ex-date; invalid /
contradictory split ratios are rejected (never fabricated); raw columns are preserved;
the transform is deterministic and idempotent. No rounding until presentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

RAW_COLUMNS = ["Raw Open", "Raw High", "Raw Low", "Raw Close", "Raw Volume"]
ADJ_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
OUTPUT_COLUMNS = (["Date"] + RAW_COLUMNS + ["Split Factor"] + ADJ_COLUMNS
                  + ["Total Return Adj Close"])


class InvalidSplit(ValueError):
    pass


@dataclass
class AdjustmentResult:
    frame: pd.DataFrame
    provenance: dict = field(default_factory=dict)

    @property
    def ok(self):
        return not self.frame.empty


def parse_split_ratio(raw):
    """Parse an EODHD split string 'num/den' (or a number) into a positive float.

    Rejects zero/negative, non-numeric, or contradictory values.
    """
    if raw is None:
        raise InvalidSplit("empty split")
    s = str(raw).strip()
    try:
        if "/" in s:
            num, den = s.split("/", 1)
            num, den = float(num), float(den)
            if den == 0:
                raise InvalidSplit(f"zero denominator: {s}")
            ratio = num / den
        else:
            ratio = float(s)
    except (ValueError, TypeError) as error:
        raise InvalidSplit(f"unparseable split '{s}'") from error
    if not (ratio > 0) or ratio != ratio:            # <=0 or NaN
        raise InvalidSplit(f"non-positive split ratio '{s}'")
    if ratio > 10000:                                # absurd ratio guard
        raise InvalidSplit(f"implausible split ratio '{s}'")
    return ratio


def _to_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.to_datetime(value).date()


def normalize_splits(splits):
    """[(date, ratio)] sorted ascending, de-duplicated (same-day → single combined).

    A same-day duplicate corporate action is combined by multiplying ratios once.
    Invalid ratios raise InvalidSplit.
    """
    by_date = {}
    for item in splits or []:
        if isinstance(item, dict):
            d = _to_date(item.get("date"))
            ratio = parse_split_ratio(item.get("split"))
        else:
            d, ratio = _to_date(item[0]), parse_split_ratio(item[1])
        by_date[d] = by_date.get(d, 1.0) * ratio
    return sorted(by_date.items())


def cumulative_factor_for(day, sorted_splits):
    """Product of split ratios with ex-date strictly AFTER ``day``.

    Prices before a split's ex-date are divided by that split's ratio; a bar dated on
    or after the ex-date is already post-split.
    """
    factor = 1.0
    d = _to_date(day)
    for sd, ratio in sorted_splits:
        if sd > d:
            factor *= ratio
    return factor


def adjust(frame, splits, *, total_return_close=None):
    """Return an AdjustmentResult with RAW / SPLIT_ADJUSTED / TOTAL_RETURN columns.

    frame: canonical daily frame with Date, Open, High, Low, Close, Volume.
    splits: EODHD splits list (dicts) or [(date, ratio)].
    total_return_close: optional per-Date mapping/Series of EODHD adjusted_close.
    """
    if frame is None or frame.empty:
        return AdjustmentResult(pd.DataFrame(columns=OUTPUT_COLUMNS),
                                {"rows": 0, "splits_applied": 0})
    sorted_splits = normalize_splits(splits)
    df = frame.copy()
    df["Date"] = df["Date"].map(_to_date)
    df = df.drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True)

    factors = df["Date"].map(lambda d: cumulative_factor_for(d, sorted_splits))
    out = pd.DataFrame({
        "Date": df["Date"],
        "Raw Open": df["Open"].astype(float), "Raw High": df["High"].astype(float),
        "Raw Low": df["Low"].astype(float), "Raw Close": df["Close"].astype(float),
        "Raw Volume": pd.to_numeric(df.get("Volume"), errors="coerce").fillna(0).astype(float),
        "Split Factor": factors.astype(float),
    })
    # same factor per row → prices divided, volume multiplied (share-count grows on split)
    out["Open"] = out["Raw Open"] / out["Split Factor"]
    out["High"] = out["Raw High"] / out["Split Factor"]
    out["Low"] = out["Raw Low"] / out["Split Factor"]
    out["Close"] = out["Raw Close"] / out["Split Factor"]
    out["Volume"] = out["Raw Volume"] * out["Split Factor"]

    if total_return_close is not None:
        tr = pd.Series(total_return_close) if not isinstance(total_return_close, pd.Series) \
            else total_return_close
        out["Total Return Adj Close"] = out["Date"].map(lambda d: tr.get(d))
    else:
        out["Total Return Adj Close"] = pd.NA

    _validate_ordering(out)
    provenance = {
        "rows": int(len(out)), "splits_applied": len(sorted_splits),
        "split_events": [(d.isoformat(), round(r, 6)) for d, r in sorted_splits],
        "max_split_factor": float(out["Split Factor"].max()),
        "min_split_factor": float(out["Split Factor"].min()),
        "series": {"raw": "RAW_UNADJUSTED", "adjusted": "SPLIT_ADJUSTED",
                   "total_return": "TOTAL_RETURN_ADJUSTED"},
    }
    return AdjustmentResult(out[OUTPUT_COLUMNS], provenance)


def _validate_ordering(out):
    """Assert Low ≤ Open,Close ≤ High on the adjusted series (ordering is scale-invariant)."""
    bad = out[(out["Low"] > out["High"] + 1e-9)
              | (out["Open"] < out["Low"] - 1e-9) | (out["Open"] > out["High"] + 1e-9)
              | (out["Close"] < out["Low"] - 1e-9) | (out["Close"] > out["High"] + 1e-9)]
    if not bad.empty:
        raise InvalidSplit(f"adjusted OHLC ordering violated on {len(bad)} row(s)")
