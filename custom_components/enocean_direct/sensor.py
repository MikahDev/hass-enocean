"""Per-device diagnostic sensors: signal strength, last seen, telegram count,
and the configured sender ID.

All values derive from telegrams the device sends anyway; nothing is polled
and nothing is transmitted. Counters restart at zero on reload, matching the
in-memory design of the radio inbox.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EnOceanConfigEntry
from .const import SIGNAL_TELEGRAM
from .entity import EnOceanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    entities: list[SensorEntity] = []
    for record in hub.devices.values():
        entities.append(EnOceanRSSISensor(hub, record))
        entities.append(EnOceanLastSeenSensor(hub, record))
        entities.append(EnOceanTelegramCountSensor(hub, record))
        if record.sender_id is not None:
            entities.append(EnOceanSenderIDSensor(hub, record))
    async_add_entities(entities)


class EnOceanTelegramSensor(EnOceanEntity, SensorEntity):
    """Base for sensors fed by every telegram the device sends."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub, record) -> None:
        super().__init__(hub, record)
        self._attr_unique_id = f"{record.address}-{self._attr_translation_key}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_TELEGRAM.format(self.record.address),
                self._on_telegram,
            )
        )

    @callback
    def _on_telegram(self, rssi_dbm: int | None, when: datetime) -> None:
        raise NotImplementedError


class EnOceanRSSISensor(EnOceanTelegramSensor):
    """Signal strength of the last telegram (None when not reported).

    Deliberately enabled by default, against the HA quality-scale suggestion
    for RSSI sensors: reception debugging is the reason this entity exists.
    """

    _attr_translation_key = "rssi"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    @callback
    def _on_telegram(self, rssi_dbm: int | None, when: datetime) -> None:
        self._attr_native_value = rssi_dbm
        self.async_write_ha_state()


class EnOceanLastSeenSensor(EnOceanTelegramSensor):
    """When the last telegram from this device was received."""

    _attr_translation_key = "last_seen"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @callback
    def _on_telegram(self, rssi_dbm: int | None, when: datetime) -> None:
        self._attr_native_value = when
        self.async_write_ha_state()


class EnOceanTelegramCountSensor(EnOceanTelegramSensor):
    """Telegrams received from this device since the last (re)load."""

    _attr_translation_key = "telegram_count"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_registry_enabled_default = False
    _attr_native_value = 0

    @callback
    def _on_telegram(self, rssi_dbm: int | None, when: datetime) -> None:
        self._attr_native_value += 1
        self.async_write_ha_state()


class EnOceanSenderIDSensor(EnOceanEntity, SensorEntity):
    """The controller sender ID this device answers to (static, for debug)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "sender_id"

    def __init__(self, hub, record) -> None:
        super().__init__(hub, record)
        self._attr_unique_id = f"{record.address}-sender_id"
        self._attr_native_value = record.sender_id
