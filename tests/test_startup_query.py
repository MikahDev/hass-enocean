"""Opt-in startup status query and restored switch state.

The query removes the assumed-state double-button UI right after a restart;
restored state bridges the gap as "assumed" until the actuator confirms.
"""

from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.enocean_direct.const import (
    CONF_BASE_ID,
    CONF_DEVICE_PATH,
    CONF_DEVICES,
    CONF_QUERY_STARTUP,
    DOMAIN,
)

from .conftest import (
    ACTUATOR,
    BASE_ID_HEX,
    COVER,
    PORT,
    FakeDongle,
    d2_status_frame,
    flush,
    make_entry,
    setup_entry,
)

ACTUATOR_INT = int(ACTUATOR["address"], 16)


def _entry_with_query(hass, devices):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EnOcean gateway FF974100",
        data={CONF_DEVICE_PATH: PORT, CONF_BASE_ID: BASE_ID_HEX},
        options={CONF_DEVICES: devices, CONF_QUERY_STARTUP: True},
    )
    entry.add_to_hass(hass)
    return entry


async def test_disabled_by_default_sends_nothing(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [ACTUATOR, COVER])
    assert await setup_entry(hass, entry)
    await flush(hass)
    assert dongle.sent_radio == []


async def test_startup_queries_sent_when_enabled(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = _entry_with_query(hass, [ACTUATOR, COVER])
    assert await setup_entry(hass, entry)
    await flush(hass)
    assert [data for data, _ in dongle.sent_radio] == [
        # D2-01 CMD 0x3 status query, channel 0, from the actuator's sender
        bytes.fromhex("D20300FF97410000"),
        # D2-05 CMD 0x3 position query, channel 0, from the cover's sender
        bytes.fromhex("D203FF97410100"),
    ]

    # the reply confirms the switch: assumed state gone without any command
    dongle.inject(d2_status_frame(ACTUATOR_INT, channel=0, output_value=100))
    await flush(hass)
    state = hass.states.get("switch.pilot_relay")
    assert state.state == "on"
    assert "assumed_state" not in state.attributes
    assert state.attributes["state_confirmed"] is True


async def test_bad_sender_does_not_break_setup(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """A device whose stored sender is invalid must not take the entry down."""
    bad = dict(ACTUATOR, sender_id="FF974180")  # base + 128
    entry = _entry_with_query(hass, [bad, COVER])
    assert await setup_entry(hass, entry)
    await flush(hass)
    # only the valid cover was queried
    assert [data for data, _ in dongle.sent_radio] == [
        bytes.fromhex("D203FF97410100"),
    ]


async def test_switch_restores_state_as_assumed(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    mock_restore_cache(hass, [State("switch.pilot_relay", "on")])
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)
    state = hass.states.get("switch.pilot_relay")
    assert state.state == "on"
    assert state.attributes["assumed_state"] is True  # restored, not confirmed
    assert state.attributes["state_confirmed"] is False


async def test_settings_flow_persists_and_survives_device_edits(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_QUERY_STARTUP: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_QUERY_STARTUP] is True

    # adding a device must not drop the setting
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_manual"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"address": "0084ACF3", "eep": "D5-00-01", "name": "Door"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_QUERY_STARTUP] is True
    assert len(entry.options[CONF_DEVICES]) == 2
