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

# Daemon lifecycle endpoints — the canonical wake/sleep on the
# Wireless unit, where "asleep" means the backend is fully stopped
# and every /api/motors/* and /api/move/* route returns 503. Mirrors
# the official dashboard (dashboard/static/js/daemon.js). The start
# path also enables motor torque before the wake move, which the bare
# ENDPOINT_MOVE_WAKE_UP does not.
ENDPOINT_DAEMON_START_WAKE = "/api/daemon/start?wake_up=true"
ENDPOINT_DAEMON_STOP_SLEEP = "/api/daemon/stop?goto_sleep=true"

# /api/daemon/status "state" value while the backend is up. Anything
# else (stopped, not_initialized, starting, stopping, error) means the
# backend-gated endpoints are unusable.
DAEMON_STATE_RUNNING = "running"
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

# Variant model strings — exactly mirror the values the SDK advertises
# in its mDNS TXT `model` record (see reachy_mini/utils/discovery.py).
MODEL_WIRELESS = "Reachy Mini Wireless"
MODEL_LITE = "Reachy Mini Lite"
MODEL_DEFAULT = "Reachy Mini"

# Config entry data keys.
CONF_UNIT_ID = "unit_id"
CONF_MODEL = "model"

# Recorded-move datasets the daemon preloads at startup. Mirrors
# DEFAULT_DATASETS in reachy_mini/motion/recorded_move.py on the SDK
# side. Add new entries here when the SDK ships a third library.
EMOTIONS_DATASET = "pollen-robotics/reachy-mini-emotions-library"
DANCES_DATASET = "pollen-robotics/reachy-mini-dances-library"
RECORDED_MOVE_DATASETS: tuple[str, ...] = (EMOTIONS_DATASET, DANCES_DATASET)

# Move catalog + playback endpoints. The dataset segment can contain
# '/' (HF repo paths like "pollen-robotics/...") — both endpoints use
# FastAPI :path matching on the daemon side.
ENDPOINT_MOVE_LIST = "/api/move/recorded-move-datasets/list/{dataset}"
ENDPOINT_MOVE_PLAY = "/api/move/play/recorded-move-dataset/{dataset}/{move}"

# Service action surfaced under Developer Tools → Services.
SERVICE_PLAY_RECORDED_MOVE = "play_recorded_move"
