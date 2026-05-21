"""Shared base entity + device-info helper for Reachy Mini.

All sensor and binary_sensor entities subclass :class:`ReachyMiniEntity`
so they share one DeviceInfo and one set of availability semantics —
everything ends up grouped under one Reachy Mini device card in the HA
UI, keyed by the robot's stable ``unit_id``.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MODEL, CONF_UNIT_ID, MODEL_DEFAULT, DOMAIN
from .coordinator import ReachyMiniCoordinator


def build_device_info(
    coordinator: ReachyMiniCoordinator, entry: ConfigEntry
) -> DeviceInfo:
    """Construct the DeviceInfo shown under Settings → Devices."""
    payload: dict[str, Any] = coordinator.data or {}
    unit_id = entry.data.get(CONF_UNIT_ID) or entry.entry_id
    # Variant ("Reachy Mini Wireless" / "Reachy Mini Lite") is captured
    # at config-flow time — from mDNS TXT on discovery or from the
    # daemon-status `wireless_version` flag on manual setup. Falls back
    # to the generic name for legacy entries created before this field
    # was stored. sw_version is the daemon's `/api/daemon/status::version`.
    return DeviceInfo(
        identifiers={(DOMAIN, unit_id)},
        manufacturer="Pollen Robotics",
        model=entry.data.get(CONF_MODEL) or MODEL_DEFAULT,
        name=f"Reachy Mini ({unit_id[:4]})" if len(unit_id) >= 4 else "Reachy Mini",
        sw_version=payload.get("firmware_version"),
        configuration_url=f"http://{coordinator.host}:{coordinator.port}/",
    )


class ReachyMiniEntity(CoordinatorEntity[ReachyMiniCoordinator]):
    """Base class with shared device info and unique-id derivation."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ReachyMiniCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise the shared identity bits."""
        super().__init__(coordinator)
        unit_id = entry.data.get(CONF_UNIT_ID) or entry.entry_id
        self._attr_unique_id = f"{unit_id}_{key}"
        self._attr_device_info = build_device_info(coordinator, entry)
