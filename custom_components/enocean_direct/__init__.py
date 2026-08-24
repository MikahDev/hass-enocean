"""EnOcean Direct: native EnOcean USB transceiver integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import area_registry as ar
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
    """Create device entries for all configured devices before the platforms
    load (so the chosen area is applied on first creation, and rocker device
    triggers work) and remove entries for devices no longer configured."""
    registry = dr.async_get(hass)
    areas = ar.async_get(hass)
    for record in hub.devices.values():
        existing = registry.async_get_device(identifiers={(DOMAIN, record.address)})
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, record.address)},
            name=record.name,
            manufacturer="EnOcean",
            model=record.eep,
        )
        # Apply the chosen area only on first creation, so a room the user
        # later changes (or clears) in the UI is never overwritten on reload.
        # Unknown area ids (e.g. imported from another installation) are
        # skipped silently.
        if (
            existing is None
            and record.area_id
            and areas.async_get_area(record.area_id) is not None
        ):
            registry.async_update_device(device.id, area_id=record.area_id)
    configured = {(DOMAIN, address) for address in hub.devices}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not (device.identifiers & configured):
            registry.async_remove_device(device.id)
