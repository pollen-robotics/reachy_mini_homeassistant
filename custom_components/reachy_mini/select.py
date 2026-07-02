"""Select entities for Reachy Mini.

Two kinds:

- ``Motor mode`` — writable read-back of the daemon's motor control
  mode. Picking an option POSTs to ``/api/motors/set_mode/{mode}``.
- ``Emotion`` / ``Dance`` — pick-only selects backed by the daemon's
  recorded-move catalogs (loaded once at setup via
  ``coordinator.async_load_move_lists``). Selecting does NOT play —
  that's the matching ``Play emotion`` / ``Play dance`` button's job.
  Survives HA restarts via ``RestoreEntity``.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DAEMON_STATE_RUNNING,
    DANCES_DATASET,
    DOMAIN,
    EMOTIONS_DATASET,
    ENDPOINT_MOTOR_SET_MODE,
    MOTOR_MODES,
)
from .coordinator import ReachyMiniCoordinator
from .entity import ReachyMiniEntity

MOTOR_MODE_DESCRIPTION = SelectEntityDescription(
    key="motor_mode",
    translation_key="motor_mode",
    options=list(MOTOR_MODES),
    icon="mdi:engine",
)


@dataclass(frozen=True, kw_only=True)
class ReachyMiniRecordedMoveSelectDescription(SelectEntityDescription):
    """Describe a per-dataset recorded-move select."""

    dataset: str


RECORDED_MOVE_SELECTS: tuple[ReachyMiniRecordedMoveSelectDescription, ...] = (
    ReachyMiniRecordedMoveSelectDescription(
        key="emotion",
        translation_key="emotion",
        name="Emotion",
        dataset=EMOTIONS_DATASET,
        icon="mdi:emoticon-happy-outline",
    ),
    ReachyMiniRecordedMoveSelectDescription(
        key="dance",
        translation_key="dance",
        name="Dance",
        dataset=DANCES_DATASET,
        icon="mdi:dance-ballroom",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the motor-mode select plus any populated recorded-move selects."""
    coordinator: ReachyMiniCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SelectEntity] = [ReachyMiniMotorModeSelect(coordinator, entry)]
    for description in RECORDED_MOVE_SELECTS:
        # Skip datasets the daemon didn't return moves for — keeps the
        # device card clean on older daemons or Lite installs.
        moves = coordinator.move_lists.get(description.dataset) or []
        if moves:
            entities.append(
                ReachyMiniRecordedMoveSelect(coordinator, entry, description)
            )
    async_add_entities(entities)


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
        # While the backend is stopped (the robot is asleep), the
        # set_mode endpoint answers 503 "Backend not running" — turn
        # that into an actionable message instead.
        data = self.coordinator.data or {}
        if data.get("daemon_state") != DAEMON_STATE_RUNNING:
            raise HomeAssistantError(
                "Cannot set motor mode while Reachy Mini is asleep "
                "(daemon backend stopped) — press Wake up first"
            )
        path = ENDPOINT_MOTOR_SET_MODE.format(mode=option)
        await self.coordinator.async_post(path)


class ReachyMiniRecordedMoveSelect(ReachyMiniEntity, SelectEntity, RestoreEntity):
    """Pick-only select tied to a recorded-move dataset.

    Selecting an option mutates ``coordinator.selected_move`` and
    nothing else. The matching ``Play <category>`` button reads that
    state and POSTs to the play endpoint when pressed.
    """

    entity_description: ReachyMiniRecordedMoveSelectDescription

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
        description: ReachyMiniRecordedMoveSelectDescription,
    ) -> None:
        """Bind to the dataset's move list captured at setup time."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description
        self._attr_options = list(coordinator.move_lists[description.dataset])

    @property
    def current_option(self) -> str | None:
        """Read the user's last pick from the coordinator's in-memory map."""
        return self.coordinator.selected_move.get(self.entity_description.dataset)

    async def async_select_option(self, option: str) -> None:
        """Stash the pick on the coordinator; no HTTP call."""
        if option not in self._attr_options:
            return
        self.coordinator.selected_move[self.entity_description.dataset] = option
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore the last pick if it's still a valid option."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if (
            last_state is not None
            and last_state.state in self._attr_options
        ):
            self.coordinator.selected_move[
                self.entity_description.dataset
            ] = last_state.state
