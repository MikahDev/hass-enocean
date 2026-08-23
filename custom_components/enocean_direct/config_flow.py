"""Config flow: select and validate the serial gateway."""

from __future__ import annotations

import glob
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_BASE_ID, CONF_DEVICE_PATH, DOMAIN
from .options_flow import EnOceanOptionsFlow

_LOGGER = logging.getLogger(__name__)

_PORT_GLOBS = (
    "/dev/serial/by-id/*",
    "/dev/ttyUSB*",
    "/dev/tty.usbserial*",
)


def _list_serial_ports() -> list[str]:
    ports: list[str] = []
    for pattern in _PORT_GLOBS:
        ports.extend(sorted(glob.glob(pattern)))
    return ports


async def validate_gateway(hass: HomeAssistant, path: str) -> str:
    """Open the gateway once to prove the port works, and return its Base ID.

    The library closes the descriptor itself on any start() failure; we stop
    explicitly on success so the flow never leaves the port open.
    """
    # Imported here so the config flow module stays importable without the hub.
    from enocean_async import Gateway

    gateway = Gateway(path)
    try:
        await gateway.start(auto_reconnect=False)
        base = gateway.base_id
    finally:
        await gateway.stop()
    if base is None:  # start() guarantees a base id, but be defensive
        raise ConnectionError("gateway did not report a base ID")
    return f"{int(base):08X}"


class EnOceanDirectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the gateway setup flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> EnOceanOptionsFlow:
        return EnOceanOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_step_device(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_step_device(user_input, reconfigure=True)

    async def _async_step_device(
        self, user_input: dict[str, Any] | None, reconfigure: bool
    ) -> ConfigFlowResult:
        step_id = "reconfigure" if reconfigure else "user"
        errors: dict[str, str] = {}
        if user_input is not None:
            path = user_input[CONF_DEVICE_PATH].strip()
            try:
                base_id = await validate_gateway(self.hass, path)
            except ConnectionError as err:
                _LOGGER.warning("Cannot connect to %s: %s", path, err)
                errors["base"] = "cannot_connect"
            else:
                data = {CONF_DEVICE_PATH: path, CONF_BASE_ID: base_id}
                if reconfigure:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data=data
                    )
                return self.async_create_entry(
                    title=f"EnOcean gateway {base_id}", data=data
                )

        ports = await self.hass.async_add_executor_job(_list_serial_ports)
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_PATH): SelectSelector(
                    SelectSelectorConfig(
                        options=ports,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)
