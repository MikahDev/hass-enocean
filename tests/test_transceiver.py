"""Transceiver management: repeater mode (CO_WR_REPEATER) and Base ID recovery
(CO_WR_IDBASE). Both are ESP3 common commands written to the local module;
none of these tests may see a radio telegram leave the dongle.
"""

import asyncio
import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_direct.const import (
    CONF_BASE_ID,
    CONF_DEVICE_PATH,
    CONF_DEVICES,
    CONF_QUERY_STARTUP,
    CONF_REPEATER,
    DOMAIN,
)

from .conftest import (
    ACTUATOR,
    BASE_ID_HEX,
    CONTACT,
    PORT,
    FakeDongle,
    make_entry,
    setup_entry,
)

NEW_BASE_HEX = "FF800100"
NEW_BASE = 0xFF800100


def _entry(hass, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"EnOcean gateway {BASE_ID_HEX}",
        data={CONF_DEVICE_PATH: PORT, CONF_BASE_ID: BASE_ID_HEX},
        options={CONF_DEVICES: [CONTACT], **options},
    )
    entry.add_to_hass(hass)
    return entry


async def _menu(hass, entry, choice: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": choice}
    )


# ---------------------------------------------------------------- repeater
async def test_repeater_untouched_by_default(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    assert dongle.repeater_writes == []
    assert dongle.sent_radio == []


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("off", (0x00, 0x00)), ("level_1", (0x01, 0x01)), ("level_2", (0x01, 0x02))],
)
async def test_repeater_applied_on_load(
    hass: HomeAssistant, dongle: FakeDongle, mode: str, expected: tuple[int, int]
) -> None:
    entry = _entry(hass, {CONF_REPEATER: mode})
    assert await setup_entry(hass, entry)
    # exactly one write per load, even for "off" (an explicit off must undo a
    # level set by the previous configuration on a still-powered stick)
    assert dongle.repeater_writes == [expected]
    assert dongle.sent_radio == []  # local module write, nothing on the air


async def test_repeater_settings_flow_reapplies(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "settings")
    assert result["step_id"] == "settings"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_QUERY_STARTUP: False, CONF_REPEATER: "level_2"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_REPEATER] == "level_2"
    # the save reloaded the entry and the new hub wrote the mode once
    assert dongle.repeater_writes == [(0x01, 0x02)]
    assert dongle.sent_radio == []

    # every later reload writes it again (the module setting is volatile)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert dongle.repeater_writes == [(0x01, 0x02), (0x01, 0x02)]


