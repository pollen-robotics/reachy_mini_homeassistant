"""DataUpdateCoordinator for Reachy Mini.

Polls the daemon's `/api/homeassistant/state` endpoint on a fixed
schedule and caches the latest payload for all entity platforms.

Failure handling: a single failed poll surfaces as `UpdateFailed` so
all entities go `unavailable`. The next successful poll recovers them.
This matches the rest of HA's polled-integration ecosystem.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    ENDPOINT_PATH,
    SUPPORTED_SCHEMA_VERSIONS,
)

_LOGGER = logging.getLogger(__name__)


class ReachyMiniCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls `/api/homeassistant/state` every :data:`DEFAULT_SCAN_INTERVAL`."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Bind the coordinator to a config entry."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_HOST]}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self._entry = entry
        self._session = async_get_clientsession(hass)
        self._unknown_schema_logged = False

    @property
    def host(self) -> str:
        """Configured hostname or IP of the daemon."""
        return self._entry.data[CONF_HOST]

    @property
    def port(self) -> int:
        """Configured TCP port of the daemon (default 8000)."""
        return self._entry.data.get(CONF_PORT, DEFAULT_PORT)

    @property
    def url(self) -> str:
        """Fully-qualified URL of the HA aggregator endpoint."""
        return f"http://{self.host}:{self.port}{ENDPOINT_PATH}"

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and return the latest state snapshot."""
        try:
            async with self._session.get(
                self.url,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                payload: dict[str, Any] = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Cannot reach Reachy Mini at {self.url}: {err}") from err

        schema = payload.get("schema_version")
        if schema not in SUPPORTED_SCHEMA_VERSIONS and not self._unknown_schema_logged:
            _LOGGER.warning(
                "Reachy Mini reported unknown schema_version=%s; "
                "some fields may be missing or renamed. "
                "Consider upgrading the integration.",
                schema,
            )
            # Log once per coordinator lifecycle to avoid spamming.
            self._unknown_schema_logged = True
        return payload
