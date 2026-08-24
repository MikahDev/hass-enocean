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
  is limited to D2-01 switch and D2-05 cover commands from configured entities, plus
  the UTE teach-in response inside a guided-pairing window (user-initiated from a
  discovery card, focused on one device, time-bounded by PAIRING_TIMEOUT).
- Sender IDs are validated against the transceiver Base ID range (base to base+127).
  They are user-provided, except guided pairing which allocates the first free
  base+offset (offset 1..127; offset 0 stays the manual default proposal).
- D5-00-01 CO bit: 0 = open, 1 = closed. binary_sensor is_on means open. Do not invert.
- Device addresses are exactly 8 hex digits, leading zeroes preserved.
- Target: Home Assistant 2026.8 (Python >= 3.14.2), enocean-async pinned in manifest.json.
- Do not push or open PRs without explicit approval.
