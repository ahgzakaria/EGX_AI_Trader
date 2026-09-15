"""What the pages say about live prices, now that there is no live feed.

The Rubix live quote feed was retired on the evening of 2026-09-10 (commits
0c1bd76, c1defae, 2d73a31). It never carried the 14:25 auction close, so the
"close" it produced was the last continuous trade in seven sessions of ten;
the daily candle comes from MubasherTrade PRO's measured record instead, on
top of EODHD. Nothing writes the Rubix database any more, and its newest row is
from 14:18 on 2026-09-10.

Several pages kept presenting Rubix as the live source -- the Daily Dashboard's
price-source cell, its scan banner, the AI Analysis chart tabs, System Health.
The fix is not to decide whether those five-day-old quotes are fresh or stale:
there is no live source, and every price on screen is a completed session's
close. This module is the one place that says so, so the pages cannot disagree.

Decisions never depended on the wording. A signal is actionable, and a
portfolio price is live, only when a quote is FRESH, which none has been since.
"""

from __future__ import annotations

#: The evening the feed was retired.
RUBIX_RETIRED_ON = "2026-09-10"

#: The price every page actually shows.
PRICE_SOURCE_AR = "إغلاق آخر جلسة"
PRICE_SOURCE_EN = "Last completed session close"

#: One line for a banner or caption.
NO_LIVE_FEED_AR = "لا توجد أسعار لحظية — أُوقف Rubix في 2026-09-10"
NO_LIVE_FEED_EN = "No live quotes — the Rubix feed was retired on 2026-09-10"

#: The value for a status line whose label is already "Live quotes".
LIVE_QUOTES_STATUS_EN = "None · Rubix retired 2026-09-10"

#: The longer explanation, for a help tooltip or a diagnostics page.
RETIREMENT_NOTE = (
    "Rubix was retired on 2026-09-10: it never sent the 14:25 auction close, so "
    "its daily close was the last continuous trade in seven sessions of ten. "
    "Every price shown is a completed session's close from EODHD with "
    "MubasherTrade PRO's measured tail. No page shows live quotes."
)

#: Readable names for the history providers a scan row can carry.
HISTORY_SOURCE_LABELS = {
    "eodhd": "EODHD",
    "eodhd_plus_mubasher": "EODHD + Mubasher",
    "local_plus_mubasher": "Local + Mubasher",
    "mubasher_live": "Mubasher",
    "mubasher_index": "Mubasher",
}


def history_source_label(values):
    """One readable label for the history providers a set of rows used.

    ``values`` is any iterable of provider names. Unknown names are shown as
    they are rather than guessed at; nothing known yields "—".
    """
    names = []
    for value in values:
        key = str(value or "").strip().lower()
        if not key or key == "unknown":
            continue
        label = HISTORY_SOURCE_LABELS.get(key, key)
        if label not in names:
            names.append(label)
    if not names:
        return "—"
    return " · ".join(sorted(names))
