"""Controllable number entities for Reachy Mini — speaker + mic volume.

Render as 0-100 % sliders in the HA UI. Reads come from the
coordinator's cached state (same as the read-only path used to);
writes POST to the daemon's existing volume routes and immediately
trigger a coordinator refresh so the slider snaps to the value the
robot actually applied (the daemon may clamp / round).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp
from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_TIMEOUT,
    DOMAIN,
    ENDPOINT_VOLUME_MIC_SET,
    ENDPOINT_VOLUME_SPEAKER_SET,
)
from .coordinator import ReachyMiniCoordinator
from .entity import ReachyMiniEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ReachyMiniVolumeDescription(NumberEntityDescription):
    """Describe a Reachy Mini volume slider and where it reads/writes."""

    # Key in the coordinator's data dict that holds the current value.
    json_key: str
    # SDK POST path for setting the value (relative to base_url).
    set_path: str


NUMBERS: tuple[ReachyMiniVolumeDescription, ...] = (
    ReachyMiniVolumeDescription(
        key="speaker_volume",
        translation_key="speaker_volume",
        json_key="speaker_volume",
        set_path=ENDPOINT_VOLUME_SPEAKER_SET,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        icon="mdi:volume-high",
    ),
    ReachyMiniVolumeDescription(
        key="mic_volume",
        translation_key="mic_volume",
        json_key="mic_volume",
        set_path=ENDPOINT_VOLUME_MIC_SET,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        icon="mdi:microphone",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one ReachyMiniVolumeNumber per entry in :data:`NUMBERS`."""
    coordinator: ReachyMiniCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ReachyMiniVolumeNumber(coordinator, entry, desc) for desc in NUMBERS
    )


class ReachyMiniVolumeNumber(ReachyMiniEntity, NumberEntity):
    """A 0-100 % volume slider backed by an SDK POST endpoint."""

    entity_description: ReachyMiniVolumeDescription

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
        description: ReachyMiniVolumeDescription,
    ) -> None:
        """Wire the entity to its coordinator key + write path."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Read the cached volume from the coordinator dict."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.entity_description.json_key)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """POST the new volume to the daemon and refresh the coordinator.

        The daemon may clamp or round the requested value (it returns
        the applied value in the response); we don't try to predict
        that — just request an immediate coordinator refresh so the
        slider snaps to whatever the daemon decided.
        """
        url = f"{self.coordinator.base_url}{self.entity_description.set_path}"
        body = {"volume": int(value)}
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                url,
                json=body,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning(
                "Failed to set %s on Reachy Mini: %s",
                self.entity_description.key,
                err,
            )
            raise

        # Snap the UI to the value the daemon actually applied.
        await self.coordinator.async_request_refresh()
