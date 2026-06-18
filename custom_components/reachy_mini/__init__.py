"""The Reachy Mini integration.

A thin client over the Reachy Mini daemon's existing REST surface.
Its only job is to:

1. Be claimed by HA when the daemon's `_reachy-mini._tcp.local.` mDNS
   record appears on the LAN — the matching is declared in
   `manifest.json` and filtered by the `manufacturer=Pollen Robotics`
   TXT property (variant-agnostic across Wireless and Lite).
2. Drive a :class:`DataUpdateCoordinator` that polls several daemon
   endpoints in parallel (`/api/daemon/status`,
   `/api/daemon/robot-app-lock-status`, `/api/state/doa`,
   `/api/volume/current`, `/api/volume/microphone/current`) and
   assembles a unified state dict — including HA-shaped derivations
   (`awake`, `active_app_transport`, `webrtc_active`).
3. Expose that dict as entities across the sensor, binary_sensor,
   number, select, and button platforms, grouped under a single
   Reachy Mini device.

The integration carries no robot-side logic; it depends only on the
daemon's documented routes.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ReachyMiniCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Reachy Mini from a config entry."""
    coordinator = ReachyMiniCoordinator(hass, entry)
    # Block until the first poll succeeds so entity_setup can read the
    # `model` / `manufacturer` / `firmware_version` fields for the
    # device registry.
    await coordinator.async_config_entry_first_refresh()
    # Pull recorded-move catalogs once. Stable at runtime, so we don't
    # include them in the per-tick poll. Failures inside are logged but
    # don't abort setup — the matching entities just won't be created.
    await coordinator.async_load_move_lists()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove the service only when the last Reachy Mini entry is
        # gone; otherwise other entries would lose the service action.
        if not hass.data[DOMAIN]:
            async_unregister_services(hass)
    return unload_ok
