"""Constants for the Reachy Mini integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "reachy_mini"

# Reachy Mini daemon defaults — match upstream src/reachy_mini/daemon/app/main.py.
DEFAULT_PORT = 8000
ENDPOINT_PATH = "/api/daemon/snapshot"

# Polling. The SDK endpoint is cheap; 30 s is a good default for HA
# polled REST integrations.
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
DEFAULT_TIMEOUT = 5.0

# TXT record keys exposed by the SDK's _reachy-mini._tcp.local. service.
TXT_UNIT_ID = "unit_id"
TXT_MODEL = "model"
TXT_MANUFACTURER = "manufacturer"
TXT_VERSION = "version"
TXT_ROBOT_NAME = "robot_name"

# The integration's own schema_version — independent of any SDK
# response shape. Bumps when the *integration's* derived dict keys
# (the names the entity classes read) change in a breaking way.
INTEGRATION_SCHEMA_VERSION = 1

# Required keys we expect to see in the SDK snapshot. If any are
# missing we still surface what we have — but log a single warning so
# users get a heads-up that the SDK and integration may have drifted.
EXPECTED_SDK_KEYS = frozenset(
    {
        "unit_id",
        "model",
        "manufacturer",
        "firmware_version",
        "uptime_seconds",
        "motor_mode",
        "imu_quaternion",
        "imu_temp_celsius",
        "doa_angle_rad",
        "doa_speech_detected",
        "app_lock_state",
        "app_lock_holder",
        "speaker_volume",
        "mic_volume",
        "cpu_pct",
        "mem_pct",
    }
)

# Config entry data keys.
CONF_UNIT_ID = "unit_id"
