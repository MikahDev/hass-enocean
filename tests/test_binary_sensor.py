"""D5-00-01 contact: semantics, teach-in, unknown senders, attributes."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import CONTACT, FakeDongle, d5_frame, make_entry, setup_entry

CONTACT_INT = int(CONTACT["address"], 16)
ENTITY = "binary_sensor.test_contact"


async def test_open_closed_semantics(hass: HomeAssistant, dongle: FakeDongle) -> None:
    """CO bit 0 = open, 1 = closed; HA opening sensor: on = open."""
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    state = hass.states.get(ENTITY)
    assert state.state == "unknown"
    assert state.attributes["device_class"] == "opening"

    dongle.inject(d5_frame(CONTACT_INT, closed=False, rssi=63))
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY)
    assert state.state == "on"  # open
    assert state.attributes["rssi_dbm"] == -63
    assert state.attributes["last_received"] is not None

    dongle.inject(d5_frame(CONTACT_INT, closed=True))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "off"  # closed


async def test_teach_in_does_not_change_state(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    dongle.inject(d5_frame(CONTACT_INT, closed=True))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "off"

    dongle.inject(d5_frame(CONTACT_INT, closed=False, teach_in=True))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "off"  # unchanged


async def test_unknown_sender_ignored(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    before = len(hass.states.async_entity_ids())
    dongle.inject(d5_frame(0x00112233, closed=False))
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids()) == before
    assert hass.states.get(ENTITY).state == "unknown"
    # but the inbox saw it
    hub = entry.runtime_data
    assert hub.inbox.get("00112233") is not None


async def test_unique_id_leading_zero(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    registry = er.async_get(hass)
    entity = registry.async_get(ENTITY)
    assert entity.unique_id == "0084ACF3"


async def test_duplicate_repeated_telegram(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """A repeater copy (repeater count 1) of a just-seen telegram is dropped."""
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    hub = entry.runtime_data

    dongle.inject(d5_frame(CONTACT_INT, closed=True, status=0x00))
    await hass.async_block_till_done()
    count_after_original = hub.inbox.get(CONTACT["address"]).count

    dongle.inject(d5_frame(CONTACT_INT, closed=True, status=0x01))  # repeated copy
    await hass.async_block_till_done()
    assert hub.inbox.get(CONTACT["address"]).count == count_after_original
