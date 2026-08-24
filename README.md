# EnOcean Direct

A Home Assistant custom integration that connects an EnOcean USB transceiver
(USB 300 / FTDI-based sticks) directly to Home Assistant. No MQTT, no bridge,
no daemon.

```text
EnOcean USB transceiver -> enocean_direct -> native HA devices and entities
```

Built on the [enocean-async](https://github.com/henningkerstan/enocean-async)
library (the same library the Home Assistant Core EnOcean integration uses),
with the device management the core integration lacks: a UI config flow, a
radio inbox, manual addition by radio ID, import/export, Repairs issues and
FR/EN translations.

## Supported profiles (MVP)

| EEP | Device | Home Assistant |
|-----|--------|----------------|
| D5-00-01 | Single-input contact | `binary_sensor` (opening; on = open) |
| F6-02-01 / F6-02-02 | 2-rocker wall switch | Device triggers (press per button, release) |
| D2-01-0F | 1-channel relay (e.g. NodOn micromodule) | `switch` with confirmed/assumed state |
| D2-05-00 | Blinds/shutter actuator | `cover` with position, stop and status feedback |

## Design rules

- **Passive by default.** The integration only transmits for configured
  D2-01 switch and D2-05 cover entities, plus the teach-in response during
  guided pairing (see below). There is no open learning mode and no
  arbitrary-packet API.
- **Sender IDs are explicit or paired.** For a device migrated from another
  controller you enter the sender ID it already knows (the transceiver Base ID
  is proposed as a default), so validated historical associations survive. For
  a NEW device, guided pairing from its discovery card allocates the first
  free Base ID+offset sender and answers the device's next teach-in with it;
  the pairing window is time-bounded and focused on that one device. Sender
  IDs outside Base ID..Base ID+127 are rejected everywhere.
- **Addresses are exact.** Radio addresses are 8 hex digits; leading zeroes
  are preserved and required.
- **No fake state.** A switch is "assumed" until the actuator's own status
  telegram (D2-01 CMD 0x4) confirms it; a cover's position is unknown until
  its first position reply (D2-05 CMD 0x4). Unacknowledged commands raise an
  error instead of flipping the UI.
- **One serial owner.** The serial descriptor is owned by one gateway object
  per config entry and is released on unload, reload, failed setup, USB
  disconnect and Home Assistant stop.

## Installation

Requires Home Assistant 2026.8 or later (Python 3.14).

1. Copy `custom_components/enocean_direct` into your `config/custom_components/`
   (or add this repository to HACS as a custom repository).
2. Make sure nothing else owns the serial port (stop any EnOcean MQTT add-on
   first; two processes must never open the transceiver simultaneously).
3. Settings > Devices & Services > Add integration > EnOcean Direct, and pick
   the `/dev/serial/by-id/...` path of the stick.
4. Manage devices via the integration's Configure menu: radio inbox, manual
   add, import/export.

## Import / export schema

```json
{
  "version": 1,
  "devices": [
    {"address": "0084ACF3", "eep": "D5-00-01", "name": "Front door"},
    {"address": "050A5C20", "eep": "D2-01-0F", "name": "Relay",
     "sender_id": "FF974100", "channel": 0}
  ]
}
```

Imports are validated as a whole and previewed as a dry run; nothing is saved
if any record is invalid. Exports contain recovery configuration and no
secrets.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q        # full test suite (no hardware needed)
.venv/bin/ruff check .
```

Tests run against a scripted fake dongle; no test opens a real serial port or
transmits radio telegrams.

## Licence

Apache-2.0. See LICENSE and NOTICE.
