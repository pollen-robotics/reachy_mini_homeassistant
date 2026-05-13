"""Sensor entities for Reachy Mini.

Each entity reads one key from the coordinator's unified dict, which
is assembled from several SDK REST endpoints (see ``coordinator.py``).
Field-by-field metadata (units, state class, icon) is declared once
in :data:`SENSORS`; the runtime entity class is a thin lookup shim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ReachyMiniCoordinator
from .entity import ReachyMiniEntity


@dataclass(frozen=True, kw_only=True)
class ReachyMiniSensorDescription(SensorEntityDescription):
    """Describe a Reachy Mini sensor and the JSON key it reads."""

    json_key: str


SENSORS: tuple[ReachyMiniSensorDescription, ...] = (
    ReachyMiniSensorDescription(
        key="active_app",
        translation_key="active_app",
        json_key="active_app",
        icon="mdi:robot-happy",
    ),
    ReachyMiniSensorDescription(
        key="active_app_transport",
        translation_key="active_app_transport",
        json_key="active_app_transport",
        icon="mdi:transit-connection-variant",
    ),
    ReachyMiniSensorDescription(
        key="doa_angle_rad",
        translation_key="voice_direction",
        json_key="doa_angle_rad",
        native_unit_of_measurement="rad",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:account-voice",
        suggested_display_precision=2,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one ReachyMiniSensor per entry in :data:`SENSORS`."""
    coordinator: ReachyMiniCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ReachyMiniSensor(coordinator, entry, desc) for desc in SENSORS
    )


class ReachyMiniSensor(ReachyMiniEntity, SensorEntity):
    """A single value from the aggregator endpoint."""

    entity_description: ReachyMiniSensorDescription

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
        description: ReachyMiniSensorDescription,
    ) -> None:
        """Wire the entity to its coordinator key."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Pluck the JSON key from the cached state, or None if absent."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.json_key)
