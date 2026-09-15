"""Scan-scoped resources for one market scan.

A universe scan repeatedly asked the same questions: it built an EODHD client per symbol,
recomputed the expected completed session per symbol, and opened a Rubix connection per
symbol. Those answers are constant for the duration of one scan and are resolved here,
once, then handed to every symbol evaluation.

The context is deliberately **scan-scoped rather than process-global**: each scan builds
its own EODHD session, so a scan started after a token, settings or configuration change
observes that change. Callers outside a scan keep their previous behaviour.

Nothing in this module fetches market data, computes an indicator, or makes a trading
decision. It owns lifetimes and cancellation, not analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading

logger = logging.getLogger(__name__)

# A scan used to open the Rubix database once and read a quote for every symbol. That
# feed was retired on 2026-09-10 and nothing has written the database since, so a scan
# reads no live quote at all; see ``core.live_feed``.


class ScanCancelled(Exception):
    """Raised to unwind the scan loop once cancellation has been requested."""


@dataclass
class ScanDataContext:
    """Resources shared by every symbol of ONE scan.

    ``eodhd_client`` is this scan's session. ``expected_completed_session`` is resolved
    once. ``cancellation_event`` is checked between symbols and before any provider work.
    """

    eodhd_client: object = None
    expected_completed_session: object = None
    cancellation_event: threading.Event = field(default_factory=threading.Event)
    # Bounds for any refresh this scan performs. ``None`` means use the module default.
    request_deadline_seconds: float = None
    max_refresh_attempts: int = None
    #: Optional CircuitBreaker. When open, no NEW network refresh is started.
    breaker: object = None
    _owns_client: bool = False

    # -- cancellation ------------------------------------------------------- #

    @property
    def cancelled(self) -> bool:
        return self.cancellation_event.is_set()

    def cancel(self):
        self.cancellation_event.set()

    def raise_if_cancelled(self):
        """Cooperative cancellation point. Called between symbols and phases."""
        if self.cancelled:
            raise ScanCancelled("scan cancelled by operator")

    # -- bounded provider requests ------------------------------------------ #

    def request_budget(self):
        """The bound every in-scan EODHD refresh must respect.

        One attempt, a total deadline, and this scan's cancellation predicate — checked
        immediately before and after the network call, so a stopped scan does not sit
        waiting on a request nobody wants any more.
        """
        from providers.eodhd_client import SCAN_RETRIES, SCAN_TOTAL_DEADLINE

        return {
            "deadline_seconds": self.request_deadline_seconds or SCAN_TOTAL_DEADLINE,
            "max_attempts": self.max_refresh_attempts or SCAN_RETRIES,
            "cancel": self.cancellation_event.is_set,
            "breaker": self.breaker,
        }

    # -- live quote overlay ------------------------------------------------- #

    # -- lifetime ----------------------------------------------------------- #

    def close(self):
        """Release anything this context owns. Safe to call more than once."""
        client = self.eodhd_client
        self.eodhd_client = None
        if client is not None and self._owns_client:
            closer = getattr(client, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # a cleanup fault must never mask a scan result
                    logger.debug("EODHD client close failed", exc_info=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def build_scan_context(symbols, *, eodhd_client=None, cancellation_event=None,
                       breaker=None):
    """Resolve every once-per-scan answer and return the context.

    ``symbols`` is the approved universe in its original order. No live quote is loaded:
    the Rubix feed is retired, and a missing live quote is never substituted from another
    provider.
    """
    from core.research_router import _compute_expected_completed_session

    context = ScanDataContext(
        cancellation_event=cancellation_event or threading.Event())
    context.breaker = breaker

    if eodhd_client is None:
        from providers.eodhd_client import EODHDClient
        # Constructed per scan so a token or settings change is picked up next scan.
        context.eodhd_client = EODHDClient()
        context._owns_client = True
    else:
        context.eodhd_client = eodhd_client
        context._owns_client = False

    context.expected_completed_session = _compute_expected_completed_session()
    return context

