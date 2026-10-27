"""Fixtures for integration tests: real settings from the environment and .env."""

import pytest

from app.core.settings import Settings, load_settings


@pytest.fixture(scope="session")
def live_settings() -> Settings:
    """Return the real settings; the values are never printed."""
    return load_settings()
