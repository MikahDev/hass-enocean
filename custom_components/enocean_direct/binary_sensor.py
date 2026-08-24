"""Binary sensors: the D5-00-01 contact and binary values of wave-2 profiles.

EEP D5-00-01 CO bit: 0 = open, 1 = closed. In Home Assistant an opening
binary sensor is on when open, so is_on = (CO == 0). Verified against the
current EEP specification; deliberately NOT copied from the EnOcean MQTT UI
add-on, whose current mapping inverts this.

Wave-2 binary values arrive as the EEP's own enum labels (they differ per
profile, and some rest states are active-low on the wire); the label, not the
raw bit, is authoritative. A5-10 contact follows the same polarity rule as
D5: is_on means open.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import EnOceanConfigEntry
from .const import SIGNAL_CONTACT, SIGNAL_SENSOR
from .entity import EnOceanEntity
from .gateway import sensor_entities_for_eep

_LOGGER = logging.getLogger(__name__)

# (description, labels meaning on, labels meaning off); unknown labels -> None
PROFILE_DESCRIPTIONS: dict[
    str, tuple[BinarySensorEntityDescription, frozenset[str], frozenset[str]]
] = {
    "motion": (
        BinarySensorEntityDescription(
            key="motion",
            translation_key="motion",
            device_class=BinarySensorDeviceClass.MOTION,
        ),
        frozenset({"motion detected", "motion"}),
        frozenset({"uncertain of occupancy status", "no motion"}),
    ),
    "occupancy_button": (
        BinarySensorEntityDescription(
            key="occupancy_button", translation_key="occupancy_button"
        ),
        frozenset({"pressed", "occupied"}),
        frozenset({"released", "unoccupied"}),
    ),
    "day_night": (
        BinarySensorEntityDescription(key="day_night", translation_key="day_night"),
        frozenset({"day/on"}),
        frozenset({"night/off"}),
    ),
    "contact_state": (
        BinarySensorEntityDescription(
            key="contact_state",
            translation_key="contact_state",
            device_class=BinarySensorDeviceClass.OPENING,
        ),
        frozenset({"open"}),
        frozenset({"closed"}),
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for record in hub.devices.values():
        if record.kind == "contact":
            entities.append(EnOceanContact(hub, record))
        elif record.kind == "sensor":
            for entity_id, observable, is_binary in sensor_entities_for_eep(record.eep):
                if not is_binary or entity_id not in PROFILE_DESCRIPTIONS:
                    continue
                description, on_labels, off_labels = PROFILE_DESCRIPTIONS[entity_id]
                entities.append(
                    EnOceanProfileBinarySensor(
                        hub, record, description, observable, on_labels, off_labels
                    )
                )
    async_add_entities(entities)


class EnOceanProfileBinarySensor(EnOceanEntity, BinarySensorEntity):
    """One binary value of a receive-only profile."""

    def __init__(
        self,
        hub,
        record,
        description,
        observable: str,
        on_labels: frozenset[str],
        off_labels: frozenset[str],
    ) -> None:
        super().__init__(hub, record)
        self.entity_description = description
        self._observable = observable
        self._on_labels = on_labels
        self._off_labels = off_labels
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
        label = str(values[self._observable]).lower()
        if label in self._on_labels:
            self._attr_is_on = True
        elif label in self._off_labels:
            self._attr_is_on = False
        else:
            _LOGGER.debug("%s: unknown label %r", self.entity_id, label)
            self._attr_is_on = None
        self.async_write_ha_state()


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
