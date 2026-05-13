# Reachy Mini Home Assistant integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

Adds a [Reachy Mini](https://github.com/pollen-robotics/reachy_mini) to Home
Assistant with **zero YAML**. Drops a "Reachy Mini" device into your HA
instance, polls the robot's daemon every 30 s, and creates sensors for
motor state, active app, host CPU/memory, audio levels, IMU pitch/roll/
temperature, microphone-array direction of arrival, and WebRTC session
presence.

Works alongside the existing `_reachy-mini._tcp.local.` mDNS
advertisement and `GET /api/homeassistant/state` endpoint that ship in
the Reachy Mini SDK — no extra protocols, no broker, no auth.

## Install

### Via HACS (recommended)

> Until this repo is added to the default HACS registry, install it as a
> [Custom Repository](https://hacs.xyz/docs/faq/custom_repositories/).

1. Open HACS → **Integrations** → ⋮ → **Custom repositories**.
2. Add `https://github.com/pollen-robotics/reachy_mini_homeassistant`
   with category **Integration**, then click **Add**.
3. Find **Reachy Mini** in the HACS list and click **Download**.
4. Restart Home Assistant.

### Manual install (no HACS)

1. Copy the `custom_components/reachy_mini/` folder into your HA config
   directory: `<config>/custom_components/reachy_mini/`.
2. Restart Home Assistant.

## Use

1. Make sure your Reachy Mini daemon is running on the **same LAN** and
   on a recent version (the `/api/homeassistant/state` endpoint must
   exist — that's the [`feat/homeassistant` branch](https://github.com/pollen-robotics/reachy_mini/tree/feat/homeassistant)
   or any release that includes it).
2. Within ~30 s of HA starting, you'll see a **"Discovered: Reachy Mini"**
   card under **Settings → Devices & Services**. Click **Add**.
3. Confirm — your Reachy Mini appears as a device with all sensors
   grouped underneath, identified by its stable `unit_id`.

No DNS / no IPs to type. If `.local` resolution is broken on your
network, you can still **Manual configuration** with the daemon's IP.

## What you get

The integration creates one **device** per Reachy Mini, identified by
its stable `unit_id` (a hash of the audio device serial). Underneath:

### Sensors

| Entity | Unit | Notes |
|---|---|---|
| Active app | — | Local Python app or WebRTC peer name |
| Active app transport | — | `local` or `webrtc` |
| Motor mode | — | `enabled` / `disabled` / `gravity_compensation` |
| Speaker volume | % | 0-100 |
| Microphone volume | % | 0-100 |
| Voice direction | rad | 0 = left, π/2 = front, π = right |

### Binary sensors

| Entity | Device class | Notes |
|---|---|---|
| Awake | `power` | True when motors are enabled or in gravity comp |
| WebRTC active | `connectivity` | True when a remote session holds the slot |
| Speech detected | `sound` | Mic-array VAD signal (LAN-side speech-activity VAD) |

### Not yet exposed

The SDK doesn't currently expose REST routes for these; they're easy
additive extensions if anyone wants them:

| Field | Status |
|---|---|
| IMU pitch / roll / temperature | Wireless-only sensors. Add a `/api/state/imu` route in the SDK and the integration picks it up. |
| CPU / memory / uptime | Daemon-process health metrics. Could be added as additive fields to `/api/daemon/status`. |

## Blueprints

Six ready-made automation blueprints ship in the
`blueprints/automation/reachy_mini/` folder of this repo. Once you have
the integration set up, import them by clicking the badge in each row:

| Blueprint | Import |
|---|---|
| Notify when robot wakes up | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fpollen-robotics%2Freachy_mini_homeassistant%2Fmain%2Fblueprints%2Fautomation%2Freachy_mini%2Fnotify_on_wake.yaml) |
| Dim lights when someone speaks to it | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fpollen-robotics%2Freachy_mini_homeassistant%2Fmain%2Fblueprints%2Fautomation%2Freachy_mini%2Fdim_lights_on_speech.yaml) |
| Activate a scene when a specific app launches | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fmy.home-assistant.io%2Fredirect%2Fblueprint_import%2F%3Fblueprint_url%3Dhttps%253A%252F%252Fraw.githubusercontent.com%252Fpollen-robotics%252Freachy_mini_homeassistant%252Fmain%252Fblueprints%252Fautomation%252Freachy_mini%252Fscene_on_app_launch.yaml) |
| Run an action with the speaker's direction | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fpollen-robotics%2Freachy_mini_homeassistant%2Fmain%2Fblueprints%2Fautomation%2Freachy_mini%2Frun_with_doa.yaml) |
| Notify when the IMU runs hot | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fpollen-robotics%2Freachy_mini_homeassistant%2Fmain%2Fblueprints%2Fautomation%2Freachy_mini%2Fimu_thermal_warning.yaml) |
| Daily status report | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fpollen-robotics%2Freachy_mini_homeassistant%2Fmain%2Fblueprints%2Fautomation%2Freachy_mini%2Fdaily_status_report.yaml) |

Each blueprint pre-fills its entity dropdowns with the names produced by
this integration; you only have to pick the action targets.

## Troubleshooting

**HA doesn't show the "Discovered" card.**

- Your daemon is running but on an older version that doesn't advertise
  the `model=ReachyMini` TXT record. Upgrade to the SDK build that
  includes the `feat/homeassistant` work.
- mDNS isn't traversing your network. Confirm with `avahi-browse -ar`
  (Linux) or `dns-sd -B _reachy-mini._tcp` (macOS).
- HA's zeroconf component is disabled. Re-enable via Settings →
  Integrations.

**"Cannot connect" when adding manually.**

`curl http://<host>:8000/api/homeassistant/state` — if that returns a
JSON blob with `schema_version`, the integration should also work. If
not, check the daemon logs.

**Entities show `unknown` after add.**

The daemon backend hasn't finished starting yet (motor configuration
takes ~5-10 s on first boot). Wait one update interval (30 s).

## Design notes

This integration is intentionally tiny: ~400 lines of Python. It
consumes the `GET /api/homeassistant/state` endpoint that ships in the
upstream Reachy Mini SDK; **no protocol logic, no custom queries, no
schema parsing beyond the JSON fields** the SDK already documents.

That means the integration depends only on the SDK's documented
`schema_version: 1` contract. When the schema bumps, this integration
bumps too — never silently.

## License

Apache 2.0 — same as the upstream Reachy Mini SDK.
