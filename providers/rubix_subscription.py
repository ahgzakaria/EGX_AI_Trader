"""Canonical Rubix subscription planning for the external collector.

This module builds protocol identifiers only. EGX AI Trader never connects to
Rubix; the independent collector consumes the deterministic batches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from core.symbols import SYMBOL_SOURCE, load_active_symbols
from core.universe import RUBIX_VERIFIED, canonical, load_universe, lookup
from providers.symbol_mapping import to_rubix_subscription_symbol


_ENGINE_SYMBOL = re.compile(r"^[A-Z0-9]+\.CA$")


@dataclass(frozen=True)
class RubixSubscriptionPlan:
    requested: tuple[str, ...]
    subscriptions: tuple[str, ...]
    invalid: tuple[dict, ...]
    batches: tuple[tuple[str, ...], ...]
    #: Retired tickers kept subscribed purely so an open position can be exited.
    exit_monitoring: tuple[str, ...] = field(default=())
    #: Active symbols with no VERIFIED Rubix mapping. Reported, never guessed —
    #: an operator resolves these; a fabricated ``CASE~`` key is never emitted.
    unmapped: tuple[str, ...] = field(default=())

    def as_report(self):
        return {
            "requested_symbols": len(self.requested),
            "valid_subscriptions": len(self.subscriptions),
            "invalid_mappings": list(self.invalid),
            "subscription_batches": len(self.batches),
            "exit_monitoring_symbols": list(self.exit_monitoring),
            "unmapped_symbols": list(self.unmapped),
        }


def build_rubix_subscription_plan(source=SYMBOL_SOURCE, batch_size=100,
                                  open_position_symbols=()):
    """Deterministic provider batches for the authoritative universe.

    Every subscription key comes from the universe record's explicit, verified
    ``rubix_symbol``; nothing is produced by blind suffix substitution. A symbol
    whose mapping was never verified against a real feed observation is reported
    as invalid rather than guessed.

    ``open_position_symbols`` keeps a REMOVED ticker subscribed while a paper
    position is still open, so an exit can be monitored. It never makes that
    ticker eligible for a new entry.
    """

    requested = tuple(load_active_symbols(source))
    subscriptions = []
    invalid = []
    unmapped = []
    seen = set()

    def _admit(raw):
        record = lookup(str(raw).strip().upper())
        if record is None:
            invalid.append({"symbol": raw, "reason": "not in the authoritative universe"})
            return None
        if not record.has_verified_rubix_mapping:
            unmapped.append(record.canonical_symbol)
            return None
        if record.rubix_symbol not in seen:
            seen.add(record.rubix_symbol)
            subscriptions.append(record.rubix_symbol)
        return record

    for raw in requested:
        _admit(raw)

    exit_monitoring = []
    for raw in open_position_symbols or ():
        record = lookup(raw)
        if record is None or record.is_active:
            continue                      # already covered by the active universe
        if _admit(raw) is not None:
            exit_monitoring.append(record.canonical_symbol)

    size = max(1, int(batch_size))
    batches = tuple(
        tuple(subscriptions[index:index + size])
        for index in range(0, len(subscriptions), size)
    )
    return RubixSubscriptionPlan(
        requested=requested,
        subscriptions=tuple(subscriptions),
        invalid=tuple(invalid),
        batches=batches,
        exit_monitoring=tuple(exit_monitoring),
        unmapped=tuple(unmapped),
    )
