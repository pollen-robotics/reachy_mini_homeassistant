"""DataUpdateCoordinator for Reachy Mini.

Polls the SDK's primitive snapshot endpoint
(``GET /api/daemon/snapshot``) and derives the consumer-side fields
that HA's entity platforms read.

The SDK exposes only primitive values (motor_mode, imu_quaternion,
app_lock_state, …). All HA-shaped semantics live here:

- ``awake``  = motor_mode is "enabled" or "gravity_compensation"
- ``imu_pitch_deg``, ``imu_roll_deg``  = euler conversion of quaternion
- ``active_app``, ``active_app_transport``  = derived from app_lock_*
- ``webrtc_active``  = app_lock_state == "remote_session"

Failure handling: a single failed poll surfaces as ``UpdateFailed`` so
all entities go ``unavailable``. The next successful poll recovers them.
"""

from __future__ import annotations

import logging
import math
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
    ENDPOINT_PATH,
    EXPECTED_SDK_KEYS,
)

_LOGGER = logging.getLogger(__name__)


def _quat_to_pitch_roll_deg(quat: list[float]) -> tuple[float, float]:
    """Convert a (w, x, y, z) quaternion to pitch and roll, in degrees.

    Yaw is intentionally omitted — drifts on a six-axis IMU without a
    magnetometer, and pitch/roll are the only orientation signals a
    typical HA user cares about ("is the robot tipped over?").
    """
    w, x, y, z = quat
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))
    sinp = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    pitch = math.degrees(math.asin(sinp))
    return pitch, roll


def _derive_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply HA-shaped derivations on top of the raw SDK snapshot.

    Returns a new dict containing both the raw fields *and* the
    derived ones — entity platforms look up by key without needing to
    know which is which.
    """
    out: dict[str, Any] = dict(raw)

    motor_mode = raw.get("motor_mode")
    if motor_mode is not None:
        out["awake"] = motor_mode in ("enabled", "gravity_compensation")
    else:
        out["awake"] = None

    quat = raw.get("imu_quaternion")
    if isinstance(quat, list) and len(quat) == 4:
        try:
            pitch, roll = _quat_to_pitch_roll_deg(quat)
            out["imu_pitch_deg"] = round(pitch, 2)
            out["imu_roll_deg"] = round(roll, 2)
        except (TypeError, ValueError):
            out["imu_pitch_deg"] = None
            out["imu_roll_deg"] = None
    else:
        out["imu_pitch_deg"] = None
        out["imu_roll_deg"] = None

    lock_state = raw.get("app_lock_state")
    lock_holder = raw.get("app_lock_holder")
    if lock_state == "local_app":
        out["active_app"] = lock_holder
        out["active_app_transport"] = "local"
        out["webrtc_active"] = False
    elif lock_state == "remote_session":
        out["active_app"] = lock_holder
        out["active_app_transport"] = "webrtc"
        out["webrtc_active"] = True
    elif lock_state == "free":
        out["active_app"] = None
        out["active_app_transport"] = None
        out["webrtc_active"] = False
    else:
        out["active_app"] = None
        out["active_app_transport"] = None
        out["webrtc_active"] = None

    return out


class ReachyMiniCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the SDK's snapshot endpoint and derives HA-shaped fields."""

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
        self._missing_keys_logged = False

    @property
    def host(self) -> str:
        """Configured hostname or IP of the daemon."""
        return self._entry.data[CONF_HOST]

    @property
    def port(self) -> int:
        """Configured TCP port of the daemon (default 8000)."""
        return self._entry.data.get(CONF_PORT, DEFAULT_PORT)

    @property
    def url(self) -> str:
        """Fully-qualified URL of the SDK snapshot endpoint."""
        return f"http://{self.host}:{self.port}{ENDPOINT_PATH}"

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the raw snapshot and apply HA-shaped derivations."""
        try:
            async with self._session.get(
                self.url,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                raw: dict[str, Any] = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Cannot reach Reachy Mini at {self.url}: {err}") from err

        # Detect SDK / integration drift once per coordinator lifetime.
        missing = EXPECTED_SDK_KEYS - raw.keys()
        if missing and not self._missing_keys_logged:
            _LOGGER.warning(
                "Reachy Mini snapshot is missing expected keys %s — "
                "the daemon and integration may be out of sync. "
                "Consider upgrading both.",
                sorted(missing),
            )
            self._missing_keys_logged = True

        return _derive_state(raw)
