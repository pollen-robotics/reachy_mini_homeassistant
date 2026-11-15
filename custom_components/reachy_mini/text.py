"""Text entities for Reachy Mini.

``Conversation instructions``, ``Conversation context`` and
``Conversation greeting`` — free-text settings read by the ``Start
conversation`` button (same meaning as the ``instructions`` /
``context`` / ``greeting`` fields of the
``reachy_mini.start_conversation`` service action). Pick-only like the
move / app selects: editing stores the value on the coordinator and
makes no HTTP call. Survive HA restarts via ``RestoreEntity``.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.text import TextEntity, TextEntityDescription, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONVERSATION_APP_NAME, DOMAIN
from .coordinator import ReachyMiniCoordinator
from .entity import ReachyMiniEntity

# HA caps entity states at 255 characters. Longer instructions can still
# be passed through the start_conversation service action.
MAX_TEXT_LENGTH = 255


@dataclass(frozen=True, kw_only=True)
class ReachyMiniConversationTextDescription(TextEntityDescription):
    """Describe a conversation text setting and its coordinator attribute."""

    attr: str


CONVERSATION_TEXTS: tuple[ReachyMiniConversationTextDescription, ...] = (
    ReachyMiniConversationTextDescription(
        key="conversation_instructions",
        translation_key="conversation_instructions",
        attr="conversation_instructions",
        icon="mdi:script-text-outline",
        mode=TextMode.TEXT,
        native_max=MAX_TEXT_LENGTH,
    ),
    ReachyMiniConversationTextDescription(
        key="conversation_context",
        translation_key="conversation_context",
        attr="conversation_context",
        icon="mdi:information-outline",
        mode=TextMode.TEXT,
        native_max=MAX_TEXT_LENGTH,
    ),
    ReachyMiniConversationTextDescription(
        key="conversation_greeting",
        translation_key="conversation_greeting",
        attr="conversation_greeting",
        icon="mdi:hand-wave-outline",
        mode=TextMode.TEXT,
        native_max=MAX_TEXT_LENGTH,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the conversation text settings when the app is installed."""
    coordinator: ReachyMiniCoordinator = hass.data[DOMAIN][entry.entry_id]
    if CONVERSATION_APP_NAME not in coordinator.available_apps:
        return
    async_add_entities(
        ReachyMiniConversationText(coordinator, entry, description)
        for description in CONVERSATION_TEXTS
    )


class ReachyMiniConversationText(ReachyMiniEntity, TextEntity, RestoreEntity):
    """A free-text conversation setting stored on the coordinator."""

    entity_description: ReachyMiniConversationTextDescription

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
        description: ReachyMiniConversationTextDescription,
    ) -> None:
        """Bind to the coordinator attribute named in the description."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str:
        """Current text; empty means "not set"."""
        return getattr(self.coordinator, self.entity_description.attr)

    async def async_set_value(self, value: str) -> None:
        """Store the text on the coordinator; no HTTP call."""
        setattr(self.coordinator, self.entity_description.attr, value.strip())
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore the last value."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            setattr(self.coordinator, self.entity_description.attr, last_state.state)
