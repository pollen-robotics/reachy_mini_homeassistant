"""Minimal JSON-RPC client for the Reachy Mini conversation app.

The conversation app (``reachy_mini_conversation_app``) mounts a JSON-RPC
2.0 WebSocket at ``ws://<robot>:7860/rpc`` — the same control surface its
own web UI and the mobile app (via the daemon relay) use. Only the
methods needed to start a session and manage personalities from HA are
wrapped here:

- ``conversation.status``   — is the realtime backend connected yet?
- ``voices.apply``          — change the voice without a backend rebuild.
- ``personalities.list`` / ``personalities.load`` / ``personalities.save``
                            — read profiles, save customized copies (the
                              bundled ones are read-only app-side).
- ``personalities.delete``  — remove a user personality.
- ``personalities.apply``   — switch profile (rebuilds the backend).
- ``conversation.say``      — inject a user turn Reachy answers out loud,
                              used to open the chat with context.

The app takes a few seconds to come up after the daemon spawns it, and
its backend reconnects after a personality change, so every step retries
until a shared deadline instead of failing on the first "not ready yet".
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

import aiohttp

from .const import (
    CONVERSATION_RPC_PATH,
    CONVERSATION_RPC_PORT,
    CONVERSATION_SESSION_PROFILE,
)

_LOGGER = logging.getLogger(__name__)

# Delay between readiness retries (connect, backend status, say).
_RETRY_INTERVAL = 1.0
# Per-request cap; personalities.apply itself waits up to 10 s app-side.
_CALL_TIMEOUT = 15.0
# Error reasons that just mean "the app isn't ready yet — try again".
_RETRYABLE_REASONS = frozenset({"not_running", "loop_unavailable"})
# Separates the base personality's instructions from the ones added by
# HA, so a later session built on top of the session profile itself
# (no explicit personality given) replaces them instead of stacking.
_SESSION_INSTRUCTIONS_MARKER = "\n\n## Session instructions (from Home Assistant)\n"
_USER_PROFILE_PREFIX = "user_personalities/"
# Readable text for the app's terse error reasons.
_REASON_MESSAGES = {
    "profile_in_use": (
        "it is the active or startup personality — switch to another one first"
    ),
    "not_deletable": "only custom (user_personalities/...) personalities can be deleted",
    "profile_locked": "the conversation app is locked to a single personality",
    "profile_unavailable": "personality not found or unreadable",
    "profile_exists": "a personality with that name already exists",
}


def build_opening_message(context: str | None, greeting: str | None) -> str | None:
    """Compose the user turn that opens the conversation.

    ``conversation.say`` is not verbatim TTS: the model *answers* the
    injected text. So a greeting is phrased as a request to say it, and
    context is framed as background for the opening line.
    """
    parts: list[str] = []
    if context:
        parts.append(f"Context for this conversation (from Home Assistant): {context}")
    if greeting:
        parts.append(f"Open the conversation now by saying, in character: {greeting}")
    elif context:
        parts.append(
            "Open the conversation now with a brief greeting in character "
            "that takes this context into account."
        )
    return "\n\n".join(parts) or None


class ConversationRpcError(Exception):
    """A JSON-RPC error response from the conversation app."""

    def __init__(self, method: str, message: str, reason: str | None) -> None:
        """Keep the stable ``reason`` string for retry decisions."""
        if reason in _REASON_MESSAGES and message in ("", reason, "None"):
            message = _REASON_MESSAGES[reason]
        super().__init__(f"{method} failed: {message}")
        self.reason = reason


class ConversationClient:
    """One WebSocket session against the conversation app's ``/rpc``."""

    def __init__(self, session: aiohttp.ClientSession, host: str, timeout: float) -> None:
        """Bind to the robot host; nothing connects until ``async_run``."""
        self._session = session
        self._url = f"ws://{host}:{CONVERSATION_RPC_PORT}{CONVERSATION_RPC_PATH}"
        self._timeout = timeout
        self._ids = itertools.count(1)
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    async def async_run(
        self,
        *,
        personality: str | None = None,
        voice: str | None = None,
        greeting: str | None = None,
        instructions: str | None = None,
        context: str | None = None,
    ) -> None:
        """Configure the running session and optionally open the chat.

        Voice goes first: it is kept as the handler's override, so it
        survives the backend rebuild a personality change triggers.
        """
        opening = build_opening_message(context, greeting)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        await self._async_connect(deadline)
        assert self._ws is not None
        try:
            await self._async_wait_backend_connected(deadline)
            if voice:
                await self._async_call_retrying("voices.apply", {"voice": voice}, deadline)
            if instructions:
                profile = await self._async_save_session_profile(
                    personality, instructions, deadline
                )
                # force: the session profile may already be the active
                # one, with different instructions from a previous run.
                await self._async_call_retrying(
                    "personalities.apply", {"name": profile, "force": True}, deadline
                )
            elif personality:
                await self._async_call_retrying(
                    "personalities.apply", {"name": personality}, deadline
                )
            if opening:
                await self._async_wait_backend_connected(deadline)
                await self._async_call_retrying(
                    "conversation.say", {"text": opening}, deadline
                )
        finally:
            await self._ws.close()

    async def async_call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Connect, make one call (retrying "not ready"), disconnect."""
        deadline = asyncio.get_running_loop().time() + self._timeout
        await self._async_connect(deadline)
        assert self._ws is not None
        try:
            return await self._async_call_retrying(method, params or {}, deadline)
        finally:
            await self._ws.close()

    async def async_list_personalities(self) -> dict[str, Any]:
        """Return ``personalities.list`` (``choices`` + ``current``)."""
        result = await self.async_call("personalities.list")
        return result if isinstance(result, dict) else {}

    async def async_save_personality(
        self,
        name: str,
        *,
        based_on: str | None = None,
        instructions: str | None = None,
        greeting: str | None = None,
        voice: str | None = None,
        tools: list[str] | None = None,
        apply: bool = False,
    ) -> str:
        """Create or update a user personality; return its selection name.

        Fields not given are taken from ``based_on`` or, when editing an
        existing user personality of the same name, from that one — so
        e.g. only the instructions can be changed. With neither, the
        app's defaults apply (default voice and tool set, no greeting).
        """
        deadline = asyncio.get_running_loop().time() + self._timeout
        await self._async_connect(deadline)
        assert self._ws is not None
        try:
            base = based_on
            if base is None:
                listing = await self._async_call_retrying("personalities.list", {}, deadline)
                existing = f"{_USER_PROFILE_PREFIX}{name}"
                if existing in ((listing or {}).get("choices") or []):
                    base = existing
            loaded: dict[str, Any] = {}
            if base is not None:
                loaded = (
                    await self._async_call_retrying("personalities.load", {"name": base}, deadline)
                    or {}
                )

            payload: dict[str, Any] = {
                "name": name,
                "instructions": instructions or loaded.get("instructions") or "",
                "overwrite": True,
            }
            if not payload["instructions"].strip():
                raise ConversationRpcError(
                    "personalities.save",
                    f"no instructions for new personality '{name}' — give "
                    "instructions, or based_on to start from an existing one",
                    "invalid_instructions",
                )
            if greeting is not None:
                payload["greeting"] = greeting
            elif loaded.get("greeting"):
                payload["greeting"] = loaded["greeting"]
            if voice:
                payload["voice"] = voice
            elif loaded.get("voice") and not loaded.get("uses_default_voice"):
                payload["voice"] = loaded["voice"]
            if tools is not None:
                payload["tools_text"] = "".join(f"{tool}\n" for tool in tools)
            elif "tools_text" in loaded:
                payload["tools_text"] = loaded["tools_text"] or ""

            saved = await self._async_call_retrying("personalities.save", payload, deadline)
            selection = str((saved or {}).get("value") or f"{_USER_PROFILE_PREFIX}{name}")
            if apply:
                # force: re-applying the active personality must pick up
                # the edited content.
                await self._async_call_retrying(
                    "personalities.apply", {"name": selection, "force": True}, deadline
                )
            return selection
        finally:
            await self._ws.close()

    async def _async_save_session_profile(
        self, personality: str | None, instructions: str, deadline: float
    ) -> str:
        """Save the base personality plus ``instructions`` as the session profile.

        The app only takes system instructions from a personality
        profile, so the base one (``personality`` or the currently
        active one) is copied — instructions, greeting, voice, tools —
        with the extra instructions appended, into a dedicated profile
        that is overwritten on every call. Returns its selection name.
        """
        base = personality
        if not base:
            choices = await self._async_call_retrying("personalities.list", {}, deadline)
            base = (choices or {}).get("current") or "default"
        loaded = await self._async_call_retrying("personalities.load", {"name": base}, deadline)
        loaded = loaded or {}
        base_instructions = str(loaded.get("instructions") or "")
        base_instructions = base_instructions.split(_SESSION_INSTRUCTIONS_MARKER, 1)[0].rstrip()

        payload: dict[str, Any] = {
            "name": CONVERSATION_SESSION_PROFILE,
            "instructions": (
                f"{base_instructions}{_SESSION_INSTRUCTIONS_MARKER}{instructions.strip()}"
            ),
            "tools_text": loaded.get("tools_text") or "",
            "overwrite": True,
        }
        if loaded.get("greeting"):
            payload["greeting"] = loaded["greeting"]
        if loaded.get("voice") and not loaded.get("uses_default_voice"):
            payload["voice"] = loaded["voice"]
        saved = await self._async_call_retrying("personalities.save", payload, deadline)
        return str(
            (saved or {}).get("value")
            or f"{_USER_PROFILE_PREFIX}{CONVERSATION_SESSION_PROFILE}"
        )

    async def _async_connect(self, deadline: float) -> None:
        """Open the WebSocket, retrying while the app is still starting."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                self._ws = await self._session.ws_connect(
                    self._url, timeout=aiohttp.ClientWSTimeout(ws_close=5.0)
                )
                return
            except (aiohttp.ClientError, OSError) as err:
                if loop.time() >= deadline:
                    raise TimeoutError(
                        f"Conversation app did not answer at {self._url}: {err}"
                    ) from err
                _LOGGER.debug("Conversation app not reachable yet: %s", err)
            await asyncio.sleep(_RETRY_INTERVAL)

    async def _async_wait_backend_connected(self, deadline: float) -> None:
        """Poll ``conversation.status`` until the realtime backend is live."""
        loop = asyncio.get_running_loop()
        while True:
            status = await self._async_call("conversation.status", {})
            if isinstance(status, dict) and status.get("backend_connected"):
                return
            if loop.time() >= deadline:
                state = status.get("backend_connection_state") if isinstance(status, dict) else None
                error = status.get("backend_error") if isinstance(status, dict) else None
                raise TimeoutError(
                    f"Conversation backend not connected (state: {state}, error: {error})"
                )
            await asyncio.sleep(_RETRY_INTERVAL)

    async def _async_call_retrying(
        self, method: str, params: dict[str, Any], deadline: float
    ) -> Any:
        """Call ``method``, retrying on "not ready" errors until the deadline."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                return await self._async_call(method, params)
            except ConversationRpcError as err:
                if err.reason not in _RETRYABLE_REASONS or loop.time() >= deadline:
                    raise
            await asyncio.sleep(_RETRY_INTERVAL)

    async def _async_call(self, method: str, params: dict[str, Any]) -> Any:
        """Send one request and wait for its response, skipping notifications."""
        assert self._ws is not None
        request_id = next(self._ids)
        await self._ws.send_str(
            json.dumps(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
        )
        async with asyncio.timeout(_CALL_TIMEOUT):
            while True:
                msg = await self._ws.receive()
                if msg.type != aiohttp.WSMsgType.TEXT:
                    raise ConversationRpcError(
                        method, f"connection closed ({msg.type.name})", None
                    )
                frame = json.loads(msg.data)
                # Notifications (conversation.turn / transcript ...) carry
                # no id; responses to other requests can't happen since we
                # only have one in flight.
                if frame.get("id") != request_id:
                    continue
                if "error" in frame:
                    error = frame["error"] or {}
                    raise ConversationRpcError(
                        method,
                        str(error.get("message")),
                        (error.get("data") or {}).get("reason"),
                    )
                return frame.get("result")
