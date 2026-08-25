"""D2-01-0F actuator channel as a switch.

State handling: after a command is acknowledged by the transceiver the state
is optimistic and reported as assumed. The first D2-01 CMD 0x4 status telegram
from the actuator confirms it, after which the entity reports real state.
No status is ever fabricated: an unacknowledged command raises instead of
flipping the switch.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import EnOceanConfigEntry
from .const import DOMAIN, SIGNAL_SWITCH_STATE
from .entity import EnOceanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        EnOceanSwitch(hub, record)
        for record in hub.devices.values()
        if record.kind == "actuator"
    )


class EnOceanSwitch(EnOceanEntity, RestoreEntity, SwitchEntity):
    """One channel of a D2-01 actuator."""

    _attr_name = None

    def __init__(self, hub, record) -> None:
        super().__init__(hub, record)
        self._attr_unique_id = f"{record.address}-{record.channel}"
        self._confirmed = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The last known state survives restarts as "assumed" (still
        # unconfirmed) until the actuator's own status telegram arrives.
        if (last_state := await self.async_get_last_state()) is not None and (
            last_state.state in ("on", "off")
        ):
            self._attr_is_on = last_state.state == "on"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SWITCH_STATE.format(self.record.address),
                self._on_status,
            )
        )

    @callback
    def _on_status(self, channel: int, is_on: bool) -> None:
        if channel != self.record.channel:
            return
        self._attr_is_on = is_on
        self._confirmed = True
        self.async_write_ha_state()

    @property
    def assumed_state(self) -> bool:
        return not self._confirmed

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "radio_channel": self.record.channel,
            "channel_number": self.record.channel_number,
            "sender_id": self.record.sender_id,
            "state_confirmed": self._confirmed,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(False)

    async def _send(self, turn_on: bool) -> None:
        # The command invalidates the last confirmation; a CMD 0x4 status
        # arriving while we await the ack re-confirms and must not be
        # overwritten by the optimistic write below.
        self._confirmed = False
        acknowledged = await self.hub.async_set_output(self.record, turn_on)
        if not acknowledged:
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_not_acknowledged",
                translation_placeholders={"address": self.record.address},
            )
        if not self._confirmed:
            # Optimistic until the actuator's CMD 0x4 status telegram arrives.
            self._attr_is_on = turn_on
        self.async_write_ha_state()
