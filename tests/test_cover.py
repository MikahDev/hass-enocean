"""D2-05-00 cover: exact TX bytes, position feedback, polarity inversion.

EnOcean D2-05 position polarity is 0 = open, 100 = closed; HA uses 100 = open.
"""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import (
    COVER,
    FakeDongle,
    d2_05_reply_frame,
    flush,
    make_entry,
    setup_entry,
)

COVER_INT = int(COVER["address"], 16)
ENTITY = "cover.test_blind"

# ESP3 data section of D2-05 commands, channel 0, sender FF974101,
# destination 051B2C30. ANG 0x7F = keep current angle.
#   RORG  POS  ANG  REPO/LOCK  CHN<<4|CMD  sender........  status
OPEN_DATA = bytes.fromhex("D2007F0001FF97410100")  # CMD 1, POS 0 (open)
CLOSE_DATA = bytes.fromhex("D2647F0001FF97410100")  # CMD 1, POS 100 (closed)
POS30_DATA = bytes.fromhex("D2467F0001FF97410100")  # HA 30% open -> POS 70
STOP_DATA = bytes.fromhex("D202FF97410100")  # CMD 2, 1-byte payload
# optional: sub_tel_num 3, destination, max dBm, no security
EXPECTED_OPTIONAL = bytes.fromhex("03051B2C30FF00")


async def _setup(hass: HomeAssistant):
    entry = make_entry(hass, [COVER])
    assert await setup_entry(hass, entry)
    return entry


async def test_unknown_until_first_reply(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    state = hass.states.get(ENTITY)
    assert state.state == "unknown"
    assert state.attributes["sender_id"] == "FF974101"
    assert state.attributes["radio_channel"] == 0


async def test_open_close_stop_bytes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": ENTITY}, blocking=True
    )
    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": ENTITY}, blocking=True
    )
    await hass.services.async_call(
        "cover", "stop_cover", {"entity_id": ENTITY}, blocking=True
    )
    assert [data for data, _ in dongle.sent_radio] == [
        OPEN_DATA,
        CLOSE_DATA,
        STOP_DATA,
    ]
    assert all(optional == EXPECTED_OPTIONAL for _, optional in dongle.sent_radio)


async def test_set_position_inverts_polarity(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": ENTITY, "position": 30},
        blocking=True,
    )
    data, optional = dongle.sent_radio[0]
    assert data == POS30_DATA
    assert optional == EXPECTED_OPTIONAL


async def test_position_feedback(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=100))  # EnOcean closed
    await flush(hass)
    state = hass.states.get(ENTITY)
    assert state.state == "closed"
    assert state.attributes["current_position"] == 0

    dongle.inject(d2_05_reply_frame(COVER_INT, position=0))  # EnOcean open
    await flush(hass)
    state = hass.states.get(ENTITY)
    assert state.state == "open"
    assert state.attributes["current_position"] == 100


async def test_movement_states(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=30))
    await flush(hass)
    assert hass.states.get(ENTITY).state == "open"  # partial, direction unknown

    dongle.inject(d2_05_reply_frame(COVER_INT, position=60))
    await flush(hass)
    assert hass.states.get(ENTITY).state == "closing"

    # same position again: movement has stopped (also cancels the watchdog)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=60))
    await flush(hass)
    state = hass.states.get(ENTITY)
    assert state.state == "open"
    assert state.attributes["current_position"] == 40


async def test_unknown_position_reply_ignored(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=127))  # 127 = unknown
    await flush(hass)
    assert hass.states.get(ENTITY).state == "unknown"


async def test_no_ack_raises(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    dongle.respond_to_radio = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": ENTITY}, blocking=True
        )
    assert hass.states.get(ENTITY).state == "unknown"
