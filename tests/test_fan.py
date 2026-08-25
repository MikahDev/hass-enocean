"""D2-20-02 fan: exact TX bytes, status feedback, auto preset."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import FakeDongle, erp1_frame, flush, make_entry, setup_entry

FAN = {
    "address": "050B7E10",
    "eep": "D2-20-02",
    "name": "Test fan",
    "sender_id": "FF974103",
    "channel": 0,
}
FAN_INT = int(FAN["address"], 16)
ENTITY = "fan.test_fan"
# CMD 0 fan control: byte0=CMD, byte1=RSR 'no change'(3)<<4 | RS 'no change'(15),
# byte2 reserved, byte3=FS.
SPEED_40 = bytes.fromhex("D2003F0028FF97410300")
SPEED_OFF = bytes.fromhex("D2003F0000FF97410300")
SPEED_AUTO = bytes.fromhex("D2003F00FDFF97410300")
SPEED_DEFAULT = bytes.fromhex("D2003F00FEFF97410300")
EXPECTED_OPTIONAL = bytes.fromhex("03050B7E10FF00")


def _status_frame(percentage: int) -> bytes:
    # CMD 1 fan status: byte1 = HCS disabled | RS 'no change'; byte3 = FS
    return erp1_frame(0xD2, [0x01, 0x0F, 0x00, percentage], FAN_INT)


async def _setup(hass: HomeAssistant):
    entry = make_entry(hass, [FAN])
    assert await setup_entry(hass, entry)
    return entry


async def test_speed_and_onoff_bytes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    assert hass.states.get(ENTITY).state == "unknown"

    await hass.services.async_call(
        "fan", "set_percentage", {"entity_id": ENTITY, "percentage": 40}, blocking=True
    )
    await hass.services.async_call(
        "fan", "turn_off", {"entity_id": ENTITY}, blocking=True
    )
    await hass.services.async_call(
        "fan", "turn_on", {"entity_id": ENTITY}, blocking=True
    )
    assert [data for data, _ in dongle.sent_radio] == [
        SPEED_40,
        SPEED_OFF,
        SPEED_DEFAULT,
    ]
    assert all(optional == EXPECTED_OPTIONAL for _, optional in dongle.sent_radio)


async def test_status_feedback(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    dongle.inject(_status_frame(40))
    await flush(hass)
    state = hass.states.get(ENTITY)
    assert state.state == "on"
    assert state.attributes["percentage"] == 40

    dongle.inject(_status_frame(0))
    await flush(hass)
    assert hass.states.get(ENTITY).state == "off"


async def test_auto_preset(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "fan",
        "set_preset_mode",
        {"entity_id": ENTITY, "preset_mode": "auto"},
        blocking=True,
    )
    assert dongle.sent_radio[0][0] == SPEED_AUTO
    state = hass.states.get(ENTITY)
    assert state.attributes["preset_mode"] == "auto"

    # a numeric status clears the optimistic preset
    dongle.inject(_status_frame(25))
    await flush(hass)
    state = hass.states.get(ENTITY)
    assert state.attributes["preset_mode"] is None
    assert state.attributes["percentage"] == 25


async def test_no_ack_raises(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    dongle.respond_to_radio = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "fan", "turn_on", {"entity_id": ENTITY}, blocking=True
        )
    assert hass.states.get(ENTITY).state == "unknown"
