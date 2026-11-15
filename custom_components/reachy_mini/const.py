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
# SourceKind.INSTALLED filter — only apps already installed on the
# daemon, not the full HF Space / dashboard / local catalog.
ENDPOINT_APPS_LIST_INSTALLED = "/api/apps/list-available/installed"
ENDPOINT_APP_START = "/api/apps/start-app/{app_name}"  # path-templated
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

# Service actions surfaced under Developer Tools → Services.
SERVICE_PLAY_RECORDED_MOVE = "play_recorded_move"
SERVICE_START_CONVERSATION = "start_conversation"
SERVICE_GET_PERSONALITY = "get_personality"
SERVICE_SAVE_PERSONALITY = "save_personality"
SERVICE_DELETE_PERSONALITY = "delete_personality"

# --- Conversation app -------------------------------------------------
# pollen-robotics/reachy_mini_conversation_app: its installed name (also
# the app-lock holder name while it runs) and the JSON-RPC WebSocket its
# settings server mounts (custom_app_url = http://0.0.0.0:7860/).
CONVERSATION_APP_NAME = "reachy_mini_conversation_app"
CONVERSATION_RPC_PORT = 7860
CONVERSATION_RPC_PATH = "/rpc"
# Personality the app stores for sessions started with extra
# instructions — overwritten on every such start.
CONVERSATION_SESSION_PROFILE = "home_assistant_session"
# Its selection name, as personalities.list reports it — hidden from the
# personality select, since it's rebuilt from the chosen base anyway.
CONVERSATION_SESSION_SELECTION = f"user_personalities/{CONVERSATION_SESSION_PROFILE}"
# Quick best-effort personality fetch at setup; never blocks setup long.
CONVERSATION_LIST_TIMEOUT = 3.0
# Personality get/save/delete against an already-running app.
CONVERSATION_MANAGE_TIMEOUT = 15.0

# Waiting for the daemon backend to come up from sleep.
DAEMON_START_TIMEOUT = 60.0
# Waiting for the conversation app to boot and its realtime backend to
# connect (it also reconnects after a personality change).
CONVERSATION_READY_TIMEOUT = 90.0
# stop-current-app waits up to 20 s daemon-side for the app to exit.
APP_STOP_TIMEOUT = 25.0

# --- Camera / WebRTC stream -------------------------------------------
# The daemon's webrtcsink runs a gst-webrtc-signalling server on this
# port; the camera+audio producer is advertised with this meta name
# (see reachy_mini/media/media_server.py on the SDK side).
SIGNALLING_PORT = 8443
PRODUCER_NAME = "reachymini"

# Tear the robot session down this many seconds after the last HA
# consumer (still image or MJPEG stream) goes away.
CAMERA_IDLE_TIMEOUT = 10.0

# After the robot side fails or ends the session, don't reconnect for
# this long — keeps auto-refreshing dashboards from hammering the robot.
CAMERA_RETRY_COOLDOWN = 5.0

# Live-view frame pacing. 10 fps is sized for a Raspberry Pi 5 host
# software-decoding 720p30 H264 and JPEG-encoding on demand.
CAMERA_MJPEG_FPS = 10

# aiortc's default DTLS cipher list is ECDSA-only; the robot's Linux
# GStreamer build has an RSA DTLS certificate (verified live: Chrome
# negotiates TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256), while the macOS
# build generates an ECDSA one — offer both suite families.
DTLS_CIPHER_LIST = (
    b"ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
    b"ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
    b"ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
    b"ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA"
)
