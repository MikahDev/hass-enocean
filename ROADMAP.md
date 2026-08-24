# Roadmap

Status: v0.1.0 live-validated on real hardware (24/08/2026). Items below are
ordered roughly by value; nothing here is committed to a date.

## Next up

- [x] **Auto-discovery** (v0.2.0): promote radio-inbox hits to native Home Assistant
  discovered-device cards (`async_step_discovery` / discovery flows), so a new
  sender shows up under Settings > Devices & Services without opening the
  options menu. Teach-in telegrams that declare an EEP (UTE, 1BS) can be
  pre-filled; everything else still requires the user to pick the EEP, never
  guessed. Includes de-duplication against configured devices and an
  ignore list.
- [x] **Guided pairing (Jeedom-style)** (v0.4.0; v0.5.0 adds one-press pairing
  from the Configure menu, window first): a "Pair" path on the discovery
  card for NEW devices: automatically allocate the first free Base ID+offset
  sender (library sender-slot tracking), answer the UTE teach-in with it,
  and store it, so the user never types a sender ID for newly paired
  devices. Migrated devices keep their historical sender (that value lives
  in the device, not in the controller, and cannot be allocated). Transmits
  a teach-in response: bounded to an explicit user action on one device,
  first live use behind a supervised gate.
- [x] **Per-device diagnostics** (v0.6.0, safe, no TX): signal strength (RSSI
  dBm), last seen, and telegram count (disabled by default) as diagnostic
  sensor entities on every configured device, fed by every incoming ERP1
  telegram. A sender ID diagnostic entity on actuators/covers makes the
  allocated or historical sender visible for debugging without opening the
  entity attributes. Feeds into the repeater insight exploration below
  (same telegram metadata).
- [ ] **Module parameters (Jeedom parity)**: per-actuator settings step
  sending D2-01 CMD 0x2 Actuator Set Local (local button enable/disable,
  switch vs toggle mode, power-failure default state), verified against the
  CMD 0x4/0xD status responses. Transmits configuration to physical
  equipment: first live use goes through the same supervised gate as the
  relay test in docs/live-validation.md.
- [x] **EEP expansion, wave 1 — D2-05-00 cover** (v0.4.0, owned hardware): native
  `cover` entity via the library's D2-05 handler (position, stop, status
  feedback), same explicit sender-ID rules as D2-01. Transmits: first live
  movement goes through a supervised gate like the relay test.
- [ ] **EEP expansion, wave 2 — receive-only sensors** (safe, no TX):
  profiles the enocean-async library decodes, exposed as native sensors:
  A5-02-xx temperature, A5-04-xx temperature/humidity, A5-06-xx light,
  A5-07-03 occupancy, A5-08-xx, A5-10-xx room panels, A5-12-00..03
  metering, F6-10-00 window handle. Fixture-tested per profile.
- [ ] **EEP expansion, wave 3 — remaining actuators**: other D2-01 types
  (incl. metering variants as power/energy sensors), D2-20-02, A5-20-01
  valve, A5-38-08 central command / Eltako dimmers.
- Non-goal: a generic EEP database covering the whole EnOcean catalogue.
  Profiles are added individually with telegram fixtures and tests; that is
  what keeps semantics (like the D5 contact polarity) verifiably correct.

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
