"""Test-wide isolation for the AI narrative provider.

``load_project_environment()`` reads ``.env`` into ``os.environ`` the first time
any analysis runs, which sets ``AI_NARRATIVE_ENABLED=true`` plus the Ollama
endpoint and model. That is process-global and irreversible for the rest of the
session, so from that point on ``analyze_symbol()`` reaches a real local model.

Two tests assert that the DEFAULT narrative is the deterministic fallback. They
passed only while nothing had loaded ``.env`` first — that is, only by accident
of collection order and only on a machine where no provider was configured. Once
any earlier module ran a real analysis they failed, and they would equally fail
for any developer whose ``.env`` was loaded first.

A test asserting "the default is the fallback" must not let an installed live
provider decide the answer. So the whole suite runs with the AI narrative
provider explicitly DISABLED, making the deterministic fallback the honest
default everywhere. Tests that exercise the AI path inject their own provider,
config or fake transport and are unaffected.

Production defaults are untouched: this only shapes ``os.environ`` inside pytest.
"""

from __future__ import annotations

import os

import pytest

#: Every environment key that can turn on or steer a live narrative provider.
_AI_NARRATIVE_PREFIX = "AI_NARRATIVE_"


@pytest.fixture(autouse=True)
def deterministic_narrative_environment(monkeypatch):
    """Disable the live AI narrative provider for the duration of each test.

    ``monkeypatch`` restores the previous environment afterwards, so a test that
    deliberately sets its own ``AI_NARRATIVE_*`` values still works and nothing
    leaks into the next test.
    """

    for name in [key for key in os.environ if key.startswith(_AI_NARRATIVE_PREFIX)]:
        monkeypatch.delenv(name, raising=False)
    # Set explicitly rather than merely unsetting: ``load_project_environment``
    # uses a non-overriding dotenv load, so an existing value wins over ``.env``.
    monkeypatch.setenv("AI_NARRATIVE_ENABLED", "false")

    # An accepted narrative is cached in-process by symbol + evidence hash; clear
    # it so one test can never serve another test's narrative.
    try:
        from core.ai_narrative_provider import NARRATIVE_CACHE

        NARRATIVE_CACHE.clear()
    except Exception:                                  # pragma: no cover
        pass
    yield


@pytest.fixture
def live_narrative_environment(monkeypatch):
    """Opt back in, for a test that genuinely wants the provider path enabled.

    Still no network: the caller supplies a fake transport or provider.
    """

    monkeypatch.setenv("AI_NARRATIVE_ENABLED", "true")
    yield
