"""Sensor entities for Reachy Mini.

Each entity reads one key from the daemon's `/api/homeassistant/state`
payload. Field-by-field metadata (units, device class, state class,
icon) is declared once in :data:`SENSORS`; the runtime entity class
is a thin lookup-by-key shim on top of the coordinator's cached dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
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
        key="motor_mode",
        translation_key="motor_mode",
        json_key="motor_mode",
        icon="mdi:engine",
    ),
    ReachyMiniSensorDescription(
        key="cpu_pct",
        translation_key="cpu",
        json_key="cpu_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cpu-64-bit",
        suggested_display_precision=1,
    ),
    ReachyMiniSensorDescription(
        key="mem_pct",
        translation_key="memory",
        json_key="mem_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:memory",
        suggested_display_precision=1,
    ),
    ReachyMiniSensorDescription(
        key="speaker_volume",
        translation_key="speaker_volume",
        json_key="speaker_volume",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:volume-high",
    ),
    ReachyMiniSensorDescription(
        key="mic_volume",
        translation_key="mic_volume",
        json_key="mic_volume",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:microphone",
    ),
    ReachyMiniSensorDescription(
        key="imu_temp_celsius",
        translation_key="imu_temp",
        json_key="imu_temp_celsius",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ReachyMiniSensorDescription(
        key="imu_pitch_deg",
        translation_key="imu_pitch",
        json_key="imu_pitch_deg",
        native_unit_of_measurement="°",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:angle-acute",
        suggested_display_precision=1,
    ),
    ReachyMiniSensorDescription(
        key="imu_roll_deg",
        translation_key="imu_roll",
        json_key="imu_roll_deg",
        native_unit_of_measurement="°",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:angle-acute",
        suggested_display_precision=1,
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
    ReachyMiniSensorDescription(
        key="uptime_seconds",
        translation_key="uptime",
        json_key="uptime_seconds",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:timer-outline",
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
