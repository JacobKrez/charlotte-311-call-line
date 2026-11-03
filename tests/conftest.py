"""Shared test fixtures.

Every test in this suite runs against the deterministic offline
components. No provider API key is required, and none is read. That is
a hard rule, enforced by an autouse fixture that removes the relevant
environment variables before any test body runs.

The reason is that a test suite that needs a network round-trip to
tell you whether the safety guardrails work is a test suite that will
eventually tell you they work when they do not, because the network
call failed and the assertion was skipped.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _no_provider_credentials(monkeypatch):
    """Ensure no provider credential leaks into a test run."""
    for var in (
        "DEEPGRAM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ELEVENLABS_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def store():
    """An in-memory store."""
    from src.store import Store

    instance = Store(":memory:")
    yield instance
    instance.close()
