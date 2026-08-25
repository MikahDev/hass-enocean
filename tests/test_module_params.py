"""Module parameters: D2-01 CMD 0x2 Actuator Set Local via the options flow.

One telegram writes every local parameter, so the tests pin the exact bytes
for the full field set, not just the toggles a submit changed.
"""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import ACTUATOR, CONTACT, FakeDongle, make_entry, setup_entry

# optional: sub_tel_num 3, destination 050A5C20, max dBm, no security
EXPECTED_OPTIONAL = bytes.fromhex("03050A5C20FF00")


async def _to_form(hass: HomeAssistant, entry) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "module_params"}
    )
    assert result["step_id"] == "module_params"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"address": ACTUATOR["address"]}
    )
    assert result["step_id"] == "module_params_form"
    return result


async def test_set_local_bytes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)
    result = await _to_form(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "local_control": True,
            "taught_in_enabled": True,
            "overcurrent_restart": False,
            "power_failure_detection": False,
            "default_state": "previous",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "params_sent"
    #   RORG  d/e<<7|CMD  OC|RO|LC|I/O  DT2|DT3  d/n|PF|DS|DT1  sender    status
    #   D2    0x82        LC -> 0x20    0x00     DS=2 -> 0x20   FF974100  00
    data, optional = dongle.sent_radio[0]
    assert data == bytes.fromhex("D282200020FF97410000")
    assert optional == EXPECTED_OPTIONAL


async def test_set_local_bytes_alternate(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)
    result = await _to_form(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "local_control": False,
            "taught_in_enabled": True,
            "overcurrent_restart": True,
            "power_failure_detection": True,
            "default_state": "on",
        },
    )
    assert result["reason"] == "params_sent"
    # OC -> byte1 0x80; PF 0x40 | DS=1 0x10 -> byte3 0x50
    data, optional = dongle.sent_radio[0]
    assert data == bytes.fromhex("D282800050FF97410000")
    assert optional == EXPECTED_OPTIONAL


async def test_not_acknowledged_shows_error(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)
    result = await _to_form(hass, entry)
    dongle.respond_to_radio = False
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "local_control": True,
            "taught_in_enabled": True,
            "overcurrent_restart": False,
            "power_failure_detection": False,
            "default_state": "previous",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_acknowledged"}


async def test_no_actuators_aborts(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "module_params"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_actuators"


async def test_disconnected_between_render_and_submit(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """A USB drop after the form rendered aborts with a clear message
    instead of crashing the flow with an unknown error."""
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)
    result = await _to_form(hass, entry)
    dongle.unplug()
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "local_control": True,
            "taught_in_enabled": True,
            "overcurrent_restart": False,
            "power_failure_detection": False,
            "default_state": "previous",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_connected"
    assert dongle.sent_radio == []
