"""Edit devices in place (name, room, sender, channel) and cover inversion.

Inversion swaps open/close on the D2-05 TX path and mirrors the position
conversion on RX. It is a cover-only field: the D5-00-01 contact polarity is
spec-pinned and validate_record must reject an invert flag on it.
"""

import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr

from custom_components.enocean_direct.const import (
    CONF_DEVICES,
    CONF_QUERY_STARTUP,
    DOMAIN,
    EEP_COVER,
    SUPPORTED_EEPS,
)
from custom_components.enocean_direct.models import (
    DeviceRecord,
    build_export,
    parse_import,
    record_from_dict,
    validate_record,
)

from .conftest import (
    ACTUATOR,
    BASE_ID_HEX,
    CONTACT,
    COVER,
    PORT,
    FakeDongle,
    d2_05_reply_frame,
    flush,
    make_entry,
    setup_entry,
)

COVER_INT = int(COVER["address"], 16)
INVERTED_COVER = {**COVER, "invert": True}

# D2-05 CMD 1 frames from sender FF974101 to 051B2C30 (see test_cover.py).
OPEN_DATA = bytes.fromhex("D2007F0001FF97410100")  # POS 0 = EnOcean open
CLOSE_DATA = bytes.fromhex("D2647F0001FF97410100")  # POS 100 = EnOcean closed
POS30_INVERTED_DATA = bytes.fromhex("D21E7F0001FF97410100")  # HA 30% -> POS 30


