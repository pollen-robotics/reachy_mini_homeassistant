"""Binary sensor entities for Reachy Mini.

Same shape as :mod:`.sensor` but for boolean-typed JSON fields.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ReachyMiniCoordinator
from .entity import ReachyMiniEntity


@dataclass(frozen=True, kw_only=True)
class ReachyMiniBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Reachy Mini binary sensor and the JSON key it reads."""

    json_key: str


BINARY_SENSORS: tuple[ReachyMiniBinarySensorDescription, ...] = (
    ReachyMiniBinarySensorDescription(
        key="awake",
        translation_key="awake",
        json_key="awake",
        device_class=BinarySensorDeviceClass.POWER,
    ),
    ReachyMiniBinarySensorDescription(
        key="webrtc_active",
        translation_key="webrtc_active",
        json_key="webrtc_active",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
    ReachyMiniBinarySensorDescription(
        key="doa_speech_detected",
        translation_key="speech_detected",
        json_key="doa_speech_detected",
        device_class=BinarySensorDeviceClass.SOUND,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one ReachyMiniBinarySensor per entry in :data:`BINARY_SENSORS`."""
    coordinator: ReachyMiniCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ReachyMiniBinarySensor(coordinator, entry, desc)
        for desc in BINARY_SENSORS
    )


class ReachyMiniBinarySensor(ReachyMiniEntity, BinarySensorEntity):
    """Boolean view of one JSON key."""

    entity_description: ReachyMiniBinarySensorDescription

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
        description: ReachyMiniBinarySensorDescription,
    ) -> None:
        """Wire the entity to its coordinator key."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the boolean value, or None if unavailable."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.entity_description.json_key)
        if value is None:
            return None
        return bool(value)
