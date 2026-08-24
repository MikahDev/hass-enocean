"""Options flow: inbox, manual add, actuator validation, import, export, remove."""

import json

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr

from custom_components.enocean_direct.const import CONF_DEVICES, DOMAIN

from .conftest import (
    ACTUATOR,
    BASE_ID_HEX,
    CONTACT,
    FakeDongle,
    d5_frame,
    erp1_frame,
    f6_frame,
    make_entry,
    setup_entry,
    ute_teach_in_frame,
)


async def _menu(hass, entry, choice: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": choice}
    )


async def test_add_contact_manually(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "add_manual")
    assert result["type"] is FlowResultType.FORM

    # invalid address rejected with leading-zero guidance
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"address": "84ACF3", "eep": "D5-00-01", "name": "Door"},
    )
    assert result["errors"] == {"address": "invalid_address"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"address": "00:84:ac:f3", "eep": "D5-00-01", "name": "Door"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_DEVICES] == [
        {"address": "0084ACF3", "eep": "D5-00-01", "name": "Door"}
    ]
    assert hass.states.get("binary_sensor.door") is not None


async def test_add_actuator_sender_validation(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "add_manual")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"address": "050A5C20", "eep": "D2-01-0F", "name": "Relay"},
    )
    assert result["step_id"] == "actuator"
    # the Base ID is proposed as default, never silently substituted
    assert result["description_placeholders"]["base_id"] == BASE_ID_HEX

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sender_id": "FF974180", "channel": 0}
    )
    assert result["errors"] == {"sender_id": "sender_out_of_range"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sender_id": "not hex", "channel": 0}
    )
    assert result["errors"] == {"sender_id": "invalid_sender"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sender_id": BASE_ID_HEX, "channel": 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_DEVICES] == [
        {
            "address": "050A5C20",
            "eep": "D2-01-0F",
            "name": "Relay",
            "sender_id": BASE_ID_HEX,
            "channel": 0,
        }
    ]
    assert hass.states.get("switch.relay") is not None


async def test_duplicate_address_rejected(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "add_manual")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"address": CONTACT["address"], "eep": "D5-00-01", "name": "Again"},
    )
    assert result["errors"] == {"address": "duplicate_address"}


async def test_inbox_lists_and_prefills(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    # D5 teach-in declares the only 1BS profile; F6 press declares nothing.
    dongle.inject(d5_frame(0x0084ACF3, closed=False, teach_in=True))
    dongle.inject(f6_frame(0x002909BE, 0x30))
    await hass.async_block_till_done()

    result = await _menu(hass, entry, "inbox")
    assert result["type"] is FlowResultType.FORM
    options = result["data_schema"].schema["address"].config["options"]
    labels = {opt["value"]: opt["label"] for opt in options}
    assert "D5-00-01" in labels["0084ACF3"]
    assert "profile unknown" in labels["002909BE"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"address": "0084ACF3"}
    )
    assert result["step_id"] == "add_manual"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"address": "0084ACF3", "eep": "D5-00-01", "name": "Door"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    # saving reloaded the entry; the inbox is in-memory by design and resets,
    # so the configured contact can never reappear in it
    result = await _menu(hass, entry, "inbox")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "inbox_empty"


async def test_inbox_shows_ute_declared_eep(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """A UTE teach-in declares its EEP; the inbox must show it. It is never
    acknowledged (no teach-in mode exists)."""
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(ute_teach_in_frame(0x050A5C20, 0xD2, 0x01, 0x0F))
    await hass.async_block_till_done()
    assert dongle.sent_radio == []  # no UTE response transmitted

    result = await _menu(hass, entry, "inbox")
    options = result["data_schema"].schema["address"].config["options"]
    labels = {opt["value"]: opt["label"] for opt in options}
    assert "D2-01-0F" in labels["050A5C20"]


async def test_add_manual_rejects_base_range_address(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "add_manual")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"address": "FF900000", "eep": "D2-01-0F", "name": "Bad"},
    )
    assert result["errors"] == {"address": "not_eurid"}


async def test_inbox_empty_aborts(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "inbox")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "inbox_empty"


