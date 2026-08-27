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
radio inbox, manual addition by radio ID, import/export, per-device
diagnostic sensors (signal strength, last seen, telegram count, sender ID),
Repairs issues and FR/EN translations.

## Supported profiles (MVP)

| EEP | Device | Home Assistant |
|-----|--------|----------------|
| D5-00-01 | Single-input contact | `binary_sensor` (opening; on = open) |
| F6-02-01 / F6-02-02 | 2-rocker wall switch | Device triggers: press, held (after 0.5 s), released after hold, double press (within 0.5 s) per button, plus a generic release |
| D2-01-00..0F (16 types) | 1-channel relays (e.g. NodOn micromodules) | `switch` with confirmed/assumed state; metering types add energy/power sensors + a "Read meter" button |
| D2-05-00 | Blinds/shutter actuator | `cover` with position, stop and status feedback; optional direction inversion for units wired backwards |
| D2-20-02 | Fan control unit | `fan` (percentage, Auto preset) with status feedback |
| A5-02-xx (25 types) | Temperature sensors | `sensor` |
| A5-04-01..03 | Temperature and humidity sensors | `sensor` |
| A5-06-01..05 | Light sensors | `sensor` |
| A5-07-03 | Occupancy sensor | `binary_sensor` (motion) + illuminance/voltage |
| A5-08-01..03 | Light/temperature/occupancy sensors | `sensor` + `binary_sensor` |
| A5-10-xx (33 types) | Room operating panels | `sensor` (temperature, set point, fan...) + `binary_sensor` (incl. battery-low on types 20/21) |
| A5-12-00..03 | Metering (counter, electricity, gas, water) | `sensor` |
| F6-10-00 | Window handle | `sensor` (open / tilted / closed) |

## Rocker device triggers

Rockers create no state entities; they offer device triggers for automations.
The three gesture triggers are synthesised by the integration from the
press/release telegrams the rocker sends anyway (no extra radio traffic) and
their timing is fixed:

| Trigger | Per button | Fires when |
|---------|-----------|------------|
| pressed | yes | The button is pressed (immediately) |
| released | no (any button) | The energy bow is released |
| held | yes | The button has been down for 0.5 s (fires while still down, so a hold-to-dim automation can start on it) |
| released after hold | yes | The button is released after a `held` fired |
| double pressed | yes | The same button is pressed again within 0.5 s of the previous short press's release (fires on the second press, in addition to its `pressed`) |

A short tap therefore gives `pressed` then `released`; a long press gives
`pressed`, `held`, `released`, `released after hold`. Three quick taps give
one `double pressed` (taps 1 and 2), not two.

## Gateway settings and transceiver management

Configure > **Gateway settings**:

- **Query status on startup** (off by default): one status query per
  configured switch and cover when the integration loads, so their state is
  confirmed within seconds after a restart. This transmits once per device
  per reload.
- **Repeater mode** (off by default; level 1 / level 2): makes the USB
  transceiver relay telegrams it hears, to extend range (level 2 also relays
  telegrams that were already repeated once). Writing the setting is an ESP3
  `CO_WR_REPEATER` command to the local module, but be clear about what the
  setting does: while it is on, the stick re-transmits other devices'
  telegrams on the air. It is the one option here that turns the transceiver
  into an active radio participant, which is why it is off unless you choose
  it. The USB300 forgets it on power loss, so it is written again on every
  load and after every reconnect.

Configure > **Transceiver Base ID (recovery)** writes a new Base ID into the
transceiver (`CO_WR_IDBASE`). Its only purpose is to restore the Base ID from
a backup export onto a replacement stick, so actuators paired with the dead
stick keep answering. The module allows about 10 such writes in its whole
life; the step shows the remaining count, validates the range
(FF800000..FFFFFF80) and requires the value to be typed twice. Nothing is
sent over the air.

Configure > **Manage configured devices** opens a per-device menu to edit
(name, room, sender ID and channel for transmitting devices, direction
inversion for covers) or remove a device. Editing keeps entity IDs intact,
because a device is identified by its address and profile, which cannot be
edited. (Entity IDs are derived from the address and the radio channel, so
changing the channel would create new entities. Every profile supported
today is single-channel, so the channel cannot actually change yet.)

## Design rules

- **Passive by default.** The integration only transmits for configured
  D2-01 switch, D2-05 cover and D2-20-02 fan entities, the user-pressed
  "Read meter" button (D2-01 CMD 0x6 queries), the module-parameters step
  (D2-01 CMD 0x2, every field shown before sending), the teach-in
  response during guided pairing (see below), and, if you enable the
  off-by-default "Query status on startup" gateway setting, one status query
  per switch/cover when the integration loads. There is no open learning
  mode and no arbitrary-packet API. Writing the two transceiver settings
  (repeater mode, Base ID) uses ESP3 common commands addressed to the local
  module, not radio. Repeater mode is the one setting whose *effect* is
  on-air: while it is enabled the stick relays other devices' telegrams. It
  is off unless you turn it on.
- **Sender IDs are explicit or paired.** For a device migrated from another
  controller you enter the sender ID it already knows (the transceiver Base ID
  is proposed as a default), so validated historical associations survive. For
  a NEW device, guided pairing allocates the first free Base ID+offset sender
  and answers the device's teach-in with it: either from its discovery card
  (window focused on that device, second teach-in press needed) or via
  Configure > "Pair a new device" (window opens first, one teach-in press,
  first device heard wins). Both windows are time-bounded. Sender IDs outside
  Base ID..Base ID+127 are rejected everywhere.
- **Addresses are exact.** Radio addresses are 8 hex digits; leading zeroes
  are preserved and required.
- **No fake state.** A switch is "assumed" until the actuator's own status
  telegram (D2-01 CMD 0x4) confirms it; a cover's position is unknown until
  its first position reply (D2-05 CMD 0x4); a fan's speed is optimistic
  until its next status message, and its Auto preset is write-only (the EEP
  cannot report it). Unacknowledged commands raise an error instead of
  flipping the UI.
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
  "gateway": {"base_id": "FF974100"},
  "devices": [
    {"address": "0084ACF3", "eep": "D5-00-01", "name": "Front door"},
    {"address": "050A5C20", "eep": "D2-01-0F", "name": "Relay",
     "sender_id": "FF974100", "channel": 0},
    {"address": "051B2C30", "eep": "D2-05-00", "name": "Blind",
     "sender_id": "FF974101", "channel": 0, "invert": true}
  ]
}
```

Optional per-device keys: `area_id` (applied on first creation) and, for
D2-05-00 covers only, `invert` (true swaps open/close and mirrors the
position; rejected on any other profile, the D5-00-01 contact included).
Imports are validated as a whole and previewed as a dry run; nothing is saved
if any record is invalid. Exports contain recovery configuration and no
secrets. The `gateway.base_id` field is what the Base ID recovery step
restores onto a replacement transceiver, so keep an export somewhere off the
Home Assistant host.

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
