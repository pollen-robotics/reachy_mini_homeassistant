"""Start-conversation flow: daemon wake / app launch + the app's JSON-RPC.

The daemon REST calls go through ``aioclient_mock``; the conversation
app's ``/rpc`` WebSocket is replaced by :class:`FakeRpcSocket`, which
answers each request from a per-method handler.
"""

from __future__ import annotations

import json
from typing import Any, Callable
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.reachy_mini import conversation
from custom_components.reachy_mini.button import ReachyMiniStartConversationButton
from custom_components.reachy_mini.select import ReachyMiniPersonalitySelect
from custom_components.reachy_mini.text import CONVERSATION_TEXTS, ReachyMiniConversationText
from pytest_homeassistant_custom_component.common import MockEntityPlatform
from custom_components.reachy_mini.const import CONVERSATION_APP_NAME, DOMAIN
from custom_components.reachy_mini.conversation import (
    ConversationClient,
    build_opening_message,
)

from .conftest import BASE_URL, TEST_HOST

START_PATH = f"/api/apps/start-app/{CONVERSATION_APP_NAME}"


class _Msg:
    def __init__(self, data: str) -> None:
        self.type = aiohttp.WSMsgType.TEXT
        self.data = data


class FakeRpcSocket:
    """Stands in for the app's /rpc socket; records every request."""

    def __init__(self, handlers: dict[str, Callable[[dict[str, Any]], Any]]) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._outbox: list[str] = []
        self.closed = False

    async def send_str(self, raw: str) -> None:
        req = json.loads(raw)
        self.calls.append((req["method"], req["params"]))
        # A notification in between, like the app's live event stream.
        self._outbox.append(json.dumps({"jsonrpc": "2.0", "method": "conversation.turn", "params": {}}))
        try:
            result = self.handlers[req["method"]](req["params"])
            reply = {"jsonrpc": "2.0", "id": req["id"], "result": result}
        except _RpcFail as err:
            reply = {
                "jsonrpc": "2.0",
                "id": req["id"],
                "error": {"code": -32000, "message": err.reason, "data": {"reason": err.reason}},
            }
        self._outbox.append(json.dumps(reply))

    async def receive(self) -> _Msg:
        return _Msg(self._outbox.pop(0))

    async def close(self) -> None:
        self.closed = True


