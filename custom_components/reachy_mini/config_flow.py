"""Config flow for Reachy Mini.

Two entry points:

- :meth:`async_step_zeroconf` — triggered automatically by HA's
  zeroconf component when the daemon's `_reachy-mini._tcp.local.`
  service appears on the LAN. The user gets a "Discovered: Reachy
  Mini" card and confirms with one click.
- :meth:`async_step_user` — triggered when the user clicks
  Settings → Devices & Services → "Add Integration" → "Reachy Mini".
  Takes a hostname + port, probes ``/api/daemon/status`` to confirm
  a Reachy Mini daemon is reachable, reads ``hardware_id`` from the
  response to deduplicate against an existing entry.

Both paths converge on the same config entry, identified by `unit_id`
so the same physical robot can never be added twice (re-discoveries
just update the host address).
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.components import zeroconf
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_UNIT_ID,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DOMAIN,
    ENDPOINT_STATUS,
    TXT_ROBOT_NAME,
    TXT_UNIT_ID,
)

_LOGGER = logging.getLogger(__name__)


class ReachyMiniConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the discover-or-manual flow for a Reachy Mini."""

    VERSION = 1

    def __init__(self) -> None:
        """Stash discovery results between steps."""
        self._discovered_host: str | None = None
        self._discovered_port: int = DEFAULT_PORT
        self._discovered_unit_id: str | None = None
        self._discovered_robot_name: str | None = None

    # ------------------------------------------------------------------
    # Zeroconf discovery
    # ------------------------------------------------------------------

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a Reachy Mini surfaced by mDNS."""
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT
        props = discovery_info.properties or {}

        unit_id = props.get(TXT_UNIT_ID)
        if not unit_id:
            # Old daemons (pre-feat/homeassistant) don't advertise
            # unit_id. We can't safely identify the robot without it,
            # so we abort rather than risk duplicate entries.
            _LOGGER.debug("Discovered Reachy Mini at %s without unit_id; aborting", host)
            return self.async_abort(reason="no_unit_id")

        await self.async_set_unique_id(unit_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: host, CONF_PORT: port},
        )

        self._discovered_host = host
        self._discovered_port = port
        self._discovered_unit_id = unit_id
        self._discovered_robot_name = props.get(TXT_ROBOT_NAME) or "reachy_mini"

        # The placeholders show up in the "Discovered" card title.
        self.context["title_placeholders"] = {
            "name": f"Reachy Mini ({unit_id[:4]})",
            "host": host,
        }
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm a discovered Reachy Mini."""
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                description_placeholders={
                    "host": self._discovered_host or "unknown",
                    "unit_id": (self._discovered_unit_id or "unknown")[:8],
                },
            )

        if not await self._probe(self._discovered_host, self._discovered_port):
            return self.async_abort(reason="cannot_connect")

        return self.async_create_entry(
            title=f"Reachy Mini ({(self._discovered_unit_id or '')[:8]})",
            data={
                CONF_HOST: self._discovered_host,
                CONF_PORT: self._discovered_port,
                CONF_UNIT_ID: self._discovered_unit_id,
            },
        )

    # ------------------------------------------------------------------
    # Manual setup
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual hostname/IP entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            payload = await self._fetch_state(host, port)
            if payload is None:
                errors["base"] = "cannot_connect"
            else:
                # ``hardware_id`` on /api/daemon/status corresponds to
                # the ``unit_id`` we set as the config entry's
                # unique_id (same value, different name across the
                # daemon's two surfaces).
                unit_id = payload.get("hardware_id")
                if unit_id:
                    await self.async_set_unique_id(unit_id)
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: host, CONF_PORT: port},
                    )
                # If the daemon doesn't return a unit_id (no robot
                # attached / freshly booted), we still allow the add
                # but use the host as the entry title.
                title = (
                    f"Reachy Mini ({unit_id[:8]})"
                    if unit_id
                    else f"Reachy Mini ({host})"
                )
                return self.async_create_entry(
                    title=title,
                    data={CONF_HOST: host, CONF_PORT: port, CONF_UNIT_ID: unit_id},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="reachy-mini.local"): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _probe(self, host: str | None, port: int) -> bool:
        """Return True iff /api/daemon/status responds with valid JSON."""
        return (await self._fetch_state(host, port)) is not None

    async def _fetch_state(
        self, host: str | None, port: int
    ) -> dict[str, Any] | None:
        """Fetch /api/daemon/status; return None on any failure.

        The daemon stamps ``type: "daemon_status"`` on every response
        of that route — a cheap, daemon-specific sanity check that
        we're actually talking to a Reachy Mini and not an unrelated
        HTTP server that happens to return 200.
        """
        if not host:
            return None
        url = f"http://{host}:{port}{ENDPOINT_STATUS}"
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return None
                payload: dict[str, Any] = await resp.json()
                if payload.get("type") != "daemon_status":
                    return None
                return payload
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return None
