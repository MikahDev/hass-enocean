"""Device triggers for F6-02-01 / F6-02-02 rocker switches."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EEP_ROCKERS, EVENT_BUTTON

CONF_SUBTYPE = "subtype"

TRIGGER_TYPES = ("pressed", "released")
BUTTON_SUBTYPES = ("ai", "ao", "bi", "bo")

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Optional(CONF_SUBTYPE): vol.In(BUTTON_SUBTYPES),
    }
)


def _rocker_address(hass: HomeAssistant, device_id: str) -> str | None:
    device = dr.async_get(hass).async_get(device_id)
    if device is None or device.model not in EEP_ROCKERS:
        return None
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            return identifier
    return None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """List triggers: a press per button plus a generic release."""
    if _rocker_address(hass, device_id) is None:
        return []
    base = {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id}
    triggers = [
        {**base, CONF_TYPE: "pressed", CONF_SUBTYPE: subtype}
        for subtype in BUTTON_SUBTYPES
    ]
    triggers.append({**base, CONF_TYPE: "released"})
    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger backed by the integration's button event."""
    event_data = {
        CONF_DEVICE_ID: config[CONF_DEVICE_ID],
        "type": config[CONF_TYPE],
    }
    if CONF_SUBTYPE in config:
        event_data["button"] = config[CONF_SUBTYPE]
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_BUTTON,
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
