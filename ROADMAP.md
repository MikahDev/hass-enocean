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
- [x] **Module parameters (Jeedom parity)** (v0.10.0): per-actuator settings
  step sending D2-01 CMD 0x2 Actuator Set Local (local button enable/disable,
  taught-in devices, over-current restart, power-failure detection and
  default state), encoded through the library's EEP spec; the form shows
  every field because the telegram writes them all at once. Not included:
  switch-vs-toggle external interface mode (that is CMD 0xB, a separate
  telegram; add when needed). Transmits configuration to physical equipment:
  first live use goes through the supervised gate (Phase F) in
  docs/live-validation.md.
- [x] **EEP expansion, wave 1 — D2-05-00 cover** (v0.4.0, owned hardware): native
  `cover` entity via the library's D2-05 handler (position, stop, status
  feedback), same explicit sender-ID rules as D2-01. Transmits: first live
  movement goes through a supervised gate like the relay test.
- [x] **EEP expansion, wave 2 — receive-only sensors** (v0.7.0, safe, no TX):
  profiles the enocean-async library decodes, exposed as native sensors:
  A5-02-xx temperature, A5-04-xx temperature/humidity, A5-06-xx light,
  A5-07-03 occupancy, A5-08-xx, A5-10-xx room panels, A5-12-00..03
  metering, F6-10-00 window handle. Fixture-tested: hand-pinned telegram
  bytes for flagship profiles, encoder-built decode cases for scaling and
  label variants, and an entity-creation check across all 75 profiles.
- [x] **EEP expansion, wave 3a — D2-01 family** (v0.11.0): all 16
  single-channel D2-01 types as switches; metering variants (02/03/05/07/
  0B/0C/0E) additionally get energy and power sensors (CMD 0x7 responses,
  decoded locally so the per-device wire unit Ws/Wh/kWh/W/kW is normalised
  to Wh/W) and a "Read meter" button sending the CMD 0x6 queries.
- [ ] **EEP expansion, wave 3b — remaining actuators**: multi-channel D2-01
  types 10..16 (need one record per channel: the device model keys records
  by address today), D2-20-02 fan, A5-20-01 valve, A5-38-08 central
  command / Eltako dimmers (would introduce light/fan platforms).
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
- [x] Persist the radio inbox across reloads (v0.9.0; still in-memory only,
  not across restarts).
- [x] Parse 4BS teach-in profiles for inbox display (v0.7.0, with wave 2).
- [x] Friendlier reconfigure (v0.9.0): the entry releases the port before
  probing, and a failed probe puts the previous configuration back in service.
- [ ] Known ceiling (upstream): some wave-2 specs carry availability/presence
  flags (A5-04 TSN, A5-06-04 TMPAV/ENAV, A5-10-1F TMP_F/SP_F/FAN_F) that
  enocean-async decodes but does not gate on, so an unpopulated field can
  surface as its scale minimum. Needs semantic resolvers upstream; consider
  contributing to the library.
