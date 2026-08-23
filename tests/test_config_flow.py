"""Config flow tests: port validation, base ID read, single instance."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.enocean_direct.const import (
    CONF_BASE_ID,
    CONF_DEVICE_PATH,
    DOMAIN,
)

from .conftest import BASE_ID_HEX, PORT, FakeDongle, make_entry, setup_entry


async def test_user_flow_success(hass: HomeAssistant, dongle: FakeDongle) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_PATH: PORT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DEVICE_PATH: PORT, CONF_BASE_ID: BASE_ID_HEX}
    assert BASE_ID_HEX in result["title"]
    # the validation connection was closed again (the entry then opened its own)
    assert dongle.transports[0].closed


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    dongle.fail_connect = True
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_PATH: "/dev/missing"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    # module present but mute (e.g. wrong device): also cannot_connect
    dongle.fail_connect = False
    dongle.respond_to_common = False
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_PATH: PORT}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert dongle.transport.closed  # descriptor released after failed validation


async def test_reconfigure_updates_path(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    new_path = "/dev/serial/by-id/usb-FTDI_NEW-if00-port0"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_PATH: new_path}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DEVICE_PATH] == new_path


async def test_single_instance(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
