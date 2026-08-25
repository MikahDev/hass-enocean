# hass-enocean

Home Assistant custom integration `enocean_direct` (custom_components/enocean_direct).
Talks to an EnOcean USB transceiver directly via the enocean-async library. No MQTT.

## Commands

- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`
- Tests: `.venv/bin/python -m pytest -q`
- Lint: `.venv/bin/ruff check . && .venv/bin/ruff format --check .`

## Hard constraints

- Never open a real serial device from tests. All tests use the FakeDongle in tests/conftest.py.
- Never add an open learning mode or an arbitrary-packet send service. Transmission
  is limited to D2-01 switch and D2-05 cover commands from configured entities,
  D2-01 CMD 0x2 Actuator Set Local from the module-parameters options step
  (spec-encoded, every field explicit in the form), plus
  teach-in responses the library sends inside a guided-pairing window
  (user-initiated from a discovery card or the Configure menu, time-bounded by
  PAIRING_TIMEOUT; the discovery-card window is focused on that one device, the
  menu window accepts the first supported teach-in heard). Wave-2 sensor profiles
  are receive-only: no sender, no channel, never paired.
- Sender IDs are validated against the transceiver Base ID range (base to base+127).
  They are user-provided, except guided pairing which allocates the first free
  base+offset (offset 1..127; offset 0 stays the manual default proposal).
- D5-00-01 CO bit: 0 = open, 1 = closed. binary_sensor is_on means open. Do not invert.
- Device addresses are exactly 8 hex digits, leading zeroes preserved.
- Target: Home Assistant 2026.8 (Python >= 3.14.2), enocean-async pinned in manifest.json.
- Do not push or open PRs without explicit approval.
