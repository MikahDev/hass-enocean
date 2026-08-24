"""D2-05-00 blinds actuator as a cover.

Position feedback comes from the actuator's CMD 0x4 position replies decoded
by the library's cover observer; nothing is fabricated, so the position is
unknown until the first reply. EnOcean polarity (0 = open, 100 = closed) is
converted to HA polarity (100 = open) in the hub.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EnOceanConfigEntry
from .const import DOMAIN, SIGNAL_COVER_STATE
from .entity import EnOceanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        EnOceanCover(hub, record)
        for record in hub.devices.values()
        if record.kind == "cover"
    )


class EnOceanCover(EnOceanEntity, CoverEntity):
    """One channel of a D2-05 blinds actuator."""

    _attr_name = None
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, hub, record) -> None:
        super().__init__(hub, record)
        self._attr_unique_id = f"{record.address}-{record.channel}"
        self._cover_state: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_COVER_STATE.format(self.record.address),
                self._on_status,
            )
        )

    @callback
    def _on_status(self, position: int | None, cover_state: str | None) -> None:
        if position is not None:
            self._attr_current_cover_position = position
        if cover_state is not None:
            self._cover_state = cover_state
        self.async_write_ha_state()

    @property
    def is_closed(self) -> bool | None:
        if self.current_cover_position is None:
            return None
        return self.current_cover_position == 0

    @property
    def is_opening(self) -> bool:
        return self._cover_state == "opening"

    @property
    def is_closing(self) -> bool:
        return self._cover_state == "closing"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "radio_channel": self.record.channel,
            "channel_number": self.record.channel_number,
            "sender_id": self.record.sender_id,
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._send(self.hub.async_open_cover(self.record))

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._send(self.hub.async_close_cover(self.record))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._send(self.hub.async_stop_cover(self.record))

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._send(
            self.hub.async_set_cover_position(self.record, int(kwargs[ATTR_POSITION]))
        )

    async def _send(self, send_coro) -> None:
        acknowledged = await send_coro
        if not acknowledged:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_not_acknowledged",
                translation_placeholders={"address": self.record.address},
            )
