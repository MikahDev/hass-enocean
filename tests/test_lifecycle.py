"""Serial ownership lifecycle: open, unload, reload, failure, disconnect."""

import asyncio

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.enocean_direct.const import (
    DOMAIN,
    ISSUE_SERIAL_DISCONNECTED,
)

from .conftest import CONTACT, FakeDongle, d5_frame, make_entry, setup_entry

CONTACT_INT = int(CONTACT["address"], 16)
ENTITY = "binary_sensor.test_contact"


async def test_setup_and_unload(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert dongle.connect_count == 1
    assert not dongle.transport.closed

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert dongle.transport.closed


async def test_setup_fails_when_module_mute(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """Port opens but base ID query times out: retry state, descriptor freed."""
    dongle.respond_to_common = False
    entry = make_entry(hass, [CONTACT])
    assert not await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert dongle.transport.closed


async def test_repeated_reload_single_reader(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """Reload three times; each cycle closes the previous reader and no
    duplicate dispatching survives (state updates exactly once per telegram)."""
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)

    transports = [dongle.transport]
    for _ in range(3):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert transports[-1].closed  # previous reader gone
        transports.append(dongle.transport)
    assert dongle.connect_count == 4
    assert not dongle.transport.closed

    dongle.inject(d5_frame(CONTACT_INT, closed=False))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "on"

    dongle.inject(d5_frame(CONTACT_INT, closed=True))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "off"


async def test_usb_disconnect_and_recover(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    dongle.inject(d5_frame(CONTACT_INT, closed=True))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "off"

    dongle.unplug()
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "unavailable"
    issue_registry = ir.async_get(hass)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_SERIAL_DISCONNECTED)

    # library reconnects with 2s backoff against the same fake port
    await asyncio.sleep(2.2)
    await hass.async_block_till_done()
    assert dongle.connect_count == 2
    assert hass.states.get(ENTITY).state != "unavailable"
    assert not issue_registry.async_get_issue(DOMAIN, ISSUE_SERIAL_DISCONNECTED)

    # telegrams flow again through the new reader
    dongle.inject(d5_frame(CONTACT_INT, closed=False))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "on"


async def test_ha_stop_releases_port(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert dongle.transport.closed


async def test_inbox_persists_across_reload(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(d5_frame(0x0084AAAA, closed=True))  # unconfigured sender
    await hass.async_block_till_done()
    assert entry.runtime_data.inbox.get("0084AAAA") is not None

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.inbox.get("0084AAAA") is not None
