"""Wake/sleep behavior across daemon states.

On the Wireless unit "asleep" means the daemon backend is fully
stopped (`/api/daemon/status` → state "stopped", backend_status null).
In that state every `/api/motors/*` and `/api/move/*` endpoint returns
503 "Backend not running", so:

- Wake up must go through `POST /api/daemon/start?wake_up=true`
  (which enables motor torque before playing the wake move — mirrors
  the official dashboard).
- When the backend IS running but torque is off, the bare wake move
  plays the sound without moving; the button must enable motors first.
- Go to sleep mirrors the dashboard's `POST /api/daemon/stop?goto_sleep=true`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.reachy_mini.button import (
    ReachyMiniGotoSleepButton,
    ReachyMiniWakeUpButton,
)
from custom_components.reachy_mini.select import ReachyMiniMotorModeSelect

from .conftest import BASE_URL


def _mock_poll_endpoints(
    aioclient_mock, state: str, motor_mode: str | None = None
) -> None:
    """Mock the coordinator's GET fan-out for a given daemon state."""
    backend = None if motor_mode is None else {"motor_control_mode": motor_mode}
    aioclient_mock.get(
        f"{BASE_URL}/api/daemon/status",
        json={
            "state": state,
            "version": "1.8.4",
            "hardware_id": "1f66778465450401",
            "robot_name": "reachy_mini",
            "backend_status": backend,
        },
    )
    # Backend-gated endpoints 503 while the backend is down; keep them
    # failing in all tests — the fields under test don't come from them.
    aioclient_mock.get(f"{BASE_URL}/api/daemon/robot-app-lock-status", status=503)
    aioclient_mock.get(f"{BASE_URL}/api/state/doa", status=503)
    aioclient_mock.get(f"{BASE_URL}/api/volume/current", status=503)
    aioclient_mock.get(f"{BASE_URL}/api/volume/microphone/current", status=503)


def _posts(aioclient_mock) -> list[tuple[str, str, str]]:
    """(method, path, query-string) of every request the mock saw."""
    return [
        (method, url.path, url.query_string)
        for method, url, *_ in aioclient_mock.mock_calls
    ]


async def test_coordinator_reports_stopped_daemon_as_not_awake(
    hass, coordinator, aioclient_mock
) -> None:
    """A stopped backend means asleep — not 'unknown'."""
    _mock_poll_endpoints(aioclient_mock, state="stopped", motor_mode=None)

    data = await coordinator._async_update_data()

    assert data["daemon_state"] == "stopped"
    assert data["awake"] is False
    assert data["motor_mode"] is None


async def test_coordinator_derives_awake_from_motor_mode_when_running(
    hass, coordinator, aioclient_mock
) -> None:
    """Running backend keeps the motor-mode-based derivation."""
    _mock_poll_endpoints(aioclient_mock, state="running", motor_mode="enabled")

    data = await coordinator._async_update_data()

    assert data["daemon_state"] == "running"
    assert data["awake"] is True
    assert data["motor_mode"] == "enabled"


async def test_wake_button_starts_daemon_when_stopped(
    hass, coordinator, config_entry, aioclient_mock
) -> None:
    """From the asleep state, wake goes through daemon start."""
    coordinator.async_set_updated_data({"daemon_state": "stopped"})
    aioclient_mock.post(
        f"{BASE_URL}/api/daemon/start?wake_up=true", json={"job_id": "1"}
    )
    button = ReachyMiniWakeUpButton(coordinator, config_entry)

    with patch.object(coordinator, "async_request_refresh", AsyncMock()):
        await button.async_press()

    assert _posts(aioclient_mock) == [
        ("POST", "/api/daemon/start", "wake_up=true"),
    ]


async def test_wake_button_enables_motors_before_wake_move_when_running(
    hass, coordinator, config_entry, aioclient_mock
) -> None:
    """With the backend up but torque off, enable motors then play the move."""
    coordinator.async_set_updated_data(
        {"daemon_state": "running", "motor_mode": "disabled"}
    )
    aioclient_mock.post(f"{BASE_URL}/api/motors/set_mode/enabled", json={})
    aioclient_mock.post(f"{BASE_URL}/api/move/play/wake_up", json={"uuid": "u"})
    button = ReachyMiniWakeUpButton(coordinator, config_entry)

    with patch.object(coordinator, "async_request_refresh", AsyncMock()):
        await button.async_press()

    assert _posts(aioclient_mock) == [
        ("POST", "/api/motors/set_mode/enabled", ""),
        ("POST", "/api/move/play/wake_up", ""),
    ]


async def test_sleep_button_stops_daemon_when_running(
    hass, coordinator, config_entry, aioclient_mock
) -> None:
    """Sleep mirrors the dashboard: daemon stop with goto_sleep."""
    coordinator.async_set_updated_data(
        {"daemon_state": "running", "motor_mode": "enabled"}
    )
    aioclient_mock.post(
        f"{BASE_URL}/api/daemon/stop?goto_sleep=true", json={"job_id": "1"}
    )
    button = ReachyMiniGotoSleepButton(coordinator, config_entry)

    with patch.object(coordinator, "async_request_refresh", AsyncMock()):
        await button.async_press()

    assert _posts(aioclient_mock) == [
        ("POST", "/api/daemon/stop", "goto_sleep=true"),
    ]


async def test_sleep_button_errors_when_already_asleep(
    hass, coordinator, config_entry, aioclient_mock
) -> None:
    """Pressing sleep while stopped surfaces a friendly error, no HTTP."""
    coordinator.async_set_updated_data({"daemon_state": "stopped"})
    button = ReachyMiniGotoSleepButton(coordinator, config_entry)

    with pytest.raises(HomeAssistantError, match="asleep"):
        await button.async_press()

    assert not aioclient_mock.mock_calls


async def test_motor_mode_select_errors_when_daemon_stopped(
    hass, coordinator, config_entry, aioclient_mock
) -> None:
    """Selecting a motor mode while asleep explains itself instead of 503."""
    coordinator.async_set_updated_data({"daemon_state": "stopped", "motor_mode": None})
    select = ReachyMiniMotorModeSelect(coordinator, config_entry)

    with pytest.raises(HomeAssistantError, match="[Ww]ake"):
        await select.async_select_option("enabled")

    assert not aioclient_mock.mock_calls
