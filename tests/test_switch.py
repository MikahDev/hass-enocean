"""D2-01-0F switch: exact TX bytes, ACK handling, confirmed vs assumed state."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.enocean_direct.const import CONF_DEVICES

from .conftest import (
    ACTUATOR,
    BASE_ID_HEX,
    FakeDongle,
    d2_status_frame,
    flush,
    make_entry,
    setup_entry,
)

ACTUATOR_INT = int(ACTUATOR["address"], 16)
ENTITY = "switch.pilot_relay"

# Documented fixtures: ESP3 data section of a D2-01 CMD 0x1 Actuator Set Output,
# radio channel 0, sender FF974100 (the validated historical association),
# destination 050A5C20.
#   RORG  CMD  DV<<5|IO  OV    sender........       status
ON_DATA = bytes.fromhex("D2010064FF97410000")
OFF_DATA = bytes.fromhex("D2010000FF97410000")
# optional: sub_tel_num 3, destination, max dBm, no security
EXPECTED_OPTIONAL = bytes.fromhex("03050A5C20FF00")


async def _setup(hass: HomeAssistant) -> None:
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)
    return entry


async def test_turn_on_bytes_and_assumed_state(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    state = hass.states.get(ENTITY)
    assert state.state == "unknown"
    assert state.attributes["assumed_state"] is True

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ENTITY}, blocking=True
    )
    assert len(dongle.sent_radio) == 1
    data, optional = dongle.sent_radio[0]
    assert data == ON_DATA
    assert optional == EXPECTED_OPTIONAL

    state = hass.states.get(ENTITY)
    assert state.state == "on"
    assert state.attributes["assumed_state"] is True  # not yet confirmed
    assert state.attributes["state_confirmed"] is False


async def test_turn_off_bytes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": ENTITY}, blocking=True
    )
    data, optional = dongle.sent_radio[0]
    assert data == OFF_DATA
    assert optional == EXPECTED_OPTIONAL
    assert hass.states.get(ENTITY).state == "off"


async def test_status_confirms_state(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ENTITY}, blocking=True
    )
    # actuator answers with CMD 0x4 status: channel 0, OV 100
    dongle.inject(d2_status_frame(ACTUATOR_INT, channel=0, output_value=100))
    await flush(hass)
    state = hass.states.get(ENTITY)
    assert state.state == "on"
    # HA omits the assumed_state attribute entirely once it is False
    assert "assumed_state" not in state.attributes
    assert state.attributes["state_confirmed"] is True

    # unsolicited status (local button press) flips it without any command
    dongle.inject(d2_status_frame(ACTUATOR_INT, channel=0, output_value=0))
    await flush(hass)
    assert hass.states.get(ENTITY).state == "off"


async def test_status_other_channel_ignored(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    dongle.inject(d2_status_frame(ACTUATOR_INT, channel=1, output_value=100))
    await flush(hass)
    assert hass.states.get(ENTITY).state == "unknown"


async def test_no_ack_raises_and_state_unchanged(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    dongle.respond_to_radio = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": ENTITY}, blocking=True
        )
    assert hass.states.get(ENTITY).state == "unknown"


async def test_rejected_by_transceiver_raises(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    dongle.radio_return_code = 0x02  # RET_NOT_SUPPORTED
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": ENTITY}, blocking=True
        )
    assert hass.states.get(ENTITY).state == "unknown"


async def test_invalid_sender_refused_before_tx(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """A sender outside base..base+127 must never reach the radio."""
    bad = dict(ACTUATOR, sender_id="FF974180")  # base + 128
    entry = make_entry(hass, [bad])
    assert await setup_entry(hass, entry)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": ENTITY}, blocking=True
        )
    assert dongle.sent_radio == []


async def test_channel_attributes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = await _setup(hass)
    state = hass.states.get(ENTITY)
    assert state.attributes["radio_channel"] == 0
    assert state.attributes["channel_number"] == 1
    assert state.attributes["sender_id"] == BASE_ID_HEX
    assert entry.options[CONF_DEVICES][0]["sender_id"] == BASE_ID_HEX
