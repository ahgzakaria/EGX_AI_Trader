"""Read-only access to the frozen Yahoo snapshot (``LEGACY_BACKTEST_V1``).

Every strategy finding in this project was measured on the ``yahoo`` rows of
``data/market_data_cache.sqlite``. Their remaining value is reproducibility, so
this is the only way the program reads them, and it can neither download nor
write:

  * a missing entry raises; nothing is fetched to fill it,
  * an expired entry is served as it is, because the snapshot does not age,
  * rows after the freeze session are ignored.

The freeze session is read from the snapshot itself: the last session most
symbols end on. Six entries were replaced by live downloads after the freeze
(ABUK and HRHO on 2026-08-31, ACGC, COMI, JUFO and NCCW on 2026-09-07) and run
past it; cutting them at the freeze keeps a backtest from reaching sessions the
rest of the universe does not have.
"""

from collections import Counter

import pandas as pd

from providers.base_provider import MarketDataProvider, ProviderDataError

SNAPSHOT_PROVIDER = "yahoo"

_FREEZE_CACHE = {}


class FrozenYahooSnapshotProvider(MarketDataProvider):
    name = SNAPSHOT_PROVIDER
    delayed = True
    delay_minutes = None

    def __init__(self, cache):
        self.cache = cache

    def freeze_session(self, period, interval):
        """The last session most snapshot symbols end on, or ``None`` if empty.

        A tie goes to the earlier session, so an ambiguous snapshot is cut short
        rather than extended.
        """

        key = _cache_key(self.cache, period, interval)
        if key not in _FREEZE_CACHE:
            latest = self.cache.latest_sessions(SNAPSHOT_PROVIDER, period, interval)
            counts = Counter(latest.values())
            if counts:
                top = max(counts.values())
                _FREEZE_CACHE[key] = min(s for s, n in counts.items() if n == top)
            else:
                _FREEZE_CACHE[key] = None
        return _FREEZE_CACHE[key]

    def load_history(self, symbol, period, interval):
        frame = self.cache.load_cached(
            SNAPSHOT_PROVIDER, symbol, period, interval, allow_expired=True
        )
        freeze = self.freeze_session(period, interval)
        ignored = 0
        if freeze is not None:
            keep = frame.index <= pd.Timestamp(freeze)
            ignored = int((~keep).sum())
            metadata = frame.attrs.get("market_data", {})
            frame = frame[keep].copy()
            frame.attrs["market_data"] = dict(metadata)
        if frame.empty:
            raise ProviderDataError(
                f"frozen Yahoo snapshot has no rows for {symbol} on or before {freeze}"
            )
        frame.attrs["market_data"].update({
            "snapshot_freeze_session": freeze[:10] if freeze else None,
            "snapshot_rows_after_freeze_ignored": ignored,
            "yahoo_network_used": False,
        })
        return frame


def _cache_key(cache, period, interval):
    # The file's size and mtime are part of the key, so a snapshot changed on
    # disk is never cut at a session computed from its previous contents.
    try:
        stat = cache.path.stat()
        identity = (str(cache.path.resolve()), stat.st_size, stat.st_mtime_ns)
    except OSError:
        identity = (str(cache.path), None, None)
    return (*identity, str(period), str(interval))
