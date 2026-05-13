"""Constants for the Reachy Mini integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "reachy_mini"

# Reachy Mini daemon defaults.
DEFAULT_PORT = 8000

# REST endpoints the coordinator fans out to on every poll. Each one
# fails independently — the entities backed by a failing endpoint go
# unavailable, others keep working.
ENDPOINT_STATUS = "/api/daemon/status"
ENDPOINT_APP_LOCK = "/api/daemon/robot-app-lock-status"
ENDPOINT_DOA = "/api/state/doa"
ENDPOINT_VOLUME_SPEAKER = "/api/volume/current"
ENDPOINT_VOLUME_MIC = "/api/volume/microphone/current"

# POST endpoints used by the controllable `number` entities.
# Body: {"volume": <int 0-100>}.
# Note: ENDPOINT_VOLUME_SPEAKER_SET plays a short confirmation sound
# on the robot every time it's invoked — that's existing SDK
# behaviour, not something the integration controls.
ENDPOINT_VOLUME_SPEAKER_SET = "/api/volume/set"
ENDPOINT_VOLUME_MIC_SET = "/api/volume/microphone/set"

# Other writable endpoints used by select / button entities.
ENDPOINT_MOTOR_SET_MODE = "/api/motors/set_mode/{mode}"  # path-templated
ENDPOINT_MOVE_WAKE_UP = "/api/move/play/wake_up"
ENDPOINT_MOVE_GOTO_SLEEP = "/api/move/play/goto_sleep"
ENDPOINT_APP_STOP_CURRENT = "/api/apps/stop-current-app"
ENDPOINT_APP_RESTART_CURRENT = "/api/apps/restart-current-app"
ENDPOINT_VOLUME_TEST_SOUND = "/api/volume/test-sound"
ENDPOINT_DAEMON_RESTART = "/api/daemon/restart"

# Motor mode values — must match the SDK's MotorControlMode enum
# `.value` strings. Kept here as a tuple so the select dropdown
# options stay in sync with the SDK without depending on its types.
MOTOR_MODES: tuple[str, ...] = ("enabled", "disabled", "gravity_compensation")

# Polling cadence. Matches HA's REST default and is plenty fast for
# "is the robot awake / which app is running / who's speaking".
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
DEFAULT_TIMEOUT = 5.0

# TXT record keys exposed by the SDK's _reachy-mini._tcp.local. service.
TXT_UNIT_ID = "unit_id"
TXT_MODEL = "model"
TXT_MANUFACTURER = "manufacturer"
TXT_VERSION = "version"
TXT_ROBOT_NAME = "robot_name"

# The integration's own schema_version for the *coordinator data dict*.
# Bumps when the keys the entity classes read change in a breaking way.
INTEGRATION_SCHEMA_VERSION = 2

# Config entry data keys.
CONF_UNIT_ID = "unit_id"
