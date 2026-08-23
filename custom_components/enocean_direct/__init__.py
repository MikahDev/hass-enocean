"""EnOcean Direct: native EnOcean USB transceiver integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS
from .gateway import EnOceanHub

_LOGGER = logging.getLogger(__name__)

type EnOceanConfigEntry = ConfigEntry[EnOceanHub]


async def async_setup_entry(hass: HomeAssistant, entry: EnOceanConfigEntry) -> bool:
    """Set up the gateway from a config entry."""
    hub = EnOceanHub(hass, entry)
    try:
        await hub.async_start()
    except ConnectionError as err:
        raise ConfigEntryNotReady(f"Cannot connect to {hub.port}: {err}") from err

    entry.runtime_data = hub
    try:
        _sync_device_registry(hass, entry, hub)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # Any failure after the descriptor is open must release it.
        await hub.async_stop()
        raise

    async def _on_hass_stop(_: Event) -> None:
        await hub.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_hass_stop)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnOceanConfigEntry) -> bool:
    """Unload a config entry, releasing the serial descriptor."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return unload_ok


def _sync_device_registry(
    hass: HomeAssistant, entry: EnOceanConfigEntry, hub: EnOceanHub
) -> None:
    """Create device entries for all configured devices (rockers have no
    entities, so they need explicit registry entries for device triggers)
    and remove entries for devices no longer configured."""
    registry = dr.async_get(hass)
    for record in hub.devices.values():
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, record.address)},
            name=record.name,
            manufacturer="EnOcean",
            model=record.eep,
        )
    configured = {
        (DOMAIN, address) for address in hub.devices
    }
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not (device.identifiers & configured):
            registry.async_remove_device(device.id)
