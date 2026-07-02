"""Shared fixtures for Reachy Mini tests."""

from __future__ import annotations

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.reachy_mini.const import (
    CONF_MODEL,
    CONF_UNIT_ID,
    DOMAIN,
    MODEL_WIRELESS,
)
from custom_components.reachy_mini.coordinator import ReachyMiniCoordinator

TEST_HOST = "172.16.0.170"
TEST_PORT = 8000
BASE_URL = f"http://{TEST_HOST}:{TEST_PORT}"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/ discoverable by the HA test harness."""
    yield


@pytest.fixture
def config_entry(hass) -> MockConfigEntry:
    """A config entry matching a discovered Wireless unit."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: TEST_HOST,
            CONF_PORT: TEST_PORT,
            CONF_UNIT_ID: "1f66778465450401",
            CONF_MODEL: MODEL_WIRELESS,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def coordinator(hass, config_entry, aioclient_mock) -> ReachyMiniCoordinator:
    """A coordinator bound to the test entry (no initial refresh).

    Depends on ``aioclient_mock`` so the mocked session factory is in
    place before the coordinator captures its aiohttp session.
    """
    return ReachyMiniCoordinator(hass, config_entry)
