"""Provider selection policy isolated from trading and indicator code."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from providers.base_provider import ProviderDataError, ProviderError


@dataclass(frozen=True)
class ProviderSelection:
    frame: pd.DataFrame
    requested: str
    effective: str
    fallback_active: bool
    reason: str | None = None


class ProviderManager:
    """Select a configured provider and perform explicit, explainable fallback."""

    def __init__(self, providers, loader):
        self.providers = providers
        self.loader = loader

    def load_history(self, requested, fallback):
        if requested not in self.providers:
            return self._fallback(requested, fallback, f"unknown provider: {requested}")
        try:
            primary = self.loader(self.providers[requested])
        except ProviderError as error:
            return self._fallback(requested, fallback, str(error))

        provider = self.providers[requested]
        if not getattr(provider, "prefer_only_if_newer", False) or fallback == requested:
            return ProviderSelection(primary, requested, requested, False)

        # Rubix is used only when its real feed timestamp is newer than the
        # fallback's latest candle.  Successful downloads alone never imply
        # that either source is current.
        try:
            secondary = self.loader(self.providers[fallback])
        except (KeyError, ProviderError) as error:
            return ProviderSelection(
                primary, requested, requested, False,
                f"fallback comparison unavailable: {error}",
            )
        primary_time = _source_timestamp(primary)
        secondary_time = _source_timestamp(secondary)
        if primary_time > secondary_time:
            metadata = dict(primary.attrs.get("market_data", {}))
            metadata.update({
                "comparison_provider": fallback,
                "comparison_latest_timestamp": secondary_time.isoformat(),
                "source_selection_reason": "Rubix timestamp is newer than Yahoo",
            })
            primary.attrs["market_data"] = metadata
            return ProviderSelection(primary, requested, requested, False)
        reason = (
            f"Rubix timestamp {primary_time.isoformat()} is not newer than "
            f"Yahoo {secondary_time.isoformat()}"
        )
        metadata = dict(secondary.attrs.get("market_data", {}))
        metadata.update({
            "comparison_provider": requested,
            "comparison_latest_timestamp": primary_time.isoformat(),
            "source_selection_reason": reason,
        })
        secondary.attrs["market_data"] = metadata
        return ProviderSelection(secondary, requested, fallback, True, reason)

    def _fallback(self, requested, fallback, reason):
        if fallback not in self.providers:
            raise ProviderDataError(f"Fallback provider is unavailable: {fallback}")
        if fallback == requested:
            raise ProviderDataError(reason)
        frame = self.loader(self.providers[fallback])
        return ProviderSelection(frame, requested, fallback, True, reason)


def _source_timestamp(frame):
    metadata = dict(frame.attrs.get("market_data", {}))
    value = metadata.get("source_latest_timestamp") or metadata.get(
        "latest_exchange_timestamp"
    )
    timestamp = pd.Timestamp(value if value is not None else frame.index[-1])
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Africa/Cairo")
    return timestamp.tz_convert("UTC")
