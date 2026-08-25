"""D2-20-02 ventilation unit as a fan.

Speed feedback comes from the unit's own status messages (numeric percent).
The Auto preset is write-only: the status resolver drops the Auto sentinel,
so after selecting Auto the percentage is unknown until the unit reports a
numeric speed again; the preset is shown optimistically after the
transceiver acknowledged the command, mirroring the switch's assumed state.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EnOceanConfigEntry
from .const import DOMAIN, SIGNAL_FAN
from .entity import EnOceanEntity

FS_AUTO = 253
FS_DEFAULT = 254


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        EnOceanFan(hub, record)
        for record in hub.devices.values()
        if record.kind == "fan"
    )


class EnOceanFan(EnOceanEntity, FanEntity):
    """A D2-20-02 fan control unit."""

    _attr_name = None
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_preset_modes = ["auto"]
    # The base class defaults to 0 (off); unknown is the honest initial state.
    _attr_percentage = None

    def __init__(self, hub, record) -> None:
        super().__init__(hub, record)
        self._attr_unique_id = f"{record.address}-{record.channel}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_FAN.format(self.record.address),
                self._on_status,
            )
        )

    @property
    def is_on(self) -> bool | None:
        # No fake state: unknown until the unit reports (the base class
        # would report "off" for an unknown percentage).
        if self.preset_mode is not None:
            return True
        if self.percentage is None:
            return None
        return self.percentage > 0

    @callback
    def _on_status(self, percentage: int) -> None:
        self._attr_percentage = percentage
        self._attr_preset_mode = None
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        await self._send(percentage)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self._send(FS_AUTO)
        # Write-only preset: shown optimistically once acknowledged.
        self._attr_preset_mode = preset_mode
        self._attr_percentage = None
        self.async_write_ha_state()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
        elif percentage is not None:
            await self.async_set_percentage(percentage)
        else:
            await self._send(FS_DEFAULT)  # the unit's own default speed

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(0)

    async def _send(self, fan_speed: int) -> None:
        acknowledged = await self.hub.async_set_fan_speed(self.record, fan_speed)
        if not acknowledged:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_not_acknowledged",
                translation_placeholders={"address": self.record.address},
            )
