"""Service handlers for the Reachy Mini integration.

Currently exposes one service: ``reachy_mini.play_recorded_move``.
Registration is idempotent across multiple config entries and the
service is removed only when the last entry unloads.
"""

from __future__ import annotations

import logging
from typing import Iterable

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, SERVICE_PLAY_RECORDED_MOVE
from .coordinator import ReachyMiniCoordinator

_LOGGER = logging.getLogger(__name__)

PLAY_RECORDED_MOVE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required("dataset"): cv.string,
        vol.Required("move"): cv.string,
    }
)


def _coordinators_for_devices(
    hass: HomeAssistant, device_ids: Iterable[str]
) -> list[ReachyMiniCoordinator]:
    """Map HA device IDs back to their owning Reachy Mini coordinators."""
    registry = dr.async_get(hass)
    coordinators: list[ReachyMiniCoordinator] = []
    seen: set[str] = set()
    for device_id in device_ids:
        device = registry.async_get(device_id)
        if device is None:
            continue
        for entry_id in device.config_entries:
            if entry_id in seen:
                continue
            seen.add(entry_id)
            coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
            if coordinator is not None:
                coordinators.append(coordinator)
    return coordinators


def async_register_services(hass: HomeAssistant) -> None:
    """Register Reachy Mini services. Safe to call multiple times."""
    if hass.services.has_service(DOMAIN, SERVICE_PLAY_RECORDED_MOVE):
        return

    async def _handle_play(call: ServiceCall) -> None:
        device_ids = call.data.get(ATTR_DEVICE_ID) or []
        dataset = call.data["dataset"]
        move = call.data["move"]

        coordinators = _coordinators_for_devices(hass, device_ids)
        if not coordinators:
            raise ServiceValidationError("No Reachy Mini device targeted")

        for coordinator in coordinators:
            # Pre-validate against known catalogs. Custom datasets we
            # haven't enumerated bypass the check — the daemon will
            # 404 if the move doesn't exist.
            known = coordinator.move_lists.get(dataset)
            if known is not None and move not in known:
                raise ServiceValidationError(
                    f"Move '{move}' not found in dataset '{dataset}'"
                )
            await coordinator.async_play_recorded_move(dataset, move)

    hass.services.async_register(
        DOMAIN,
        SERVICE_PLAY_RECORDED_MOVE,
        _handle_play,
        schema=PLAY_RECORDED_MOVE_SCHEMA,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove Reachy Mini services. Called when the last entry unloads."""
    if hass.services.has_service(DOMAIN, SERVICE_PLAY_RECORDED_MOVE):
        hass.services.async_remove(DOMAIN, SERVICE_PLAY_RECORDED_MOVE)
