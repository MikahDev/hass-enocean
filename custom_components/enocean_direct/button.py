"""Read-meter button for metering D2-01 actuators.

Many D2-01 meters only send their energy/power values when queried; the
button sends the two CMD 0x6 queries (energy, then power) so the user
controls exactly when a transmission happens. No polling.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EnOceanConfigEntry
from .const import DOMAIN, EEP_METERING
from .entity import EnOceanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        EnOceanReadMeterButton(hub, record)
        for record in hub.devices.values()
        if record.eep in EEP_METERING
    )


class EnOceanReadMeterButton(EnOceanEntity, ButtonEntity):
    """Sends the energy and power queries to one metering actuator."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "read_meter"

    def __init__(self, hub, record) -> None:
        super().__init__(hub, record)
        self._attr_unique_id = f"{record.address}-read_meter"

    async def async_press(self) -> None:
        if not await self.hub.async_query_measurement(self.record):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_not_acknowledged",
                translation_placeholders={"address": self.record.address},
            )
