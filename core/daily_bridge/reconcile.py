"""Phase 7 — reconcile a Rubix-derived bar against an external/Yahoo bar.

Classifies the difference for overlapping completed sessions. Never silently
chooses a value during a material conflict — it reports the conflict so a declared
priority rule (applied elsewhere, only after evidence) decides.
"""

from __future__ import annotations

from dataclasses import dataclass

MATCH = "MATCH"
PRICE_MINOR_DIFFERENCE = "PRICE_MINOR_DIFFERENCE"
CLOSE_DEFINITION_DIFFERENCE = "CLOSE_DEFINITION_DIFFERENCE"
VOLUME_SCALE_DIFFERENCE = "VOLUME_SCALE_DIFFERENCE"
CORPORATE_ACTION_DIFFERENCE = "CORPORATE_ACTION_DIFFERENCE"
MATERIAL_CONFLICT = "MATERIAL_CONFLICT"
EXTERNAL_MISSING = "EXTERNAL_MISSING"
RUBIX_INCOMPLETE = "RUBIX_INCOMPLETE"

PRICE_MINOR_PCT = 0.5          # <=0.5% price diff is minor
MATERIAL_PCT = 3.0            # >3% unexplained price diff is material
VOLUME_SCALE_RATIO = 1.5     # >1.5x volume ratio suggests a scale/definition diff


@dataclass
class ReconResult:
    canonical_symbol: str
    session_date: str
    result: str
    open_diff_pct: float | None = None
    high_diff_pct: float | None = None
    low_diff_pct: float | None = None
    close_diff_pct: float | None = None
    volume_ratio: float | None = None
    detail: str = ""

    def as_row(self):
        return dict(self.__dict__)


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / b * 100.0, 4)


def reconcile_bar(rubix, external):
    """`rubix`/`external` are dicts with open/high/low/official_close(or close)/volume."""
    sym = (rubix or external).get("canonical_symbol")
    date_ = (rubix or external).get("session_date")
    if external is None:
        return ReconResult(sym, date_, EXTERNAL_MISSING, detail="no external bar for this date")
    if rubix is None or str(rubix.get("finalization_status")) != "FINAL":
        return ReconResult(sym, date_, RUBIX_INCOMPLETE,
                           detail="no FINAL Rubix bar for this date")

    def g(d, *keys):
        for k in keys:
            v = d.get(k)
            if v not in (None, "", "None"):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return None

    o = _pct(g(rubix, "open"), g(external, "open", "Open"))
    h = _pct(g(rubix, "high"), g(external, "high", "High"))
    l = _pct(g(rubix, "low"), g(external, "low", "Low"))
    rc = g(rubix, "official_close", "close")
    ec = g(external, "close", "Close", "official_close")
    c = _pct(rc, ec)
    rv = g(rubix, "volume"); ev = g(external, "volume", "Volume")
    vratio = round(rv / ev, 4) if (rv and ev and ev > 0) else None

    res = ReconResult(sym, date_, MATCH, o, h, l, c, vratio)
    price_diffs = [abs(x) for x in (o, h, l, c) if x is not None]
    max_price = max(price_diffs) if price_diffs else 0.0

    if vratio is not None and (vratio > VOLUME_SCALE_RATIO or vratio < 1 / VOLUME_SCALE_RATIO):
        res.result = VOLUME_SCALE_DIFFERENCE
        res.detail = f"volume ratio {vratio} (scale/definition difference)"
    if max_price <= PRICE_MINOR_PCT and res.result == MATCH:
        res.result = MATCH if max_price == 0 else PRICE_MINOR_DIFFERENCE
    elif max_price > MATERIAL_PCT:
        # a large, uniform shift across O/H/L/C is a likely corporate action
        vals = [x for x in (o, h, l, c) if x is not None]
        uniform = vals and (max(vals) - min(vals) < 1.0)
        res.result = CORPORATE_ACTION_DIFFERENCE if uniform else MATERIAL_CONFLICT
        res.detail = (f"max price diff {max_price:.2f}% "
                      f"({'uniform → likely corporate action' if uniform else 'non-uniform → material conflict'})")
    elif c is not None and abs(c) > PRICE_MINOR_PCT and (o is None or abs(o) <= PRICE_MINOR_PCT):
        # close differs but open matches -> close-definition (auction vs last)
        res.result = CLOSE_DEFINITION_DIFFERENCE
        res.detail = f"close differs {c:.2f}% while open matches (auction vs last-trade close)"
    elif res.result == MATCH and max_price > PRICE_MINOR_PCT:
        res.result = PRICE_MINOR_DIFFERENCE
    return res


def reconcile_sessions(rubix_bars, external_bars):
    ext = {(b.get("canonical_symbol"), b.get("session_date")): b for b in external_bars}
    rub = {(b.get("canonical_symbol"), b.get("session_date")): b for b in rubix_bars}
    keys = sorted(set(ext) | set(rub))
    return [reconcile_bar(rub.get(k), ext.get(k)) for k in keys]