async def _manage(hass, entry, address: str):
    """Open Configure > Manage, pick a device, return the per-device menu."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "manage"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"address": address}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "manage_device"
    return result


async def _edit(hass, entry, address: str):
    result = await _manage(hass, entry, address)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "edit_device"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edit_device"
    return result


# ---------------------------------------------------------------- models
def test_record_invert_roundtrip() -> None:
    plain = DeviceRecord("051B2C30", "D2-05-00", "Blind", "FF974101", 0)
    assert "invert" not in plain.as_dict()  # only written when set
    inverted = DeviceRecord("051B2C30", "D2-05-00", "Blind", "FF974101", 0, invert=True)
    assert inverted.as_dict()["invert"] is True
    assert record_from_dict(inverted.as_dict()) == inverted
    assert record_from_dict(plain.as_dict()).invert is False


def test_validate_invert_cover_only() -> None:
    record, errors = validate_record(INVERTED_COVER, BASE_ID_HEX, set())
    assert errors == []
    assert record.invert is True

    # the D5 contact polarity is spec-pinned: never invertible
    record, errors = validate_record({**CONTACT, "invert": True}, BASE_ID_HEX, set())
    assert record is None
    assert any("invert does not apply" in error for error in errors)

    # nor any other profile: switches, the fan, rockers, every sensor
    for eep in SUPPORTED_EEPS:
        if eep == EEP_COVER:
            continue
        raw = {"address": "0084ACF3", "eep": eep, "name": "x", "invert": True}
        if eep in ("D2-20-02",) or eep.startswith("D2-01"):
            raw |= {"sender_id": BASE_ID_HEX, "channel": 0}
        record, errors = validate_record(raw, BASE_ID_HEX, set())
        assert record is None, eep
        assert any("invert does not apply" in error for error in errors), eep

    # must be a real boolean
    record, errors = validate_record({**COVER, "invert": "yes"}, BASE_ID_HEX, set())
    assert record is None
    assert any("invert must be" in error for error in errors)


def test_export_import_carry_invert() -> None:
    records = [
        record_from_dict(INVERTED_COVER),
        record_from_dict(COVER | {"address": "051B2C31", "sender_id": "FF974102"}),
    ]
    exported = build_export(records, BASE_ID_HEX)
    doc = json.loads(exported)
    assert doc["devices"][0]["invert"] is True
    assert "invert" not in doc["devices"][1]
    result = parse_import(exported, BASE_ID_HEX, set())
    assert result.ok
    assert result.records == records


# ---------------------------------------------------------------- edit flow
async def test_edit_name_and_room(hass: HomeAssistant, dongle: FakeDongle) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.enocean_direct.const import CONF_BASE_ID, CONF_DEVICE_PATH

    kitchen = ar.async_get(hass).async_create("Kitchen")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE_PATH: PORT, CONF_BASE_ID: BASE_ID_HEX},
        options={CONF_DEVICES: [CONTACT, ACTUATOR], CONF_QUERY_STARTUP: True},
    )
    entry.add_to_hass(hass)
    assert await setup_entry(hass, entry)
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])})
    assert device.name == CONTACT["name"]
    entity_id = "binary_sensor.test_contact"
    assert hass.states.get(entity_id) is not None

    result = await _edit(hass, entry, CONTACT["address"])
    assert result["description_placeholders"]["address"] == CONTACT["address"]
    # a contact form has no sender/channel/invert fields
    keys = {str(key) for key in result["data_schema"].schema}
    assert keys == {"name", "area_id"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Front door", "area_id": kitchen.id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    stored = entry.options[CONF_DEVICES]
    assert stored[0] == {**CONTACT, "name": "Front door", "area_id": kitchen.id}
    assert stored[1] == ACTUATOR  # untouched, order preserved
    assert entry.options[CONF_QUERY_STARTUP] is True  # other options preserved
    device = registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])})
    assert device.name == "Front door"
    assert device.area_id == kitchen.id
    # the entity registry entry is intact: same entity id, new friendly name
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["friendly_name"] == "Front door"


async def test_edit_rename_overrides_user_rename(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """A device renamed in the HA UI (name_by_user) shows the name entered in
    the edit form afterwards, not the stale UI override."""
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])})
    registry.async_update_device(device.id, name_by_user="Custom")

    result = await _edit(hass, entry, CONTACT["address"])
    # prefilled with the name the user currently sees
    name_key = next(k for k in result["data_schema"].schema if str(k) == "name")
    assert name_key.default() == "Custom"
    assert result["description_placeholders"]["name"] == "Custom"
    # submitting the visible name unchanged leaves the UI override alone
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Custom"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    device = registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])})
    assert device.name_by_user == "Custom"
    assert entry.options[CONF_DEVICES][0]["name"] == "Custom"

    result = await _edit(hass, entry, CONTACT["address"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Garage door"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    device = registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])})
    assert device.name == "Garage door"
    assert device.name_by_user is None
    assert device.area_id is None  # room left empty stays empty

    # a blank name falls back to the address, never an empty device name
    result = await _edit(hass, entry, CONTACT["address"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "   "}
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_DEVICES][0]["name"] == CONTACT["address"]
    device = registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])})
    assert device.name == CONTACT["address"]


async def test_edit_room_baseline_and_clearing(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """The form starts from the room the device is really in (a UI move wins
    over the stored record), and leaving the room empty clears it."""
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    garage = areas.async_create("Garage")
    entry = make_entry(hass, [{**CONTACT, "area_id": kitchen.id}])
    assert await setup_entry(hass, entry)
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])})
    assert device.area_id == kitchen.id
    registry.async_update_device(device.id, area_id=garage.id)  # moved in the UI

    result = await _edit(hass, entry, CONTACT["address"])
    area_key = next(k for k in result["data_schema"].schema if str(k) == "area_id")
    assert area_key.description == {"suggested_value": garage.id}
    # re-selecting the stored room must still move the device (baseline is
    # the registry, not the record)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": CONTACT["name"], "area_id": kitchen.id}
    )
    await hass.async_block_till_done()
    device = registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])})
    assert device.area_id == kitchen.id

    # clearing the room
    result = await _edit(hass, entry, CONTACT["address"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": CONTACT["name"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    device = registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])})
    assert device.area_id is None
    assert "area_id" not in entry.options[CONF_DEVICES][0]
    # and a reload must not put the old room back
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    device = registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])})
    assert device.area_id is None


async def test_edit_actuator_sender(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)
    result = await _edit(hass, entry, ACTUATOR["address"])
    keys = {str(key) for key in result["data_schema"].schema}
    assert keys == {"name", "area_id", "sender_id", "channel"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Renamed relay", "sender_id": "FF974180", "channel": 0},
    )
    assert result["errors"] == {"sender_id": "sender_out_of_range"}
    # the re-rendered form keeps what was typed, not the stored values
    suggested = {
        str(k): k.description.get("suggested_value")
        for k in result["data_schema"].schema
        if k.description
    }
    assert suggested["name"] == "Renamed relay"
    assert suggested["sender_id"] == "FF974180"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Pilot relay", "sender_id": "nope", "channel": 0},
    )
    assert result["errors"] == {"sender_id": "invalid_sender"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Pilot relay", "sender_id": "ff:97:41:05", "channel": 0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_DEVICES] == [{**ACTUATOR, "sender_id": "FF974105"}]

    # the switch now transmits from the new sender; unique_id unchanged
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.pilot_relay"}, blocking=True
    )
    data, _ = dongle.sent_radio[-1]
    assert data == bytes.fromhex("D2010064FF97410500")


async def test_edit_fan_has_no_invert(hass: HomeAssistant, dongle: FakeDongle) -> None:
    fan = {
        "address": "05AA0001",
        "eep": "D2-20-02",
        "name": "Test fan",
        "sender_id": "FF974102",
        "channel": 0,
    }
    entry = make_entry(hass, [fan])
    assert await setup_entry(hass, entry)
    result = await _edit(hass, entry, fan["address"])
    keys = {str(key) for key in result["data_schema"].schema}
    assert keys == {"name", "area_id", "sender_id", "channel"}


async def test_edit_cover_invert_and_tx(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [COVER])
    assert await setup_entry(hass, entry)
    assert hass.states.get("cover.test_blind").attributes["inverted"] is False

    result = await _edit(hass, entry, COVER["address"])
    keys = {str(key) for key in result["data_schema"].schema}
    assert keys == {"name", "area_id", "sender_id", "channel", "invert"}
    defaults = {
        str(k): k.default() for k in result["data_schema"].schema if str(k) != "area_id"
    }
    assert defaults == {
        "name": COVER["name"],
        "sender_id": COVER["sender_id"],
        "channel": 0,
        "invert": False,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": COVER["name"],
            "sender_id": COVER["sender_id"],
            "channel": 0,
            "invert": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_DEVICES] == [INVERTED_COVER]
    assert hass.states.get("cover.test_blind").attributes["inverted"] is True

    # TX: open sends the EnOcean close frame and vice versa; position mirrored
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": "cover.test_blind"}, blocking=True
    )
    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": "cover.test_blind"}, blocking=True
    )
    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": "cover.test_blind", "position": 30},
        blocking=True,
    )
    assert [data for data, _ in dongle.sent_radio] == [
        CLOSE_DATA,
        OPEN_DATA,
        POS30_INVERTED_DATA,
    ]

    # switching inversion off again drops the key from the stored record;
    # the form starts from the stored (inverted) value
    result = await _edit(hass, entry, COVER["address"])
    invert_key = next(k for k in result["data_schema"].schema if str(k) == "invert")
    assert invert_key.default() is True
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": COVER["name"],
            "sender_id": COVER["sender_id"],
            "channel": 0,
            "invert": False,
        },
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_DEVICES] == [COVER]


async def test_inverted_cover_rx(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [INVERTED_COVER])
    assert await setup_entry(hass, entry)
    entity = "cover.test_blind"

    dongle.inject(d2_05_reply_frame(COVER_INT, position=127))  # unknown: ignored
    await flush(hass)
    assert hass.states.get(entity).state == "unknown"

    dongle.inject(d2_05_reply_frame(COVER_INT, position=100))  # EnOcean closed
    await flush(hass)
    state = hass.states.get(entity)
    assert state.state == "open"  # inverted: reported closed means open
    assert state.attributes["current_position"] == 100

    dongle.inject(d2_05_reply_frame(COVER_INT, position=0))
    await flush(hass)
    state = hass.states.get(entity)
    assert state.state == "closed"
    assert state.attributes["current_position"] == 0

    # movement direction mirrored: raw 30 -> 60 is "closing" for the library
    dongle.inject(d2_05_reply_frame(COVER_INT, position=30))
    await flush(hass)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=60))
    await flush(hass)
    state = hass.states.get(entity)
    assert state.state == "opening"
    assert state.attributes["current_position"] == 60

    # same position again: stopped (also cancels the library's watchdog)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=60))
    await flush(hass)
    assert hass.states.get(entity).state == "open"


async def test_manage_menu_remove_still_works(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT, ACTUATOR])
    assert await setup_entry(hass, entry)
    result = await _manage(hass, entry, CONTACT["address"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_confirm"}
    )
    assert result["step_id"] == "remove_confirm"
    assert result["description_placeholders"]["name"] == CONTACT["name"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [raw["address"] for raw in entry.options[CONF_DEVICES]] == [
        ACTUATOR["address"]
    ]


async def test_import_preview_shows_inversion_and_sender(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "import_devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"document": json.dumps({"version": 1, "devices": [INVERTED_COVER]})},
    )
    assert result["step_id"] == "import_confirm"
    summary = result["description_placeholders"]["summary"]
    assert "sender FF974101 | channel 0" in summary
    assert "| inverted" in summary


@pytest.mark.parametrize(
    ("record", "expected_pos"), [(COVER, 40), (INVERTED_COVER, 60)]
)
async def test_unknown_position_after_known_keeps_state(
    hass: HomeAssistant, dongle: FakeDongle, record: dict, expected_pos: int
) -> None:
    """A 101..127 reply after a known position must not surface the direction
    the library derives from the bogus value, inverted or not."""
    entry = make_entry(hass, [record])
    assert await setup_entry(hass, entry)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=60))
    await flush(hass)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=60))  # stopped
    await flush(hass)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=127))
    await flush(hass)
    state = hass.states.get("cover.test_blind")
    assert state.state == "open"
    assert state.attributes["current_position"] == expected_pos
    # The library judges the next real reply against 127 and derives a
    # direction we cannot fix here (upstream state); repeating the position
    # settles it and cancels the watchdog.
    dongle.inject(d2_05_reply_frame(COVER_INT, position=60))
    await flush(hass)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=60))
    await flush(hass)
    assert hass.states.get("cover.test_blind").state == "open"


@pytest.mark.parametrize("bad", [{**CONTACT, "invert": True}, {**COVER, "invert": 1}])
async def test_import_rejects_invert_where_it_does_not_apply(
    hass: HomeAssistant, dongle: FakeDongle, bad: dict
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "import_devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"document": json.dumps({"version": 1, "devices": [bad]})},
    )
    assert result["errors"] == {"document": "import_invalid"}
    assert "invert" in result["description_placeholders"]["detail"]
    assert entry.options[CONF_DEVICES] == []
