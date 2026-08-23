"""Diagnostics. Radio IDs and hardware identifiers are redacted by default."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EnOceanConfigEntry
from .const import KEY_ADDRESS, KEY_SENDER_ID

TO_REDACT = {KEY_ADDRESS, KEY_SENDER_ID, "base_id", "device", "sender", "address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EnOceanConfigEntry
) -> dict[str, Any]:
    hub = entry.runtime_data
    return async_redact_data(
        {
            "entry_data": dict(entry.data),
            "connected": hub.connected,
            "devices": [record.as_dict() for record in hub.devices.values()],
            "inbox": [item.as_dict() for item in hub.inbox.entries],
        },
        TO_REDACT,
    )
