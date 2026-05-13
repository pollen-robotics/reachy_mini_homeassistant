"""Constants for the Reachy Mini integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "reachy_mini"

# Reachy Mini daemon defaults — match upstream src/reachy_mini/daemon/app/main.py.
DEFAULT_PORT = 8000
ENDPOINT_PATH = "/api/homeassistant/state"

# Polling. Default matches the HA `rest:` blueprint cadence so users
# get the same experience whether they install this integration or
# paste the YAML.
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
DEFAULT_TIMEOUT = 5.0

# TXT record keys exposed by the SDK's _reachy-mini._tcp.local. service.
TXT_UNIT_ID = "unit_id"
TXT_MODEL = "model"
TXT_MANUFACTURER = "manufacturer"
TXT_VERSION = "version"
TXT_ROBOT_NAME = "robot_name"

# Schema versions we know how to consume. If the daemon reports
# something newer we still ingest the response but log a warning —
# field renames or removals would require a new release of this
# integration.
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

# Config entry data keys.
CONF_UNIT_ID = "unit_id"
