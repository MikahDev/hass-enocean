"""D5-00-01 single input contact as a binary sensor.

EEP D5-00-01 CO bit: 0 = open, 1 = closed. In Home Assistant an opening
binary sensor is on when open, so is_on = (CO == 0). Verified against the
current EEP specification; deliberately NOT copied from the EnOcean MQTT UI
add-on, whose current mapping inverts this.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import EnOceanConfigEntry
from .const import SIGNAL_CONTACT
from .entity import EnOceanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        EnOceanContact(hub, record)
        for record in hub.devices.values()
        if record.kind == "contact"
    )


class EnOceanContact(EnOceanEntity, RestoreEntity, BinarySensorEntity):
    """A D5-00-01 contact. Receive-only; no sender ID, no transmit path."""

    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_name = None

    def __init__(self, hub, record) -> None:
        super().__init__(hub, record)
        self._attr_unique_id = record.address
        self._rssi_dbm: int | None = None
        self._last_received: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None and (
            last_state.state in ("on", "off")
        ):
            self._attr_is_on = last_state.state == "on"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CONTACT.format(self.record.address),
                self._on_telegram,
            )
        )

    @callback
    def _on_telegram(self, is_open: bool, rssi_dbm: int | None) -> None:
        self._attr_is_on = is_open
        self._rssi_dbm = rssi_dbm
        self._last_received = dt_util.utcnow().isoformat()
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "rssi_dbm": self._rssi_dbm,
            "last_received": self._last_received,
        }