async def test_repeater_reapplied_after_replug(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = _entry(hass, {CONF_REPEATER: "level_1"})
    assert await setup_entry(hass, entry)
    assert dongle.repeater_writes == [(0x01, 0x01)]
    dongle.unplug()
    await hass.async_block_till_done()
    await asyncio.sleep(2.2)  # library reconnect backoff
    await hass.async_block_till_done()
    assert dongle.connect_count == 2
    assert dongle.repeater_writes == [(0x01, 0x01), (0x01, 0x01)]


async def test_repeater_rejection_is_not_fatal(
    hass: HomeAssistant, dongle: FakeDongle, caplog: pytest.LogCaptureFixture
) -> None:
    """A module without repeater support (RET_NOT_SUPPORTED) must not take the
    entry down; the rejection is logged."""
    dongle.common_return_codes[0x09] = 0x02
    entry = _entry(hass, {CONF_REPEATER: "level_1"})
    with caplog.at_level(logging.WARNING):
        assert await setup_entry(hass, entry)
    assert "NOT_SUPPORTED" in caplog.text
    assert hass.states.get("binary_sensor.test_contact") is not None


# ---------------------------------------------------------------- base ID
async def test_base_id_recovery_flow(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)

    result = await _menu(hass, entry, "base_id")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "base_id"
    assert result["description_placeholders"] == {
        "base_id": BASE_ID_HEX,
        "remaining": "10",
    }

    flow_id = result["flow_id"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"new_base_id": "FF80"}
    )
    assert result["errors"] == {"new_base_id": "invalid_base_id"}
    result = await hass.config_entries.options.async_configure(
        flow_id,
        {"new_base_id": "FF7FFFFF"},  # below the base range
    )
    assert result["errors"] == {"new_base_id": "base_id_out_of_range"}
    result = await hass.config_entries.options.async_configure(
        flow_id,
        {"new_base_id": "FFFFFF81"},  # above FFFFFF80
    )
    assert result["errors"] == {"new_base_id": "base_id_out_of_range"}
    result = await hass.config_entries.options.async_configure(
        flow_id, {"new_base_id": BASE_ID_HEX}
    )
    assert result["errors"] == {"new_base_id": "base_id_unchanged"}
    assert dongle.base_id_writes == []  # validation never touches the module

    result = await hass.config_entries.options.async_configure(
        flow_id, {"new_base_id": "ff:80:01:00"}
    )
    assert result["step_id"] == "base_id_confirm"
    assert result["description_placeholders"] == {
        "base_id": BASE_ID_HEX,
        "new_base_id": NEW_BASE_HEX,
        "remaining": "10",
    }

    # a typo in the confirmation writes nothing
    result = await hass.config_entries.options.async_configure(
        flow_id, {"confirm_base_id": "FF800101"}
    )
    assert result["errors"] == {"confirm_base_id": "confirm_mismatch"}
    assert dongle.base_id_writes == []

    result = await hass.config_entries.options.async_configure(
        flow_id, {"confirm_base_id": NEW_BASE_HEX}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "base_id_changed"
    assert result["description_placeholders"] == {"base_id": NEW_BASE_HEX}
    assert dongle.base_id_writes == [NEW_BASE]  # exactly one write cycle spent
    assert dongle.base_id_writes_remaining == 9
    assert dongle.sent_radio == []

    await hass.async_block_till_done()
    assert entry.data[CONF_BASE_ID] == NEW_BASE_HEX
    assert entry.title == f"EnOcean gateway {NEW_BASE_HEX}"
    # the entry reloaded and the new hub runs on the module's new Base ID
    assert dongle.connect_count == 2
    assert entry.runtime_data.base_id == NEW_BASE_HEX
    assert entry.runtime_data.base_id_remaining_write_cycles == 9

    # the export now carries the new Base ID for the next recovery
    result = await _menu(hass, entry, "export_devices")
    assert (
        f'"base_id": "{NEW_BASE_HEX}"'
        in result["description_placeholders"]["export_json"]
    )


@pytest.mark.parametrize(
    ("return_code", "reason"),
    [
        (0x02, "base_id_not_supported"),
        (0x90, "base_id_out_of_range"),
        (0x91, "base_id_max_reached"),
        (0x01, "base_id_failed"),
    ],
)
async def test_base_id_module_errors(
    hass: HomeAssistant, dongle: FakeDongle, return_code: int, reason: str
) -> None:
    dongle.common_return_codes[0x07] = return_code
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "base_id")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"new_base_id": NEW_BASE_HEX}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm_base_id": NEW_BASE_HEX}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}
    assert entry.data[CONF_BASE_ID] == BASE_ID_HEX  # nothing persisted
    assert dongle.connect_count == 1  # no reload


async def test_base_id_silently_not_written(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """Module answers OK but keeps the old Base ID: the library's read-back
    catches it and the flow reports it without touching the entry."""
    dongle.ignore_base_id_write = True
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "base_id")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"new_base_id": NEW_BASE_HEX}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm_base_id": NEW_BASE_HEX}
    )
    assert result["errors"] == {"base": "base_id_not_written"}
    assert dongle.base_id_writes == [NEW_BASE]
    assert entry.data[CONF_BASE_ID] == BASE_ID_HEX


async def test_base_id_requires_connection(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    dongle.unplug()
    await hass.async_block_till_done()
    result = await _menu(hass, entry, "base_id")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_connected"
    assert dongle.base_id_writes == []


async def test_base_id_module_goes_mute_mid_write(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """The module stops answering during the write: the library cannot read
    the Base ID back, so the flow reports it instead of assuming success."""
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "base_id")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"new_base_id": NEW_BASE_HEX}
    )
    dongle.respond_to_common = False
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm_base_id": NEW_BASE_HEX}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_connected"}
    assert entry.data[CONF_BASE_ID] == BASE_ID_HEX
    assert entry.runtime_data.base_id == BASE_ID_HEX  # never optimistically set


async def test_base_id_mismatch_after_write(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """The module acknowledges but ends up on a third Base ID: reported with
    its own message, and nothing is persisted on our side."""
    dongle.base_id_write_result = 0xFF800200
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    result = await _menu(hass, entry, "base_id")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"new_base_id": NEW_BASE_HEX}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm_base_id": NEW_BASE_HEX}
    )
    assert result["errors"] == {"base": "base_id_mismatch_after_write"}
    assert entry.data[CONF_BASE_ID] == BASE_ID_HEX
    assert entry.runtime_data.base_id == BASE_ID_HEX


async def test_unknown_repeater_mode_does_not_break_setup(
    hass: HomeAssistant, dongle: FakeDongle, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored mode this version does not know (hand-edited entry, renamed
    option) is logged and skipped; the gateway still loads."""
    entry = _entry(hass, {CONF_REPEATER: "level_9"})
    with caplog.at_level(logging.WARNING):
        assert await setup_entry(hass, entry)
    assert "Unknown repeater mode" in caplog.text
    assert dongle.repeater_writes == []
    assert hass.states.get("binary_sensor.test_contact") is not None
