"""One-shot action buttons for Reachy Mini.

Each button POSTs to a single SDK endpoint when pressed and otherwise
has no state. Pairs with the read-only sensors / binary sensors that
expose the *result* of those actions (e.g. the "Awake" binary sensor
flips after the "Wake up" button is pressed).
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.exceptions import HomeAssistantError

from .const import (
    DAEMON_STATE_RUNNING,
    DANCES_DATASET,
    DOMAIN,
    EMOTIONS_DATASET,
    ENDPOINT_APP_RESTART_CURRENT,
    ENDPOINT_APP_STOP_CURRENT,
    ENDPOINT_DAEMON_RESTART,
    ENDPOINT_DAEMON_START_WAKE,
    ENDPOINT_DAEMON_STOP_SLEEP,
    ENDPOINT_MOTOR_SET_MODE,
    ENDPOINT_MOVE_WAKE_UP,
    ENDPOINT_VOLUME_TEST_SOUND,
)
from .coordinator import ReachyMiniCoordinator
from .entity import ReachyMiniEntity


@dataclass(frozen=True, kw_only=True)
class ReachyMiniButtonDescription(ButtonEntityDescription):
    """Describe an action button and the SDK endpoint it POSTs to."""

    post_path: str


WAKE_UP_DESCRIPTION = ButtonEntityDescription(
    key="wake_up",
    translation_key="wake_up",
    icon="mdi:weather-sunny",
)

GOTO_SLEEP_DESCRIPTION = ButtonEntityDescription(
    key="goto_sleep",
    translation_key="goto_sleep",
    icon="mdi:sleep",
)

BUTTONS: tuple[ReachyMiniButtonDescription, ...] = (
    ReachyMiniButtonDescription(
        key="stop_current_app",
        translation_key="stop_current_app",
        post_path=ENDPOINT_APP_STOP_CURRENT,
        icon="mdi:close-octagon",
    ),
    ReachyMiniButtonDescription(
        key="restart_current_app",
        translation_key="restart_current_app",
        post_path=ENDPOINT_APP_RESTART_CURRENT,
        icon="mdi:restart",
    ),
    ReachyMiniButtonDescription(
        key="play_test_sound",
        translation_key="play_test_sound",
        post_path=ENDPOINT_VOLUME_TEST_SOUND,
        icon="mdi:speaker-message",
    ),
    ReachyMiniButtonDescription(
        key="restart_daemon",
        translation_key="restart_daemon",
        post_path=ENDPOINT_DAEMON_RESTART,
        device_class=ButtonDeviceClass.RESTART,
        icon="mdi:server-network",
    ),
)


@dataclass(frozen=True, kw_only=True)
class ReachyMiniPlayMoveButtonDescription(ButtonEntityDescription):
    """Describe a play-move button bound to a specific dataset."""

    dataset: str


PLAY_MOVE_BUTTONS: tuple[ReachyMiniPlayMoveButtonDescription, ...] = (
    ReachyMiniPlayMoveButtonDescription(
        key="play_emotion",
        translation_key="play_emotion",
        name="Play emotion",
        dataset=EMOTIONS_DATASET,
        icon="mdi:play-circle-outline",
    ),
    ReachyMiniPlayMoveButtonDescription(
        key="play_dance",
        translation_key="play_dance",
        name="Play dance",
        dataset=DANCES_DATASET,
        icon="mdi:play-circle-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create static action buttons plus any populated play-move buttons."""
    coordinator: ReachyMiniCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ButtonEntity] = [
        ReachyMiniWakeUpButton(coordinator, entry),
        ReachyMiniGotoSleepButton(coordinator, entry),
        *(ReachyMiniButton(coordinator, entry, desc) for desc in BUTTONS),
    ]
    for description in PLAY_MOVE_BUTTONS:
        # Mirror the select gate: only create a play button if the
        # dataset's move list was populated at setup time.
        moves = coordinator.move_lists.get(description.dataset) or []
        if moves:
            entities.append(
                ReachyMiniPlayMoveButton(coordinator, entry, description)
            )
    async_add_entities(entities)


class ReachyMiniButton(ReachyMiniEntity, ButtonEntity):
    """A one-shot action POSTed to the SDK when pressed."""

    entity_description: ReachyMiniButtonDescription

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
        description: ReachyMiniButtonDescription,
    ) -> None:
        """Wire the button to its POST endpoint."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """POST to the button's endpoint; coordinator refreshes on success."""
        await self.coordinator.async_post(self.entity_description.post_path)


class ReachyMiniWakeUpButton(ReachyMiniEntity, ButtonEntity):
    """Wake the robot regardless of which sleep state it is in.

    On the Wireless unit "asleep" usually means the daemon backend is
    fully stopped, so the wake move endpoint would 503 — waking from
    that state goes through the daemon start endpoint (which enables
    motor torque before playing the wake move, like the dashboard).
    When the backend is already running the daemon start endpoint is a
    no-op, so instead enable torque explicitly — the bare wake move
    never does, which is how you get the wake sound with no motion —
    then play the move.
    """

    entity_description = WAKE_UP_DESCRIPTION

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Wire the button to the coordinator."""
        super().__init__(coordinator, entry, WAKE_UP_DESCRIPTION.key)

    async def async_press(self) -> None:
        """Start the daemon if stopped, else enable motors and play wake."""
        data = self.coordinator.data or {}
        if data.get("daemon_state") == DAEMON_STATE_RUNNING:
            await self.coordinator.async_post(
                ENDPOINT_MOTOR_SET_MODE.format(mode="enabled")
            )
            await self.coordinator.async_post(ENDPOINT_MOVE_WAKE_UP)
        else:
            await self.coordinator.async_post(ENDPOINT_DAEMON_START_WAKE)


class ReachyMiniGotoSleepButton(ReachyMiniEntity, ButtonEntity):
    """Put the robot to sleep the way the dashboard does.

    The daemon stop endpoint plays the sleep move, disables motor
    torque and stops the backend — the bare goto_sleep move endpoint
    does only the move, leaving torque on and the robot "awake".
    """

    entity_description = GOTO_SLEEP_DESCRIPTION

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Wire the button to the coordinator."""
        super().__init__(coordinator, entry, GOTO_SLEEP_DESCRIPTION.key)

    async def async_press(self) -> None:
        """Stop the daemon with goto_sleep; explain if already asleep."""
        data = self.coordinator.data or {}
        if data.get("daemon_state") != DAEMON_STATE_RUNNING:
            raise HomeAssistantError(
                "Reachy Mini is already asleep (daemon backend stopped)"
            )
        await self.coordinator.async_post(ENDPOINT_DAEMON_STOP_SLEEP)


class ReachyMiniPlayMoveButton(ReachyMiniEntity, ButtonEntity):
    """Plays the currently-selected move from a dataset on press."""

    entity_description: ReachyMiniPlayMoveButtonDescription

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
        description: ReachyMiniPlayMoveButtonDescription,
    ) -> None:
        """Wire the button to its dataset."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Read the matching select's pick and POST to the play endpoint."""
        dataset = self.entity_description.dataset
        move = self.coordinator.selected_move.get(dataset)
        if not move:
            raise HomeAssistantError(
                f"No {self.entity_description.name.lower()} selected"
            )
        await self.coordinator.async_play_recorded_move(dataset, move)
