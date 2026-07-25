"""Yahoo Finance implementation isolated from the rest of the application."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

import yfinance as yf

from providers.base_provider import (
    MarketDataProvider,
    ProviderConnectionError,
    normalize_history,
)


class YahooProvider(MarketDataProvider):
    name = "yahoo"
    delayed = True
    delay_minutes = None

    def __init__(self, downloader=None):
        self._downloader = downloader or yf.download

    def load_history(self, symbol, period, interval):
        try:
            # yfinance writes provider diagnostics directly to stdio. Callers
            # receive a typed exception, so keep batch scans readable.
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                frame = self._downloader(
                    symbol,
                    period=period,
                    interval=interval,
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
        except Exception as error:
            raise ProviderConnectionError(
                f"Yahoo download failed for {symbol}: {error}"
            ) from error

        normalized = normalize_history(frame, symbol, self.name)
        normalized.attrs["market_data"].update({
            "delayed": True,
            "delay_minutes": None,
            "status": "delayed_or_end_of_day",
        })
        return normalized
