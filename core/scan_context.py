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
import time

logger = logging.getLogger(__name__)

# Typed batch states for the live-quote overlay. A failure here is disclosed, never
# replaced by another provider.
RUBIX_BATCH_OK = "RUBIX_BATCH_OK"
RUBIX_DB_BUSY = "RUBIX_DB_BUSY"
RUBIX_DB_UNAVAILABLE = "RUBIX_DB_UNAVAILABLE"
RUBIX_BATCH_TIMEOUT = "RUBIX_BATCH_TIMEOUT"
RUBIX_NOT_CHECKED = "RUBIX_NOT_CHECKED"


class ScanCancelled(Exception):
    """Raised to unwind the scan loop once cancellation has been requested."""


@dataclass
class ScanDataContext:
    """Resources shared by every symbol of ONE scan.

    ``eodhd_client`` is this scan's session. ``expected_completed_session`` is resolved
    once. ``rubix_overlays`` is the single batched live-quote map. ``cancellation_event``
    is checked between symbols and before any provider work.
    """

    eodhd_client: object = None
    expected_completed_session: object = None
    rubix_overlays: dict = field(default_factory=dict)
    rubix_batch_status: str = RUBIX_NOT_CHECKED
    rubix_batch_detail: str = ""
    rubix_batch_seconds: float = 0.0
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

    def overlay_for(self, symbol):
        """The batched overlay for ``symbol``, or ``None`` when it has none.

        Never opens a connection and never falls back to another provider.
        """
        return self.rubix_overlays.get(symbol)

    @property
    def rubix_available_count(self) -> int:
        return sum(1 for overlay in self.rubix_overlays.values()
                   if isinstance(overlay, dict) and overlay.get("available"))

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


def build_scan_context(symbols, *, rubix_provider=None, eodhd_client=None,
                       cancellation_event=None, load_overlays=True, breaker=None):
    """Resolve every once-per-scan answer and return the context.

    ``symbols`` is the approved universe in its original order. The Rubix overlay map is
    loaded with ONE batched call; if that fails the scan still proceeds on completed EODHD
    daily history with a typed batch status, because a missing live overlay must never be
    substituted from another provider.
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

    if load_overlays:
        _load_overlays(context, symbols, rubix_provider)
    return context


def _load_overlays(context, symbols, rubix_provider):
    """One batched Rubix read for the whole universe, with typed failure states."""
    from providers.base_provider import (
        ProviderConfigurationError,
        ProviderConnectionError,
        ProviderError,
    )

    if rubix_provider is None:
        from core.data_provider import _provider_instances
        rubix_provider = _provider_instances().get("rubix")
    if rubix_provider is None or not hasattr(rubix_provider,
                                             "load_latest_quote_overlays"):
        context.rubix_batch_status = RUBIX_DB_UNAVAILABLE
        context.rubix_batch_detail = "no Rubix provider is configured"
        return

    started = time.monotonic()
    try:
        context.rubix_overlays = rubix_provider.load_latest_quote_overlays(symbols)
        context.rubix_batch_status = RUBIX_BATCH_OK
    except ProviderConfigurationError as error:
        context.rubix_batch_status = RUBIX_DB_UNAVAILABLE
        context.rubix_batch_detail = str(error)
    except ProviderConnectionError as error:
        detail = str(error).lower()
        context.rubix_batch_status = (
            RUBIX_DB_BUSY if "lock" in detail or "busy" in detail
            else RUBIX_DB_UNAVAILABLE)
        context.rubix_batch_detail = str(error)
    except ProviderError as error:
        context.rubix_batch_status = RUBIX_DB_UNAVAILABLE
        context.rubix_batch_detail = str(error)
    finally:
        context.rubix_batch_seconds = time.monotonic() - started
    if context.rubix_batch_status != RUBIX_BATCH_OK:
        logger.warning("Rubix batch overlay unavailable (%s): %s",
                       context.rubix_batch_status, context.rubix_batch_detail)
