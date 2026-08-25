"""Sensors: decoded values from receive-only profiles (wave 2), plus the
per-device diagnostics (signal strength, last seen, telegram count, sender ID).

All values derive from telegrams the device sends anyway; nothing is polled
and nothing is transmitted. Counters restart at zero on reload, matching the
in-memory design of the radio inbox.
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EnOceanConfigEntry
from .const import EEP_METERING, SIGNAL_METERING, SIGNAL_SENSOR, SIGNAL_TELEGRAM
from .entity import EnOceanEntity
from .gateway import sensor_entities_for_eep

_LOGGER = logging.getLogger(__name__)

# One description per library entity id a wave-2 profile can report. The key
# doubles as translation key. fan_speed carries the EEP's stage labels as
# text, so it has no state class.
PROFILE_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    description.key: description
    for description in (
        SensorEntityDescription(
            key="temperature",
            translation_key="temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        SensorEntityDescription(
            key="temperature_setpoint",
            translation_key="temperature_setpoint",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        SensorEntityDescription(
            key="humidity",
            translation_key="humidity",
            device_class=SensorDeviceClass.HUMIDITY,
            native_unit_of_measurement="%",
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        SensorEntityDescription(
            key="illumination",
            translation_key="illumination",
            device_class=SensorDeviceClass.ILLUMINANCE,
            native_unit_of_measurement=LIGHT_LUX,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        ),
        SensorEntityDescription(
            key="supply_voltage",
            translation_key="supply_voltage",
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        SensorEntityDescription(
            key="fan_speed",
            translation_key="fan_speed",
        ),
        SensorEntityDescription(
            key="set_point",
            translation_key="set_point",
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        ),
        SensorEntityDescription(
            key="energy",
            translation_key="energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        SensorEntityDescription(
            key="power",
            translation_key="power",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        SensorEntityDescription(
            key="gas_volume",
            translation_key="gas_volume",
            device_class=SensorDeviceClass.GAS,
            native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        SensorEntityDescription(
            key="gas_flow",
            translation_key="gas_flow",
            device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
            native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_SECOND,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        SensorEntityDescription(
            key="water_volume",
            translation_key="water_volume",
            device_class=SensorDeviceClass.WATER,
            native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        SensorEntityDescription(
            key="water_flow",
            translation_key="water_flow",
            device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
            native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_SECOND,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        # counter: no state class. The library collapses A5-12-00's 16
        # measurement channels into one value, and interleaved channels would
        # corrupt TOTAL_INCREASING long-term statistics irreversibly.
        SensorEntityDescription(
            key="counter",
            translation_key="counter",
        ),
        SensorEntityDescription(
            key="counter_rate",
            translation_key="counter_rate",
            state_class=SensorStateClass.MEASUREMENT,
        ),
        SensorEntityDescription(
            key="window_state",
            translation_key="window_state",
            device_class=SensorDeviceClass.ENUM,
            options=["open", "tilted", "closed"],
        ),
    )
}


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
        if record.eep in EEP_METERING:
            entities.append(
                EnOceanMeterSensor(hub, record, PROFILE_DESCRIPTIONS["energy"], False)
            )
            entities.append(
                EnOceanMeterSensor(hub, record, PROFILE_DESCRIPTIONS["power"], True)
            )
        if record.kind != "sensor":
            continue
        for entity_id, observable, is_binary in sensor_entities_for_eep(record.eep):
            if is_binary:
                continue  # handled by the binary_sensor platform
            description = PROFILE_DESCRIPTIONS.get(entity_id)
            if description is None:
                _LOGGER.debug(
                    "No sensor description for %s entity %s", record.eep, entity_id
                )
                continue
            entities.append(EnOceanProfileSensor(hub, record, description, observable))
    async_add_entities(entities)


class EnOceanMeterSensor(EnOceanEntity, SensorEntity):
    """Energy or power of a metering D2-01 actuator (CMD 0x7 responses,
    locally normalised to Wh / W)."""

    def __init__(self, hub, record, description, is_power: bool) -> None:
        super().__init__(hub, record)
        self.entity_description = description
        self._is_power = is_power
        self._attr_unique_id = f"{record.address}-{description.key}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_METERING.format(self.record.address),
                self._on_measurement,
            )
        )

    @callback
    def _on_measurement(
        self, channel: int, energy_wh: float | None, power_w: float | None
    ) -> None:
        if channel not in (self.record.channel, 0x1E):  # 0x1E = all channels
            return
        value = power_w if self._is_power else energy_wh
        if value is None:
            return
        self._attr_native_value = value
        self.async_write_ha_state()


class EnOceanProfileSensor(EnOceanEntity, SensorEntity):
    """One decoded value of a receive-only profile."""

    def __init__(self, hub, record, description, observable: str) -> None:
        super().__init__(hub, record)
        self.entity_description = description
        self._observable = observable
        self._attr_unique_id = f"{record.address}-{description.key}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SENSOR.format(self.record.address),
                self._on_values,
            )
        )

    @callback
    def _on_values(self, entity_id: str, values: dict) -> None:
        if entity_id != self.entity_description.key or self._observable not in values:
            return
        value = values[self._observable]
        options = self.entity_description.options
        if options is not None and value not in options:
            # An enum label outside the declared vocabulary (e.g. after a
            # library bump) would make the state machine reject every write.
            _LOGGER.debug("%s: ignoring unknown label %r", self.entity_id, value)
            return
        self._attr_native_value = value
        self.async_write_ha_state()


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
