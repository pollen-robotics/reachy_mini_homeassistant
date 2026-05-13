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
- ``active_app``, ``active_app_transport``, ``webrtc_active`` derived
  from the app-lock state + holder name.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    ENDPOINT_APP_LOCK,
    ENDPOINT_DOA,
    ENDPOINT_STATUS,
    ENDPOINT_VOLUME_MIC,
    ENDPOINT_VOLUME_SPEAKER,
)

_LOGGER = logging.getLogger(__name__)


def _derive_awake(motor_mode: str | None) -> bool | None:
    """`enabled` and `gravity_compensation` both count as awake."""
    if motor_mode is None:
        return None
    return motor_mode in ("enabled", "gravity_compensation")


def _derive_app_slot(state: str | None, holder: str | None) -> dict[str, Any]:
    """Expand the raw app-lock fields into HA-shaped values."""
    if state == "local_app":
        return {
            "active_app": holder,
            "active_app_transport": "local",
            "webrtc_active": False,
        }
    if state == "remote_session":
        return {
            "active_app": holder,
            "active_app_transport": "webrtc",
            "webrtc_active": True,
        }
    if state == "free":
        return {
            "active_app": None,
            "active_app_transport": None,
            "webrtc_active": False,
        }
    return {
        "active_app": None,
        "active_app_transport": None,
        "webrtc_active": None,
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
        self, path: str, *, body: dict[str, Any] | None = None
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
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
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
            "motor_mode": None,
            "awake": None,
            # App slot from /api/daemon/robot-app-lock-status
            "active_app": None,
            "active_app_transport": None,
            "webrtc_active": None,
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
            backend = status.get("backend_status") or {}
            motor_mode = backend.get("motor_control_mode")
            data["motor_mode"] = motor_mode
            data["awake"] = _derive_awake(motor_mode)

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
