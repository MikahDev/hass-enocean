"""Base entity: device info and gateway availability."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_CONNECTION
from .gateway import EnOceanHub
from .models import DeviceRecord


class EnOceanEntity(Entity):
    """Common base for EnOcean Direct entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: EnOceanHub, record: DeviceRecord) -> None:
        self.hub = hub
        self.record = record
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, record.address)},
            name=record.name,
            manufacturer="EnOcean",
            model=record.eep,
        )

    @property
    def available(self) -> bool:
        return self.hub.connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CONNECTION.format(self.hub.entry.entry_id),
                self._on_connection_change,
            )
        )

    @callback
    def _on_connection_change(self, _connected: bool) -> None:
        self.async_write_ha_state()
