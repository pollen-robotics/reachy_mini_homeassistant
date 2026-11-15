"""Service handlers for the Reachy Mini integration.

Exposes ``reachy_mini.play_recorded_move``,
``reachy_mini.start_conversation`` and the conversation-app personality
actions (``get_personality`` / ``save_personality`` /
``delete_personality``). Registration is idempotent across multiple config entries and the
service is removed only when the last entry unloads.
"""

from __future__ import annotations

import logging
from typing import Iterable

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    SERVICE_DELETE_PERSONALITY,
    SERVICE_GET_PERSONALITY,
    SERVICE_PLAY_RECORDED_MOVE,
    SERVICE_SAVE_PERSONALITY,
    SERVICE_START_CONVERSATION,
)
from .coordinator import ReachyMiniCoordinator

_LOGGER = logging.getLogger(__name__)

PLAY_RECORDED_MOVE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required("dataset"): cv.string,
        vol.Required("move"): cv.string,
    }
)

START_CONVERSATION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("personality"): cv.string,
        vol.Optional("voice"): cv.string,
        vol.Optional("greeting"): cv.string,
        vol.Optional("instructions"): cv.string,
        vol.Optional("context"): cv.string,
    }
)


_DEVICE = vol.Required(ATTR_DEVICE_ID)
_DEVICE_IDS = vol.All(cv.ensure_list, [cv.string])
# The app's rule for personality names (save_user_personality).
_PERSONALITY_NAME = vol.All(
    cv.string,
    # Accept the selection form the Conversation personality select shows.
    lambda v: v.strip().removeprefix("user_personalities/"),
    vol.Match(r"^[A-Za-z0-9_-]+$", msg="use only letters, numbers, - and _"),
)

PERSONALITY_NAME_SCHEMA = vol.Schema(
    {_DEVICE: _DEVICE_IDS, vol.Required("name"): cv.string}
)

SAVE_PERSONALITY_SCHEMA = vol.Schema(
    {
        _DEVICE: _DEVICE_IDS,
        vol.Required("name"): _PERSONALITY_NAME,
        vol.Optional("based_on"): cv.string,
        vol.Optional("instructions"): cv.string,
        vol.Optional("greeting"): cv.string,
        vol.Optional("voice"): cv.string,
        vol.Optional("tools"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("apply", default=False): cv.boolean,
    }
)

_PERSONALITY_SERVICES = (
    SERVICE_GET_PERSONALITY,
    SERVICE_SAVE_PERSONALITY,
    SERVICE_DELETE_PERSONALITY,
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

    async def _handle_start_conversation(call: ServiceCall) -> None:
        coordinators = _coordinators_for_devices(
            hass, call.data.get(ATTR_DEVICE_ID) or []
        )
        if not coordinators:
            raise ServiceValidationError("No Reachy Mini device targeted")

        for coordinator in coordinators:
            await coordinator.async_start_conversation(
                personality=call.data.get("personality"),
                voice=call.data.get("voice"),
                greeting=call.data.get("greeting"),
                instructions=call.data.get("instructions"),
                context=call.data.get("context"),
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_CONVERSATION,
        _handle_start_conversation,
        schema=START_CONVERSATION_SCHEMA,
    )

    def _single_coordinator(call: ServiceCall) -> ReachyMiniCoordinator:
        coordinators = _coordinators_for_devices(
            hass, call.data.get(ATTR_DEVICE_ID) or []
        )
        if len(coordinators) != 1:
            raise ServiceValidationError("Target exactly one Reachy Mini device")
        return coordinators[0]

    async def _handle_get_personality(call: ServiceCall) -> ServiceResponse:
        return await _single_coordinator(call).async_get_personality(call.data["name"])

    async def _handle_save_personality(call: ServiceCall) -> ServiceResponse:
        fields = {
            key: call.data[key]
            for key in ("based_on", "instructions", "greeting", "voice", "tools")
            if key in call.data
        }
        selection = await _single_coordinator(call).async_save_personality(
            call.data["name"], apply=call.data["apply"], **fields
        )
        return {"name": selection}

    async def _handle_delete_personality(call: ServiceCall) -> None:
        await _single_coordinator(call).async_delete_personality(call.data["name"])

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PERSONALITY,
        _handle_get_personality,
        schema=PERSONALITY_NAME_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE_PERSONALITY,
        _handle_save_personality,
        schema=SAVE_PERSONALITY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_PERSONALITY,
        _handle_delete_personality,
        schema=PERSONALITY_NAME_SCHEMA,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove Reachy Mini services. Called when the last entry unloads."""
    for service in (
        SERVICE_PLAY_RECORDED_MOVE,
        SERVICE_START_CONVERSATION,
        *_PERSONALITY_SERVICES,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
