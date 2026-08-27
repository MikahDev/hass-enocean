# Supervised live validation runbook

Status: NOT EXECUTED. Every step below requires explicit human approval and
on-site presence. There is exactly one transceiver; the previous EnOcean MQTT
UI add-on remains the rollback owner of the serial port at all times.

Steps that change the live installation are marked [LIVE]. Steps that can
transmit radio are marked [TX] and additionally require separate approval at
that step.

Reference hardware facts (validated previously):

- Serial path: /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A90551DQ-if00-port0
- Base ID: FF974100
- Pilot actuator: NodOn D2-01-0F, address 050A5C20, sender FF974100, radio
  channel 0 (Channel 1), controllable without new teach-in.
- Contact: D5-00-01 (exact 8-digit address from Jeedom inventory).

## Phase A - preparation (no serial changes)

A1. Take a full Home Assistant backup (Settings > System > Backups).
    Pass: backup completes and is downloadable. Fail: stop.
A2. Export the current EnOcean MQTT UI add-on configuration and device list;
    store alongside the backup.
    Pass: file saved off the host. Fail: stop.
A3. Record versions: HA Core, the MQTT add-on, this integration (0.1.0).
A4. Confirm the by-id serial path exists: `ls -l /dev/serial/by-id/`.
    Pass: the FT232R path is present. Fail: stop.
A5. Install the custom integration files WITHOUT configuring it. [LIVE]
    Pass: HA restarts cleanly and the integration appears in the add list,
    unconfigured; the MQTT add-on still owns the port and devices still work.
    Fail/rollback: remove custom_components/enocean_direct, restart.

## Phase B - swap the serial owner (receive-only)

B1. Stop the EnOcean MQTT UI add-on cleanly and disable "start on boot". [LIVE]
    Pass: add-on stopped. Rollback at ANY later failure: re-enable and start
    the add-on, remove the enocean_direct config entry.
B2. Verify nothing owns the port: `fuser -v /dev/ttyUSB0` (or
    `lsof /dev/ttyUSB0`) returns no process.
    Pass: no owner. Fail: rollback B1.
B3. Add the EnOcean Direct integration via the UI, selecting the by-id path. [LIVE]
    Pass: entry loads, title shows Base ID FF974100. A different Base ID is a
    hard fail (wrong stick or wrong port): rollback.
B4. Confirm exactly one serial reader: `fuser -v /dev/ttyUSB0` lists only the
    Home Assistant process.
B5. Configure the D5-00-01 contact by its exact Jeedom address (manual add).
    Physically open and close it once each.
    Pass: HA shows Open when physically open and Closed when physically
    closed, within 2 seconds. An inverted reading is a hard fail: do not
    "fix" it by swapping semantics in config; stop and investigate.
    (Note: the current MQTT add-on shows this contact inverted; the new
    reading is expected to flip relative to the old dashboard.)
B6. Configure one F6 rocker; press and release each rocker side.
    Pass: device triggers fire matching the physical button (check the
    device page's trigger log or a test automation), no state entity exists.
B7. Reload the config entry 5 times from the UI.
    Pass: each reload succeeds; after each, B4 still shows one reader; no
    duplicate events on a rocker press.
B8. Unplug the USB stick, wait 10 seconds, replug. [LIVE]
    Pass: entities become unavailable, a Repairs issue appears; within ~60 s
    of replug the entry reconnects, entities recover, the issue clears.
    Fail: rollback (B1 rollback path) and file a bug.
B9. Soak receive-only for at least one day if desired. The MQTT add-on stays
    stopped during the soak; rollback remains available at any moment.

## Phase C - single bounded command test [TX]

Requires separate explicit approval and someone physically at the relay.

C1. Confirm the switch entity for 050A5C20 shows sender FF974100 and
    Channel 1 (radio 0) in its attributes.
C2. With the load observed, toggle the switch ON once from HA.
    Pass: the relay physically switches; the entity turns on; within a few
    seconds the state shows confirmed (assumed_state attribute gone,
    state_confirmed: true) via the actuator's CMD 0x4 status telegram.
    Fail: if the relay did not switch, do not retry more than once; rollback.
    A UI success without physical movement is a FAIL.
C3. Toggle OFF once; same criteria.
C4. Operate the actuator's physical button once; HA must follow the state
    without any command being sent.

## Phase E - v0.4.0 additions: D2-05-00 cover and guided pairing [TX]

Each step requires separate explicit approval and someone physically at the
device. Do not run Phase E until Phases A-C have passed.

E1. Guided pairing of the D2-05-00 blinds actuator, either path:
    discovery card (press LRN, open the card, choose Pair, press LRN again
    within the 60 s window) or one-press (Configure > Pair a new device,
    then press LRN once; the window answers the first teach-in heard, so
    make sure no other device is teaching in).
    Pass: the card completes with an allocated sender (Base ID + first free
    offset) stored in the device attributes; the actuator confirms the
    teach-in per its manual (LED/beep). Fail: the window times out; do not
    retry more than once before checking logs.
E2. With the blind observed, command Open once, then Stop mid-travel, then
    Close.
    Pass: physical movement matches each command; the entity position follows
    the actuator's CMD 0x4 replies (position appears after the first reply,
    opening/closing states shown during travel). A UI success without
    physical movement is a FAIL.
