"""Metering D2-01 types: energy/power sensors, unit normalisation, read button.

CMD 0x7 wire units vary per device (Ws/Wh/kWh/W/kW); the integration
normalises to Wh and W locally because the library's observation drops the
unit. All telegram bytes are hand-pinned.
"""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import FakeDongle, erp1_frame, flush, make_entry, setup_entry

METER = {
    "address": "050A6D00",
    "eep": "D2-01-0E",
    "name": "Meter relay",
    "sender_id": "FF974102",
    "channel": 0,
}
METER_INT = int(METER["address"], 16)
EXPECTED_OPTIONAL = bytes.fromhex("03050A6D00FF00")


async def _setup(hass: HomeAssistant):
    entry = make_entry(hass, [METER])
    assert await setup_entry(hass, entry)
    return entry


async def test_metering_type_is_a_switch_too(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    assert hass.states.get("switch.meter_relay") is not None
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.meter_relay"}, blocking=True
    )
    data, optional = dongle.sent_radio[0]
    assert data == bytes.fromhex("D2010064FF97410200")
    assert optional == EXPECTED_OPTIONAL


async def test_read_meter_button_bytes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.meter_relay_read_meter"}, blocking=True
    )
    assert [data for data, _ in dongle.sent_radio] == [
        bytes.fromhex("D20600FF97410200"),  # CMD 0x6, query energy, channel 0
        bytes.fromhex("D20620FF97410200"),  # CMD 0x6, query power, channel 0
    ]


async def test_measurement_unit_normalisation(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    energy = "sensor.meter_relay_energy"
    power = "sensor.meter_relay_power"
    assert hass.states.get(energy).state == "unknown"

    # kWh: UN=2 -> byte1 0x40; value 5 -> 5000 Wh
    dongle.inject(erp1_frame(0xD2, [0x07, 0x40, 0, 0, 0, 5], METER_INT))
    await flush(hass)
    assert hass.states.get(energy).state == "5000.0"
    assert hass.states.get(power).state == "unknown"

    # W: UN=3 -> byte1 0x60; value 42 -> 42 W
    dongle.inject(erp1_frame(0xD2, [0x07, 0x60, 0, 0, 0, 42], METER_INT))
    await flush(hass)
    assert hass.states.get(power).state == "42.0"

    # Ws: UN=0 -> byte1 0x00; value 7200 -> 2 Wh
    dongle.inject(erp1_frame(0xD2, [0x07, 0x00, 0, 0, 0x1C, 0x20], METER_INT))
    await flush(hass)
    assert hass.states.get(energy).state == "2.0"

    # Wh: UN=1 -> byte1 0x20; value 17 -> 17 Wh
    dongle.inject(erp1_frame(0xD2, [0x07, 0x20, 0, 0, 0, 17], METER_INT))
    await flush(hass)
    assert hass.states.get(energy).state == "17.0"

    # kW: UN=4 -> byte1 0x80; value 3 -> 3000 W
    dongle.inject(erp1_frame(0xD2, [0x07, 0x80, 0, 0, 0, 3], METER_INT))
    await flush(hass)
    assert hass.states.get(power).state == "3000.0"


async def test_measurement_edge_cases_ignored(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    energy = "sensor.meter_relay_energy"
    # reserved unit 5 (byte1 0xA0): ignored
    dongle.inject(erp1_frame(0xD2, [0x07, 0xA0, 0, 0, 0, 5], METER_INT))
    # other channel (UN=2, channel 1 -> 0x41): not this entity
    dongle.inject(erp1_frame(0xD2, [0x07, 0x41, 0, 0, 0, 9], METER_INT))
    await flush(hass)
    assert hass.states.get(energy).state == "unknown"

    # channel 0x1E means "not channel-specific": accepted
    dongle.inject(erp1_frame(0xD2, [0x07, 0x5E, 0, 0, 0, 3], METER_INT))
    await flush(hass)
    assert hass.states.get(energy).state == "3000.0"


async def test_read_meter_not_acknowledged(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    dongle.respond_to_radio = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.meter_relay_read_meter"},
            blocking=True,
        )
