"""DataUpdateCoordinator for Reachy Mini.

Fans out to several SDK endpoints in parallel and assembles a
unified, HA-shaped dict the entity platforms read by key:

- ``/api/daemon/status``           — daemon health, motor_control_mode,
                                     firmware version, hardware_id.
- ``/api/daemon/robot-app-lock-status`` — managed app slot owner.
- ``/api/state/doa``               — mic-array direction of arrival.
- ``/api/volume/current``          — speaker volume.
- ``/api/volume/microphone/current`` — microphone volume.

Each fetch is independent: an individual 404 / network error /
malformed response produces ``None`` for that endpoint's contribution
to the dict but does **not** mark the whole coordinator as
``UpdateFailed``. The whole coordinator only fails when *every*
endpoint is unreachable (no network, daemon down) — at which point
HA marks all entities unavailable, which is the correct UX.

HA-shaped derivations live here (not in the SDK):

- ``awake`` = motor_mode in {"enabled", "gravity_compensation"}.
- ``active_app``, ``active_app_transport``, ``remote_session_active``
  derived from the app-lock state + holder name. "Remote session" means
  a remote client (mobile/desktop app over WebRTC) holds the robot app
  slot — deliberately not named after WebRTC itself, since camera-feed
  consumption also uses WebRTC but never takes the slot.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    APP_STOP_TIMEOUT,
    CONVERSATION_APP_NAME,
    CONVERSATION_LIST_TIMEOUT,
    CONVERSATION_MANAGE_TIMEOUT,
    CONVERSATION_SESSION_PROFILE,
    CONVERSATION_READY_TIMEOUT,
    CONVERSATION_SESSION_SELECTION,
    DAEMON_START_TIMEOUT,
    DAEMON_STATE_RUNNING,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    ENDPOINT_APP_LOCK,
    ENDPOINT_APP_START,
    ENDPOINT_APP_STOP_CURRENT,
    ENDPOINT_APPS_LIST_INSTALLED,
    ENDPOINT_DAEMON_START_WAKE,
    ENDPOINT_DOA,
    ENDPOINT_MOVE_LIST,
    ENDPOINT_MOVE_PLAY,
    ENDPOINT_STATUS,
    ENDPOINT_VOLUME_MIC,
    ENDPOINT_VOLUME_SPEAKER,
    RECORDED_MOVE_DATASETS,
)
from .conversation import ConversationClient, ConversationRpcError

_LOGGER = logging.getLogger(__name__)


def _derive_awake(motor_mode: str | None, daemon_state: str | None) -> bool | None:
    """`enabled` and `gravity_compensation` both count as awake.

    A backend that isn't running at all (the Wireless unit's normal
    asleep state — it reports no motor mode) is definitively asleep,
    not unknown.
    """
    if daemon_state is not None and daemon_state != DAEMON_STATE_RUNNING:
        return False
    if motor_mode is None:
        return None
    return motor_mode in ("enabled", "gravity_compensation")


def _derive_app_slot(state: str | None, holder: str | None) -> dict[str, Any]:
    """Expand the raw app-lock fields into HA-shaped values."""
    if state == "local_app":
        return {
            "active_app": holder,
            "active_app_transport": "local",
            "remote_session_active": False,
        }
    if state == "remote_session":
        return {
            "active_app": holder,
            "active_app_transport": "webrtc",
            "remote_session_active": True,
        }
    if state == "free":
        return {
            "active_app": None,
            "active_app_transport": None,
            "remote_session_active": False,
        }
    return {
        "active_app": None,
        "active_app_transport": None,
        "remote_session_active": None,
    }


class ReachyMiniCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fan-out poller for the SDK's individual REST endpoints."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Bind the coordinator to a config entry."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_HOST]}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self._entry = entry
        self._session = async_get_clientsession(hass)
        # Recorded-move catalogs. Populated once at setup-time via
        # async_load_move_lists. Stable at runtime — not in the per-tick
        # fan-out poll. Keyed by full HF dataset path.
        self.move_lists: dict[str, list[str]] = {}
        # User's last pick per dataset. Mutated by the select entities'
        # async_select_option; read by the matching play buttons.
        self.selected_move: dict[str, str | None] = {
            ds: None for ds in RECORDED_MOVE_DATASETS
        }
        # Installed app names (SourceKind.INSTALLED only — excludes the
        # not-yet-installed HF Space / dashboard / local catalog).
        # Populated once at setup-time via async_load_available_apps,
        # same lifecycle as move_lists.
        self.available_apps: list[str] = []
        # User's last pick from the app select; read by the start-app button.
        self.selected_app: str | None = None
        # Conversation-app settings the Start conversation button uses,
        # set through the personality select / instructions, context and
        # greeting text entities. ``personalities`` is the app's catalog, fetched
        # over its /rpc socket (only reachable while the app runs).
        self.personalities: list[str] = []
        self.selected_personality: str | None = None
        self.conversation_instructions: str = ""
        self.conversation_context: str = ""
        self.conversation_greeting: str = ""

    @property
    def host(self) -> str:
        """Configured hostname or IP of the daemon."""
        return self._entry.data[CONF_HOST]

    @property
    def port(self) -> int:
        """Configured TCP port of the daemon (default 8000)."""
        return self._entry.data.get(CONF_PORT, DEFAULT_PORT)

    @property
    def base_url(self) -> str:
        """``http://<host>:<port>`` — root for all endpoint paths."""
        return f"http://{self.host}:{self.port}"

    async def _fetch_json(self, path: str) -> Any | None:
        """Fetch one endpoint; return parsed JSON or None on any failure."""
        url = f"{self.base_url}{path}"
        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
            ) as resp:
                if resp.status != 200:
                    _LOGGER.debug("Reachy Mini %s returned %s", path, resp.status)
                    return None
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Reachy Mini fetch %s failed: %s", path, err)
            return None

    async def async_post(
        self,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """POST to a daemon endpoint and refresh coordinator state.

        Shared helper for the integration's writable entities
        (number sliders, select dropdowns, action buttons). Logs and
        re-raises on failure so HA surfaces the action as failed in
        the UI. Triggers an immediate coordinator refresh on success
        so the state snaps to whatever the daemon actually applied —
        users don't have to wait up to 30 s for the next poll.
        """
        url = f"{self.base_url}{path}"
        try:
            async with self._session.post(
                url,
                json=body,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                resp.raise_for_status()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Reachy Mini POST %s failed: %s", path, err)
            raise
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll all endpoints in parallel and assemble the unified dict."""
        status, app_lock, doa, vol_speaker, vol_mic = await asyncio.gather(
            self._fetch_json(ENDPOINT_STATUS),
            self._fetch_json(ENDPOINT_APP_LOCK),
            self._fetch_json(ENDPOINT_DOA),
            self._fetch_json(ENDPOINT_VOLUME_SPEAKER),
            self._fetch_json(ENDPOINT_VOLUME_MIC),
        )

        # If *every* endpoint is None, the daemon is completely
        # unreachable — propagate as UpdateFailed so HA marks the
        # device unavailable. Otherwise, surface what we have.
        if all(x is None for x in (status, app_lock, doa, vol_speaker, vol_mic)):
            raise UpdateFailed(f"Cannot reach Reachy Mini at {self.base_url}")

        data: dict[str, Any] = {
            # Identity / health from /api/daemon/status
            "firmware_version": None,
            "hardware_id": None,
            "robot_name": None,
            "daemon_state": None,
            "motor_mode": None,
            "awake": None,
            # App slot from /api/daemon/robot-app-lock-status
            "active_app": None,
            "active_app_transport": None,
            "remote_session_active": None,
            # DoA from /api/state/doa (null when audio disabled)
            "doa_angle_rad": None,
            "doa_speech_detected": None,
            # Audio mixer
            "speaker_volume": None,
            "mic_volume": None,
        }

        if isinstance(status, dict):
            data["firmware_version"] = status.get("version")
            data["hardware_id"] = status.get("hardware_id")
            data["robot_name"] = status.get("robot_name")
            daemon_state = status.get("state")
            data["daemon_state"] = daemon_state
            backend = status.get("backend_status") or {}
            motor_mode = backend.get("motor_control_mode")
            data["motor_mode"] = motor_mode
            data["awake"] = _derive_awake(motor_mode, daemon_state)

        if isinstance(app_lock, dict):
            data.update(
                _derive_app_slot(app_lock.get("state"), app_lock.get("holder_name"))
            )

        if isinstance(doa, dict):
            data["doa_angle_rad"] = doa.get("angle")
            data["doa_speech_detected"] = doa.get("speech_detected")

        if isinstance(vol_speaker, dict):
            v = vol_speaker.get("volume")
            data["speaker_volume"] = v if isinstance(v, int) and v >= 0 else None

        if isinstance(vol_mic, dict):
            v = vol_mic.get("volume")
            data["mic_volume"] = v if isinstance(v, int) and v >= 0 else None

        return data

    async def async_load_move_lists(self) -> None:
        """Fetch the move name list for each known dataset (best-effort).

        Called once during ``async_setup_entry`` after the first
        coordinator refresh. Failures (404 from older daemons, network
        blip) leave that dataset absent from ``move_lists`` — the
        matching entities are then simply not created downstream.
        """
        for dataset in RECORDED_MOVE_DATASETS:
            path = ENDPOINT_MOVE_LIST.format(dataset=dataset)
            result = await self._fetch_json(path)
            if isinstance(result, list) and result:
                moves = [str(m) for m in result]
                self.move_lists[dataset] = moves
                # Default the selection so a fresh-install button press
                # works without forcing the user to pick first.
                if self.selected_move.get(dataset) is None:
                    self.selected_move[dataset] = moves[0]
            else:
                _LOGGER.info(
                    "Reachy Mini: no recorded moves available for dataset %s",
                    dataset,
                )

    async def async_play_recorded_move(self, dataset: str, move: str) -> None:
        """POST to the play endpoint; re-raises on HTTP failure."""
        await self.async_post(
            ENDPOINT_MOVE_PLAY.format(dataset=dataset, move=move)
        )

    async def async_load_available_apps(self) -> None:
        """Fetch the installed app catalog (best-effort).

        Called once during ``async_setup_entry``, same lifecycle as
        ``async_load_move_lists``. Only installed apps (SourceKind.
        INSTALLED) are listed — not the full HF Space / dashboard /
        local catalog of apps that aren't installed yet. A failure
        (older daemon, network blip) just leaves ``available_apps``
        empty — the app select / start-app button are then simply not
        created downstream.
        """
        result = await self._fetch_json(ENDPOINT_APPS_LIST_INSTALLED)
        if isinstance(result, list) and result:
            names = [str(app["name"]) for app in result if "name" in app]
            self.available_apps = names
            if names and self.selected_app is None:
                self.selected_app = names[0]
        else:
            _LOGGER.info("Reachy Mini: no available apps returned by daemon")

    async def async_load_personalities(
        self, timeout: float = CONVERSATION_LIST_TIMEOUT
    ) -> None:
        """Fetch the conversation app's personality list (best-effort).

        The app only answers while it runs, so a failure just keeps the
        previous list; it's fetched again every time a conversation is
        started. Listeners are notified so the select picks up changes.
        """
        client = ConversationClient(self._session, self.host, timeout)
        try:
            result = await client.async_list_personalities()
        except (ConversationRpcError, TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.debug("Reachy Mini: personality list unavailable: %s", err)
            return
        choices = [
            str(c) for c in result.get("choices") or [] if c != CONVERSATION_SESSION_SELECTION
        ]
        if not choices:
            return
        self.personalities = choices
        if self.selected_personality not in choices:
            current = result.get("current")
            self.selected_personality = current if current in choices else choices[0]
        self.async_update_listeners()

    async def _async_require_conversation_app(self) -> None:
        """Raise unless the conversation app currently holds the app slot.

        Its /rpc socket only exists while it runs, and managing
        personalities shouldn't wake the robot or evict another app.
        """
        app_lock = await self._fetch_json(ENDPOINT_APP_LOCK) or {}
        slot = _derive_app_slot(app_lock.get("state"), app_lock.get("holder_name"))
        if slot["active_app"] != CONVERSATION_APP_NAME:
            raise HomeAssistantError(
                "The conversation app is not running — press Start "
                "conversation (or call reachy_mini.start_conversation) first"
            )

    async def _async_manage_personality(self, action: Any) -> Any:
        """Run one personality operation against the running app."""
        await self._async_require_conversation_app()
        client = ConversationClient(self._session, self.host, CONVERSATION_MANAGE_TIMEOUT)
        try:
            return await action(client)
        except (ConversationRpcError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(f"Conversation app: {err}") from err

    async def async_get_personality(self, name: str) -> dict[str, Any]:
        """Return a personality's instructions, greeting, voice and tools."""
        loaded = await self._async_manage_personality(
            lambda client: client.async_call("personalities.load", {"name": name})
        )
        loaded = loaded or {}
        return {
            "name": name,
            "instructions": loaded.get("instructions", ""),
            "greeting": loaded.get("greeting", ""),
            "voice": loaded.get("voice"),
            "uses_default_voice": loaded.get("uses_default_voice"),
            "tools": loaded.get("enabled_tools", []),
            "available_tools": loaded.get("available_tools", []),
        }

    async def async_save_personality(self, name: str, **fields: Any) -> str:
        """Create/update a user personality; refresh the select's catalog."""
        if name == CONVERSATION_SESSION_PROFILE:
            raise HomeAssistantError(
                f"'{name}' is reserved for start_conversation instructions"
            )
        selection = await self._async_manage_personality(
            lambda client: client.async_save_personality(name, **fields)
        )
        await self.async_load_personalities()
        return selection

    async def async_delete_personality(self, name: str) -> None:
        """Delete a user personality; refresh the select's catalog.

        The app refuses bundled personalities and the active / startup
        one, with an explanatory error.
        """
        # Only custom personalities can be deleted, so a bare name means
        # the user_personalities/ one.
        if "/" not in name:
            name = f"user_personalities/{name}"
        await self._async_manage_personality(
            lambda client: client.async_call("personalities.delete", {"name": name})
        )
        if self.selected_personality == name:
            self.selected_personality = None
        await self.async_load_personalities()

    async def async_start_conversation_from_entities(self) -> None:
        """Start a conversation with the device page's settings."""
        await self.async_start_conversation(
            personality=self.selected_personality,
            instructions=self.conversation_instructions or None,
            context=self.conversation_context or None,
            greeting=self.conversation_greeting or None,
        )

    async def async_start_conversation(
        self,
        *,
        personality: str | None = None,
        voice: str | None = None,
        greeting: str | None = None,
        instructions: str | None = None,
        context: str | None = None,
    ) -> None:
        """Start a conversation-app session, waking the robot if needed.

        Mirrors what the dashboard / mobile app do to "start talking":

        1. Wake the daemon backend if it's stopped (Wireless sleep).
        2. Launch ``reachy_mini_conversation_app`` unless it already
           holds the app slot — stopping any other local app first,
           since the daemon refuses to start a second one.
        3. Optionally pick the voice / personality, add session
           instructions, and open the chat with context and/or a
           greeting, over the app's JSON-RPC ``/rpc`` socket.

        State is read fresh from the daemon rather than from the last
        poll, which can be up to 30 s stale.
        """
        status = await self._fetch_json(ENDPOINT_STATUS)
        if not isinstance(status, dict) or status.get("state") != DAEMON_STATE_RUNNING:
            await self.async_post(ENDPOINT_DAEMON_START_WAKE)
            await self._async_wait_daemon_running()

        app_lock = await self._fetch_json(ENDPOINT_APP_LOCK) or {}
        slot = _derive_app_slot(app_lock.get("state"), app_lock.get("holder_name"))
        if slot["active_app"] != CONVERSATION_APP_NAME:
            if slot["active_app_transport"] == "local":
                await self.async_post(ENDPOINT_APP_STOP_CURRENT, timeout=APP_STOP_TIMEOUT)
            # A remote (WebRTC) session is evicted by start-app itself.
            await self.async_post(ENDPOINT_APP_START.format(app_name=CONVERSATION_APP_NAME))

        if personality or voice or greeting or instructions or context:
            client = ConversationClient(self._session, self.host, CONVERSATION_READY_TIMEOUT)
            try:
                await client.async_run(
                    personality=personality,
                    voice=voice,
                    greeting=greeting,
                    instructions=instructions,
                    context=context,
                )
            except (ConversationRpcError, TimeoutError, aiohttp.ClientError) as err:
                raise HomeAssistantError(f"Conversation app: {err}") from err

        # The app is up now: refresh the catalog (e.g. first start, or
        # personalities added in the app since).
        await self.async_load_personalities()

    async def _async_wait_daemon_running(self) -> None:
        """Poll daemon status until the backend reports ``running``."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DAEMON_START_TIMEOUT
        while loop.time() < deadline:
            status = await self._fetch_json(ENDPOINT_STATUS)
            if isinstance(status, dict) and status.get("state") == DAEMON_STATE_RUNNING:
                return
            await asyncio.sleep(1.0)
        raise HomeAssistantError("Reachy Mini daemon did not start in time")