E3. Set position 50%.
    Pass: the blind stops near mid-travel and HA settles on the reported
    position (HA % = 100 - EnOcean %; 100 = open in HA).
E4. Move the blind from its local control (if fitted); HA must follow without
    sending any command.

## Phase F - v0.10.0: module parameters (D2-01 CMD 0x2) [TX]

Requires separate explicit approval and someone physically at the relay.
CMD 0x2 writes ALL local parameters in one telegram; note the module's
factory values from its manual before starting.

F1. Configure > Configure actuator parameters > pilot relay. Submit the form
    with the module's known factory values, changing ONLY "Local button
    enabled" to off.
    Pass: transceiver acknowledges; the relay's physical button no longer
    toggles the load; HA control still works.
F2. Re-submit with "Local button enabled" on.
    Pass: the physical button works again. Fail on either step: re-submit
    factory values, verify, and file a bug.
F3. Optional (only if the installation wants it): set "State after a power
    failure" and power-cycle the module's supply once to verify the chosen
    state; then confirm the taught-in rocker still controls the relay.

## Phase G - v0.11.0/v0.12.0: metering and fan [TX]

G1. Metering D2-01 (if fitted): press the "Read meter" button once with a
    known load on. Pass: energy and power sensors update within a few
    seconds and the power value is plausible for the load.
G2. D2-20-02 fan (if fitted): set 50%, then off, then Auto, observing the
    unit. Pass: physical behaviour matches; the percentage follows the
    unit's own status messages (Auto shows as a preset without a
    percentage until the unit reports a numeric speed).

## Phase H - v0.14.0..v0.16.0: gestures, inversion, transceiver settings

H1. Rocker gestures (receive-only, no approval gate). On the configured F6
    rocker: hold AO for about a second, then release; tap AO twice quickly.
    Pass: the device page's trigger log (or a test automation) shows
    pressed / held / released / released after hold for the hold, and
    pressed / released / pressed / double pressed / released for the double
    tap. A `held` that fires on a short tap, or a `double pressed` on two
    taps more than a second apart, is a FAIL.
H2. Cover direction inversion [TX] (only if a D2-05 unit is wired
    backwards; otherwise skip). Configure > Manage > the cover > Edit >
    Invert direction. With the blind observed, command Open once.
    Pass: the blind physically opens and HA settles on a position near 100.
    Fail: switch inversion back off before anything else.
H3. Repeater mode (local module write, no radio). Configure > Gateway
    settings > Repeater mode = Level 1; the entry reloads.
    Pass: the entry loads and the log shows no "rejected by the module"
    warning. Unplug and replug the stick: after reconnect the setting is
    written again (debug log shows a COMMON_COMMAND 0x09 send). Note this
    only proves the module ACCEPTED the setting; proving it actually repeats
    needs a device that is out of direct range of the stick but in range of
    it, which this installation may not have. Repeating makes the stick
    re-transmit other devices' telegrams, so set it back to Off afterwards
    unless the installation wants that; Off is also written explicitly.
H4. Base ID recovery [LIVE, IRREVERSIBLE]. THIS BURNS ONE OF THE MODULE'S
    ~10 LIFETIME BASE ID WRITES. Do NOT run it on the production stick as a
    test. Run it only (a) on a spare stick you accept losing a write cycle
    on, or (b) for a real replacement of a dead stick. Requires separate
    explicit approval at this step.
    H4a. Take a fresh device export first (it carries the Base ID to restore).
    H4b. Configure > Transceiver Base ID (recovery). Confirm the shown
         current Base ID matches the stick and note the remaining write
         cycles (a factory stick reports 10).
    H4c. Enter the Base ID from the export, then type it again on the
         confirmation step.
    Pass: the abort message reports the new Base ID; the entry reloads and
    its title shows the new Base ID; the remaining count dropped by exactly
    one; paired actuators answer commands from the restored senders (Phase
    C style single bounded command). Any error message: do NOT retry
    blindly; each attempt may consume a write cycle. Read the message
    (not supported / max reached / not written / mismatch after write) and
    check the stick's Base ID after a replug before deciding anything.

## Phase D - close-out

D1. If all checks pass: uninstall or leave the MQTT add-on disabled (do not
    delete its configuration; it stays the documented rollback).
D2. If any check fails: stop the enocean_direct entry (disable it), re-enable
    and start the EnOcean MQTT UI add-on, verify devices work again, and
    record the failure with logs (Settings > System > Logs, filter
    enocean_direct / enocean_async).

## Explicit non-actions

- No teach-in is performed in Phases A-D. The relay keeps its historical
  association with sender FF974100.
- No telegram is transmitted before Phase C, and Phase C sends only the two
  bounded switch commands to the validated relay. This holds as long as the
  "Query status on startup" gateway setting stays off (it is off by default);
  enabling it transmits one status query per switch and cover at every load,
  so leave it off until Phase C has passed. Repeater mode (Phase H3) must
  likewise stay off until then: it makes the stick relay telegrams on air.
- The only teach-in response ever sent is the one in E1: user-initiated,
  focused on the one device being paired, inside a 60 s window.
