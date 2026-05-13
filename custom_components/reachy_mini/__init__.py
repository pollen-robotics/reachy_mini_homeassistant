"""The Reachy Mini integration.

Polled snapshot of robot state, served by the Reachy Mini daemon at
`GET /api/homeassistant/state`. The integration's only job is to:

1. Be claimed by HA when the daemon's `_reachy-mini._tcp.local.` mDNS
   record appears on the LAN (the matching is declared in
   `manifest.json`).
2. Drive a :class:`DataUpdateCoordinator` that polls the endpoint on
   the configured schedule.
3. Expose the JSON keys as `sensor.*` and `binary_sensor.*` entities
   grouped under a single Reachy Mini device.

The integration carries no robot-side logic; everything it shows comes
from the upstream daemon's documented `schema_version: 1` contract.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ReachyMiniCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Reachy Mini from a config entry."""
    coordinator = ReachyMiniCoordinator(hass, entry)
    # Block until the first poll succeeds so entity_setup can read the
    # `model` / `manufacturer` / `firmware_version` fields for the
    # device registry.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
