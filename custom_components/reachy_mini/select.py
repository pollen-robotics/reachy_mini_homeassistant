"""Select entities for Reachy Mini.

Currently one entity: ``Motor mode``. Replaces the read-only sensor
that previously surfaced ``motor_mode`` — same value, now writable as
a dropdown. Picking an option POSTs to ``/api/motors/set_mode/{mode}``;
the coordinator then re-polls so the dropdown snaps to whatever the
daemon actually applied (it shouldn't deviate, but the round-trip
keeps the UI honest).
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENDPOINT_MOTOR_SET_MODE, MOTOR_MODES
from .coordinator import ReachyMiniCoordinator
from .entity import ReachyMiniEntity

MOTOR_MODE_DESCRIPTION = SelectEntityDescription(
    key="motor_mode",
    translation_key="motor_mode",
    options=list(MOTOR_MODES),
    icon="mdi:engine",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the motor-mode select."""
    coordinator: ReachyMiniCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ReachyMiniMotorModeSelect(coordinator, entry)])


class ReachyMiniMotorModeSelect(ReachyMiniEntity, SelectEntity):
    """Read + write motor control mode."""

    entity_description = MOTOR_MODE_DESCRIPTION

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Wire the entity to the motor_mode key in the coordinator dict."""
        super().__init__(coordinator, entry, MOTOR_MODE_DESCRIPTION.key)

    @property
    def current_option(self) -> str | None:
        """Read the latest motor mode the coordinator polled."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get("motor_mode")
        # Daemon may return an unknown / new enum value before the
        # integration is updated; treat as `None` to avoid HA warning
        # the user about an out-of-options state.
        if value not in MOTOR_MODES:
            return None
        return value

    async def async_select_option(self, option: str) -> None:
        """POST the selected mode to the daemon."""
        path = ENDPOINT_MOTOR_SET_MODE.format(mode=option)
        await self.coordinator.async_post(path)