async def test_import_rollback_and_dry_run(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    before = entry.options[CONF_DEVICES]

    # invalid: one record lacks sender/channel -> whole file rejected
    result = await _menu(hass, entry, "import_devices")
    bad = json.dumps(
        {
            "version": 1,
            "devices": [
                {"address": "002909BE", "eep": "F6-02-01", "name": "Rocker"},
                {"address": "050A5C20", "eep": "D2-01-0F", "name": "Relay"},
            ],
        }
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"document": bad}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"document": "import_invalid"}
    assert "sender_id is required" in result["description_placeholders"]["detail"]
    assert entry.options[CONF_DEVICES] == before  # rollback: nothing persisted

    # valid: dry-run summary, then persist on confirm
    good = json.dumps(
        {
            "version": 1,
            "devices": [
                {"address": "002909BE", "eep": "F6-02-01", "name": "Rocker"},
                {
                    "address": "050A5C20",
                    "eep": "D2-01-0F",
                    "name": "Relay",
                    "sender_id": BASE_ID_HEX,
                    "channel": 0,
                },
            ],
        }
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"document": good}
    )
    assert result["step_id"] == "import_confirm"
    assert result["description_placeholders"]["count"] == "2"
    assert entry.options[CONF_DEVICES] == before  # still nothing persisted

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    addresses = [raw["address"] for raw in entry.options[CONF_DEVICES]]
    assert addresses == ["0084ACF3", "002909BE", "050A5C20"]
    assert hass.states.get("switch.relay") is not None


async def test_export_contains_devices_no_secrets(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT, ACTUATOR])
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "export_devices")
    document = result["description_placeholders"]["export_json"]
    doc = json.loads(document)
    assert doc["gateway"]["base_id"] == BASE_ID_HEX
    assert len(doc["devices"]) == 2
    # only recovery configuration keys, nothing else
    allowed = {"address", "eep", "name", "sender_id", "channel"}
    for device in doc["devices"]:
        assert set(device) <= allowed
    assert set(doc) == {"version", "gateway", "devices"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_remove_device(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [CONTACT, ACTUATOR])
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "manage")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"address": CONTACT["address"]}
    )
    assert result["step_id"] == "remove_confirm"
    assert result["description_placeholders"]["name"] == CONTACT["name"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert [raw["address"] for raw in entry.options[CONF_DEVICES]] == [
        ACTUATOR["address"]
    ]
    assert hass.states.get("binary_sensor.test_contact") is None
    registry = dr.async_get(hass)
    assert registry.async_get_device(identifiers={(DOMAIN, CONTACT["address"])}) is None
    assert (
        registry.async_get_device(identifiers={(DOMAIN, ACTUATOR["address"])})
        is not None
    )


async def test_unknown_sender_never_guessed(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """An unknown 4BS sensor shows up in the inbox with no invented EEP."""
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(erp1_frame(0xA5, [0x10, 0x20, 0x30, 0x08], 0x01234567))
    await hass.async_block_till_done()
    hub = entry.runtime_data
    item = hub.inbox.get("01234567")
    assert item is not None
    assert item.eep is None  # data telegram carries no EEP: never guessed
    assert item.telegram_type == "4BS"


async def test_add_with_area(hass: HomeAssistant, dongle: FakeDongle) -> None:
    """The chosen room is applied at creation and never re-applied after the
    user moves or clears it in the UI."""
    from homeassistant.helpers import area_registry as ar

    area = ar.async_get(hass).async_create("Kitchen")
    other = ar.async_get(hass).async_create("Garage")
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)

    result = await _menu(hass, entry, "add_manual")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "address": "0084ACF3",
            "eep": "D5-00-01",
            "name": "Door",
            "area_id": area.id,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, "0084ACF3")})
    assert device.area_id == area.id

    # the user moves the device to another room; a reload must not undo it
    registry.async_update_device(device.id, area_id=other.id)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    device = registry.async_get_device(identifiers={(DOMAIN, "0084ACF3")})
    assert device.area_id == other.id


async def test_import_with_unknown_area_skipped(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """An area id from another installation is stored but not applied."""
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "import_devices")
    doc = json.dumps(
        {
            "version": 1,
            "devices": [
                {
                    "address": "0084ACF3",
                    "eep": "D5-00-01",
                    "name": "Door",
                    "area_id": "no_such_area",
                }
            ],
        }
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"document": doc}
    )
    assert result["step_id"] == "import_confirm"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "0084ACF3")})
    assert device is not None
    assert device.area_id is None