class _RpcFail(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _connected(_params: dict[str, Any]) -> dict[str, Any]:
    return {"backend_connected": True, "backend_connection_state": "connected"}


@pytest.fixture(autouse=True)
def _no_retry_delay():
    with patch.object(conversation, "_RETRY_INTERVAL", 0):
        yield


def _posts(aioclient_mock) -> list[str]:
    return [url.path for method, url, *_ in aioclient_mock.mock_calls if method == "POST"]


async def test_starts_app_when_slot_free(hass, coordinator, aioclient_mock) -> None:
    """Awake robot, nothing running: just POST start-app, no RPC."""
    aioclient_mock.get(f"{BASE_URL}/api/daemon/status", json={"state": "running"})
    aioclient_mock.get(f"{BASE_URL}/api/daemon/robot-app-lock-status", json={"state": "free"})
    aioclient_mock.post(f"{BASE_URL}{START_PATH}", json={})

    with (
        patch.object(coordinator, "async_request_refresh", AsyncMock()),
        patch.object(coordinator, "async_load_personalities", AsyncMock()) as load,
    ):
        await coordinator.async_start_conversation()

    load.assert_awaited_once()
    assert _posts(aioclient_mock) == [START_PATH]


async def test_wakes_daemon_then_starts_app(hass, coordinator, aioclient_mock) -> None:
    """Stopped backend (Wireless sleep): start the daemon, wait, then start-app."""
    aioclient_mock.get(f"{BASE_URL}/api/daemon/status", json={"state": "stopped"})
    aioclient_mock.get(f"{BASE_URL}/api/daemon/robot-app-lock-status", json={"state": "free"})
    aioclient_mock.post(f"{BASE_URL}/api/daemon/start", json={"job_id": "1"})
    aioclient_mock.post(f"{BASE_URL}{START_PATH}", json={})

    async def _running() -> None:
        return None

    with (
        patch.object(coordinator, "async_request_refresh", AsyncMock()),
        patch.object(coordinator, "_async_wait_daemon_running", side_effect=_running) as wait,
        patch.object(coordinator, "async_load_personalities", AsyncMock()),
    ):
        await coordinator.async_start_conversation()

    wait.assert_awaited_once()
    assert _posts(aioclient_mock) == ["/api/daemon/start", START_PATH]


async def test_stops_other_local_app_first(hass, coordinator, aioclient_mock) -> None:
    """The daemon refuses a second app, so another local app is stopped first."""
    aioclient_mock.get(f"{BASE_URL}/api/daemon/status", json={"state": "running"})
    aioclient_mock.get(
        f"{BASE_URL}/api/daemon/robot-app-lock-status",
        json={"state": "local_app", "holder_name": "other_app"},
    )
    aioclient_mock.post(f"{BASE_URL}/api/apps/stop-current-app", json=None)
    aioclient_mock.post(f"{BASE_URL}{START_PATH}", json={})

    with (
        patch.object(coordinator, "async_request_refresh", AsyncMock()),
        patch.object(coordinator, "async_load_personalities", AsyncMock()) as load,
    ):
        await coordinator.async_start_conversation()

    load.assert_awaited_once()
    assert _posts(aioclient_mock) == ["/api/apps/stop-current-app", START_PATH]


async def test_already_running_configures_session(hass, coordinator, aioclient_mock) -> None:
    """App already up: no restart; voice, personality, then greeting over RPC."""
    aioclient_mock.get(f"{BASE_URL}/api/daemon/status", json={"state": "running"})
    aioclient_mock.get(
        f"{BASE_URL}/api/daemon/robot-app-lock-status",
        json={"state": "local_app", "holder_name": CONVERSATION_APP_NAME},
    )
    ws = FakeRpcSocket(
        {
            "conversation.status": _connected,
            "voices.apply": lambda p: {"ok": True},
            "personalities.apply": lambda p: {"ok": True},
            "conversation.say": lambda p: {"ok": True},
            "personalities.list": lambda p: {"choices": ["default"], "current": "default"},
        }
    )

    with patch.object(coordinator._session, "ws_connect", AsyncMock(return_value=ws)) as connect:
        await coordinator.async_start_conversation(
            personality="victorian_butler", voice="alloy", greeting="Hello!"
        )

    assert connect.call_args.args[0] == f"ws://{TEST_HOST}:7860/rpc"
    assert _posts(aioclient_mock) == []
    assert ws.calls == [
        ("conversation.status", {}),
        ("voices.apply", {"voice": "alloy"}),
        ("personalities.apply", {"name": "victorian_butler"}),
        ("conversation.status", {}),
        ("conversation.say", {"text": build_opening_message(None, "Hello!")}),
        # Catalog refresh after the start (second connection).
        ("personalities.list", {}),
    ]
    assert ws.closed


async def test_client_retries_until_app_ready(hass) -> None:
    """Connect refused, backend connecting, say not_running — all retried."""
    status = iter([False, True, True])
    say_attempts = iter([_RpcFail("not_running"), None])

    def _status(_params: dict[str, Any]) -> dict[str, Any]:
        return {"backend_connected": next(status)}

    def _say(_params: dict[str, Any]) -> dict[str, Any]:
        if err := next(say_attempts):
            raise err
        return {"ok": True}

    ws = FakeRpcSocket({"conversation.status": _status, "conversation.say": _say})
    session = AsyncMock()
    session.ws_connect.side_effect = [aiohttp.ClientConnectionError("refused"), ws]

    await ConversationClient(session, TEST_HOST, timeout=30).async_run(greeting="Hi")

    assert session.ws_connect.await_count == 2
    assert [m for m, _ in ws.calls] == [
        "conversation.status",
        "conversation.status",
        "conversation.status",
        "conversation.say",
        "conversation.say",
    ]


async def test_rpc_error_surfaces_as_ha_error(hass, coordinator, aioclient_mock) -> None:
    """A non-retryable app error (unknown profile) fails the action clearly."""
    aioclient_mock.get(f"{BASE_URL}/api/daemon/status", json={"state": "running"})
    aioclient_mock.get(
        f"{BASE_URL}/api/daemon/robot-app-lock-status",
        json={"state": "local_app", "holder_name": CONVERSATION_APP_NAME},
    )

    def _fail(_params: dict[str, Any]) -> None:
        raise _RpcFail("profile_apply_failed")

    ws = FakeRpcSocket({"conversation.status": _connected, "personalities.apply": _fail})

    with (
        patch.object(coordinator._session, "ws_connect", AsyncMock(return_value=ws)),
        pytest.raises(HomeAssistantError, match="profile_apply_failed"),
    ):
        await coordinator.async_start_conversation(personality="nope")

    assert ws.closed


async def test_button_starts_conversation(hass, coordinator, config_entry) -> None:
    """The button starts a session with the app's saved settings."""
    button = ReachyMiniStartConversationButton(coordinator, config_entry)

    coordinator.selected_personality = "victorian_butler"
    coordinator.conversation_instructions = "Speak Spanish."
    coordinator.conversation_context = ""
    coordinator.conversation_greeting = "Good evening!"

    with patch.object(coordinator, "async_start_conversation", AsyncMock()) as start:
        await button.async_press()

    start.assert_awaited_once_with(
        personality="victorian_butler",
        instructions="Speak Spanish.",
        context=None,
        greeting="Good evening!",
    )


def test_opening_message_frames_context_and_greeting() -> None:
    """say() is answered by the model, so both are phrased as requests."""
    assert build_opening_message(None, None) is None
    assert build_opening_message(None, "Hi!") == (
        "Open the conversation now by saying, in character: Hi!"
    )
    only_context = build_opening_message("Anna is home.", None)
    assert only_context.startswith(
        "Context for this conversation (from Home Assistant): Anna is home."
    )
    assert "takes this context into account" in only_context
    both = build_opening_message("Anna is home.", "Welcome back!")
    assert both == (
        "Context for this conversation (from Home Assistant): Anna is home.\n\n"
        "Open the conversation now by saying, in character: Welcome back!"
    )


def _profile_handlers(saved: list[dict[str, Any]], base_instructions: str) -> dict:
    return {
        "conversation.status": _connected,
        "personalities.list": lambda p: {"current": "user_personalities/home_assistant_session"},
        "personalities.load": lambda p: {
            "instructions": base_instructions,
            "greeting": "Say hi.",
            "voice": "ballad",
            "uses_default_voice": False,
            "tools_text": "dance\ncamera\n",
        },
        "personalities.save": lambda p: (
            saved.append(p) or {"ok": True, "value": "user_personalities/home_assistant_session"}
        ),
        "personalities.apply": lambda p: {"ok": True},
        "conversation.say": lambda p: {"ok": True},
    }


async def test_instructions_saved_on_top_of_chosen_personality(hass) -> None:
    """Instructions extend the base profile, saved and force-applied."""
    saved: list[dict[str, Any]] = []
    ws = FakeRpcSocket(_profile_handlers(saved, "You are a butler."))
    session = AsyncMock()
    session.ws_connect.return_value = ws

    await ConversationClient(session, TEST_HOST, timeout=30).async_run(
        personality="victorian_butler",
        instructions="Speak Spanish.",
        context="Anna is home.",
    )

    assert [m for m, _ in ws.calls] == [
        "conversation.status",
        "personalities.load",
        "personalities.save",
        "personalities.apply",
        "conversation.status",
        "conversation.say",
    ]
    assert ws.calls[1] == ("personalities.load", {"name": "victorian_butler"})
    assert saved == [
        {
            "name": "home_assistant_session",
            "instructions": (
                "You are a butler."
                "\n\n## Session instructions (from Home Assistant)\n"
                "Speak Spanish."
            ),
            "tools_text": "dance\ncamera\n",
            "overwrite": True,
            "greeting": "Say hi.",
            "voice": "ballad",
        }
    ]
    assert ws.calls[3] == (
        "personalities.apply",
        {"name": "user_personalities/home_assistant_session", "force": True},
    )
    assert ws.calls[5] == (
        "conversation.say",
        {"text": build_opening_message("Anna is home.", None)},
    )


async def test_instructions_replace_previous_session_instructions(hass) -> None:
    """No personality given and the session profile is current: no stacking."""
    saved: list[dict[str, Any]] = []
    previous = (
        "You are a butler."
        "\n\n## Session instructions (from Home Assistant)\n"
        "Speak Spanish."
    )
    ws = FakeRpcSocket(_profile_handlers(saved, previous))
    session = AsyncMock()
    session.ws_connect.return_value = ws

    await ConversationClient(session, TEST_HOST, timeout=30).async_run(
        instructions="Speak French."
    )

    assert ws.calls[1] == ("personalities.list", {})
    assert ws.calls[2] == (
        "personalities.load",
        {"name": "user_personalities/home_assistant_session"},
    )
    assert saved[0]["instructions"] == (
        "You are a butler."
        "\n\n## Session instructions (from Home Assistant)\n"
        "Speak French."
    )
    assert "conversation.say" not in [m for m, _ in ws.calls]


async def test_load_personalities_hides_session_profile(hass, coordinator) -> None:
    """The catalog drops home_assistant_session and defaults to the current one."""
    ws = FakeRpcSocket(
        {
            "personalities.list": lambda p: {
                "choices": [
                    "default",
                    "victorian_butler",
                    "user_personalities/home_assistant_session",
                ],
                "current": "victorian_butler",
            }
        }
    )

    with patch.object(coordinator._session, "ws_connect", AsyncMock(return_value=ws)):
        await coordinator.async_load_personalities()

    assert coordinator.personalities == ["default", "victorian_butler"]
    assert coordinator.selected_personality == "victorian_butler"


async def test_load_personalities_app_not_running(hass, coordinator) -> None:
    """App down: keep what we had, no error."""
    with patch.object(
        coordinator._session,
        "ws_connect",
        AsyncMock(side_effect=aiohttp.ClientConnectionError("refused")),
    ):
        await coordinator.async_load_personalities(timeout=0)

    assert coordinator.personalities == []
    assert coordinator.selected_personality is None


async def test_personality_select(hass, coordinator, config_entry) -> None:
    """Before the catalog loads only the pick is offered; then the full list."""
    select = ReachyMiniPersonalitySelect(coordinator, config_entry)
    platform = MockEntityPlatform(hass, domain="select", platform_name=DOMAIN)
    await platform.async_add_entities([select])

    assert select.options == ["default"]
    assert select.current_option == "default"

    coordinator.personalities = ["default", "victorian_butler"]
    await select.async_select_option("victorian_butler")
    assert coordinator.selected_personality == "victorian_butler"

    await select.async_select_option("not_a_profile")
    assert coordinator.selected_personality == "victorian_butler"


async def test_conversation_texts_store_on_coordinator(
    hass, coordinator, config_entry, aioclient_mock
) -> None:
    """Editing the text entities only updates coordinator state."""
    texts = [ReachyMiniConversationText(coordinator, config_entry, d) for d in CONVERSATION_TEXTS]
    platform = MockEntityPlatform(hass, domain="text", platform_name=DOMAIN)
    await platform.async_add_entities(texts)
    instructions, context, greeting = texts

    await instructions.async_set_value("  Speak Spanish.  ")
    await context.async_set_value("Anna is home.")
    await greeting.async_set_value("Welcome back!")

    assert coordinator.conversation_instructions == "Speak Spanish."
    assert coordinator.conversation_context == "Anna is home."
    assert coordinator.conversation_greeting == "Welcome back!"
    assert instructions.native_value == "Speak Spanish."
    assert not aioclient_mock.mock_calls


_RUNNING_LOCK = {"state": "local_app", "holder_name": CONVERSATION_APP_NAME}
_BUTLER = {
    "instructions": "You are a butler.",
    "greeting": "Bow.",
    "voice": "Ryan",
    "uses_default_voice": False,
    "tools_text": "dance\ncamera\n",
    "enabled_tools": ["dance", "camera"],
    "available_tools": ["dance", "camera", "move_head"],
}


def _manage_socket(saved: list[dict[str, Any]], choices: list[str]) -> FakeRpcSocket:
    return FakeRpcSocket(
        {
            "personalities.list": lambda p: {"choices": choices, "current": "default"},
            "personalities.load": lambda p: dict(_BUTLER),
            "personalities.save": lambda p: (
                saved.append(p) or {"ok": True, "value": f"user_personalities/{p['name']}"}
            ),
            "personalities.apply": lambda p: {"ok": True},
            "personalities.delete": lambda p: {"ok": True},
        }
    )


async def test_save_personality_customizes_bundled_copy(hass, coordinator, aioclient_mock) -> None:
    """based_on fills in what isn't overridden; apply forces the switch."""
    aioclient_mock.get(f"{BASE_URL}/api/daemon/robot-app-lock-status", json=_RUNNING_LOCK)
    saved: list[dict[str, Any]] = []
    ws = _manage_socket(saved, ["default", "victorian_butler"])

    with patch.object(coordinator._session, "ws_connect", AsyncMock(return_value=ws)):
        selection = await coordinator.async_save_personality(
            "my_butler", based_on="victorian_butler", voice="Serena", apply=True
        )

    assert selection == "user_personalities/my_butler"
    assert saved == [
        {
            "name": "my_butler",
            "instructions": "You are a butler.",
            "overwrite": True,
            "greeting": "Bow.",
            "voice": "Serena",
            "tools_text": "dance\ncamera\n",
        }
    ]
    assert ("personalities.apply", {"name": "user_personalities/my_butler", "force": True}) in ws.calls


async def test_save_personality_edits_existing_custom(hass, coordinator, aioclient_mock) -> None:
    """No based_on: an existing custom personality of that name is the base."""
    aioclient_mock.get(f"{BASE_URL}/api/daemon/robot-app-lock-status", json=_RUNNING_LOCK)
    saved: list[dict[str, Any]] = []
    ws = _manage_socket(saved, ["default", "user_personalities/my_butler"])

    with patch.object(coordinator._session, "ws_connect", AsyncMock(return_value=ws)):
        await coordinator.async_save_personality("my_butler", tools=["move_head"])

    assert ("personalities.load", {"name": "user_personalities/my_butler"}) in ws.calls
    assert saved[0]["instructions"] == "You are a butler."
    assert saved[0]["tools_text"] == "move_head\n"
    assert "personalities.apply" not in [m for m, _ in ws.calls]


async def test_save_new_personality_needs_instructions(hass, coordinator, aioclient_mock) -> None:
    """A brand-new personality without instructions or base is rejected."""
    aioclient_mock.get(f"{BASE_URL}/api/daemon/robot-app-lock-status", json=_RUNNING_LOCK)
    saved: list[dict[str, Any]] = []
    ws = _manage_socket(saved, ["default"])

    with (
        patch.object(coordinator._session, "ws_connect", AsyncMock(return_value=ws)),
        pytest.raises(HomeAssistantError, match="no instructions"),
    ):
        await coordinator.async_save_personality("fresh")

    assert saved == []


async def test_personality_actions_need_running_app(hass, coordinator, aioclient_mock) -> None:
    """Managing personalities never wakes the robot or starts the app."""
    aioclient_mock.get(f"{BASE_URL}/api/daemon/robot-app-lock-status", json={"state": "free"})

    with pytest.raises(HomeAssistantError, match="not running"):
        await coordinator.async_get_personality("default")

    assert _posts(aioclient_mock) == []


async def test_session_profile_name_is_reserved(hass, coordinator) -> None:
    """home_assistant_session belongs to start_conversation."""
    with pytest.raises(HomeAssistantError, match="reserved"):
        await coordinator.async_save_personality("home_assistant_session", instructions="x")


async def test_get_and_delete_personality(hass, coordinator, aioclient_mock) -> None:
    """get returns the profile fields; delete accepts a bare custom name."""
    aioclient_mock.get(f"{BASE_URL}/api/daemon/robot-app-lock-status", json=_RUNNING_LOCK)
    ws = _manage_socket([], ["default"])
    coordinator.selected_personality = "user_personalities/my_butler"

    with patch.object(coordinator._session, "ws_connect", AsyncMock(return_value=ws)):
        result = await coordinator.async_get_personality("victorian_butler")
        await coordinator.async_delete_personality("my_butler")

    assert result == {
        "name": "victorian_butler",
        "instructions": "You are a butler.",
        "greeting": "Bow.",
        "voice": "Ryan",
        "uses_default_voice": False,
        "tools": ["dance", "camera"],
        "available_tools": ["dance", "camera", "move_head"],
    }
    assert ("personalities.delete", {"name": "user_personalities/my_butler"}) in ws.calls
    # The deleted pick falls back to the app's current personality.
    assert coordinator.selected_personality == "default"


def test_rpc_error_reasons_are_readable() -> None:
    """Bare app reasons get a readable message; real messages are kept."""
    err = conversation.ConversationRpcError("personalities.delete", "profile_in_use", "profile_in_use")
    assert "switch to another one first" in str(err)
    err = conversation.ConversationRpcError("personalities.save", "bad name", "invalid_name")
    assert str(err) == "personalities.save failed: bad name"
