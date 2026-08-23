"""Diagnostics must not leak radio IDs or hardware identifiers."""

from homeassistant.core import HomeAssistant

from custom_components.enocean_direct.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import ACTUATOR, CONTACT, FakeDongle, d5_frame, make_entry, setup_entry


async def test_diagnostics_redacted(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [CONTACT, ACTUATOR])
    assert await setup_entry(hass, entry)
    dongle.inject(d5_frame(int(CONTACT["address"], 16), closed=False))
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diagnostics)
    assert CONTACT["address"] not in text
    assert ACTUATOR["address"] not in text
    assert ACTUATOR["sender_id"] not in text
    assert "FTDI_TEST" not in text  # serial path carries the hardware serial
    assert "Pilot relay" not in text  # names are redacted (may default to address)
    assert diagnostics["connected"] is True
    assert len(diagnostics["devices"]) == 2
    assert len(diagnostics["inbox"]) == 1
