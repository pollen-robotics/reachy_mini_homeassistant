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

from .const import (
    DOMAIN,
    ENDPOINT_APP_RESTART_CURRENT,
    ENDPOINT_APP_STOP_CURRENT,
    ENDPOINT_DAEMON_RESTART,
    ENDPOINT_MOVE_GOTO_SLEEP,
    ENDPOINT_MOVE_WAKE_UP,
    ENDPOINT_VOLUME_TEST_SOUND,
)
from .coordinator import ReachyMiniCoordinator
from .entity import ReachyMiniEntity


@dataclass(frozen=True, kw_only=True)
class ReachyMiniButtonDescription(ButtonEntityDescription):
    """Describe an action button and the SDK endpoint it POSTs to."""

    post_path: str


BUTTONS: tuple[ReachyMiniButtonDescription, ...] = (
    ReachyMiniButtonDescription(
        key="wake_up",
        translation_key="wake_up",
        post_path=ENDPOINT_MOVE_WAKE_UP,
        icon="mdi:weather-sunny",
    ),
    ReachyMiniButtonDescription(
        key="goto_sleep",
        translation_key="goto_sleep",
        post_path=ENDPOINT_MOVE_GOTO_SLEEP,
        icon="mdi:sleep",
    ),
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one ReachyMiniButton per entry in :data:`BUTTONS`."""
    coordinator: ReachyMiniCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ReachyMiniButton(coordinator, entry, desc) for desc in BUTTONS
    )


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
