# Roadmap

Status: v0.1.0 live-validated on real hardware (24/08/2026). Items below are
ordered roughly by value; nothing here is committed to a date.

## Next up

- [ ] **Auto-discovery**: promote radio-inbox hits to native Home Assistant
  discovered-device cards (`async_step_discovery` / discovery flows), so a new
  sender shows up under Settings > Devices & Services without opening the
  options menu. Teach-in telegrams that declare an EEP (UTE, 1BS) can be
  pre-filled; everything else still requires the user to pick the EEP, never
  guessed. Includes de-duplication against configured devices and an
  ignore list.
- [ ] **Module parameters (Jeedom parity)**: per-actuator settings step
  sending D2-01 CMD 0x2 Actuator Set Local (local button enable/disable,
  switch vs toggle mode, power-failure default state), verified against the
  CMD 0x4/0xD status responses. Transmits configuration to physical
  equipment: first live use goes through the same supervised gate as the
  relay test in docs/live-validation.md.
- [ ] **More EEPs from the Jeedom inventory**: add profiles as needed
  (candidates: A5-04-01 temperature/humidity, F6-10-00 window handle,
  D2-01-0B metering actuator). One profile per PR, with telegram fixtures
  and tests first.

## Exploration

- [ ] **Radio topology / repeater insight ("mesh")**: EnOcean has repeaters
  rather than a true mesh. Telegrams already carry repeater count and RSSI;
  expose per-device diagnostics (heard direct vs repeated, signal history)
  and possibly a simple reception-quality view. Scope to be defined; needs
  clarification of the actual goal (coverage debugging? repeater placement?).

## Housekeeping

- [ ] Submit logo/brand assets to home-assistant/brands, then drop
  `ignore: brands` from the HACS CI check.
- [ ] Consider submitting to the HACS default store once the integration has
  settled.
- [ ] Persist the radio inbox across reloads (currently in-memory by design).
- [ ] Parse 4BS teach-in profiles for inbox display (no 4BS EEPs in MVP yet).
- [ ] Friendlier reconfigure: re-validating the same serial path fails with
  `cannot_connect` while the entry holds the port.
