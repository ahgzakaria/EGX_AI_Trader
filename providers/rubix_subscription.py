"""Canonical Rubix subscription planning for the external collector.

This module builds protocol identifiers only. EGX AI Trader never connects to
Rubix; the independent collector consumes the deterministic batches.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from core.symbols import SYMBOL_SOURCE, load_active_symbols
from providers.symbol_mapping import to_rubix_subscription_symbol


_ENGINE_SYMBOL = re.compile(r"^[A-Z0-9]+\.CA$")


@dataclass(frozen=True)
class RubixSubscriptionPlan:
    requested: tuple[str, ...]
    subscriptions: tuple[str, ...]
    invalid: tuple[dict, ...]
    batches: tuple[tuple[str, ...], ...]

    def as_report(self):
        return {
            "requested_symbols": len(self.requested),
            "valid_subscriptions": len(self.subscriptions),
            "invalid_mappings": list(self.invalid),
            "subscription_batches": len(self.batches),
        }


def build_rubix_subscription_plan(source=SYMBOL_SOURCE, batch_size=100):
    """Load the project universe and create deterministic provider batches."""

    requested = tuple(load_active_symbols(source))
    subscriptions = []
    invalid = []
    seen = set()
    for raw in requested:
        symbol = str(raw).strip().upper()
        if not _ENGINE_SYMBOL.fullmatch(symbol):
            invalid.append({"symbol": raw, "reason": "expected uppercase .CA ticker"})
            continue
        try:
            mapped = to_rubix_subscription_symbol(symbol)
        except (TypeError, ValueError) as error:
            invalid.append({"symbol": raw, "reason": str(error)})
            continue
        if mapped not in seen:
            seen.add(mapped)
            subscriptions.append(mapped)

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
    )
