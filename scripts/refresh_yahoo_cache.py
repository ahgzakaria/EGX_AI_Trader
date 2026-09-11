"""Retired: the Yahoo daily cache is the frozen backtest snapshot.

This script used to append recent Yahoo sessions to
``data/market_data_cache.sqlite``. Its task, EGX_AI_Trader_YahooCacheRefresh,
was disabled on 2026-07-24, and the rows it maintained became
``LEGACY_BACKTEST_V1``: the input every backtest in this project was measured
on. Refreshing them would change those results, so this script refuses before
any download, and ``LocalCacheProvider.store`` refuses Yahoo rows as well.

Current daily history comes from ``core.research_router``.
"""

MESSAGE = (
    "refresh_yahoo_cache is retired: the Yahoo cache is the frozen backtest "
    "snapshot (LEGACY_BACKTEST_V1) and must not be refreshed. Current daily "
    "history comes from core.research_router."
)


def refresh(symbols_source=None):
    raise SystemExit(MESSAGE)


if __name__ == "__main__":
    refresh()
