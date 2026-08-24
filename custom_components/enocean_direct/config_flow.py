"""Config flow: select and validate the serial gateway."""

from __future__ import annotations

import asyncio
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
    AreaSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_BASE_ID,
    CONF_DEVICE_PATH,
    CONF_DEVICES,
    DOMAIN,
    EEP_CHANNEL_COUNT,
    KEY_ADDRESS,
    KEY_AREA,
    KEY_CHANNEL,
    KEY_EEP,
    KEY_NAME,
    KEY_SENDER_ID,
)
from .models import (
    AddressError,
    normalize_address,
    sender_in_base_range,
    validate_record,
)
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

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}
        self._pair_task: asyncio.Task | None = None
        self._pair_error: str | None = None

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> EnOceanOptionsFlow:
        return EnOceanOptionsFlow()

    def _hub_entry(self) -> ConfigEntry | None:
        entries = self._async_current_entries(include_ignore=False)
        return entries[0] if entries else None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # One gateway only. Enforced here rather than via single_config_entry
        # in the manifest, because that flag would also abort the per-device
        # discovery flows below.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return await self._async_step_device(user_input, reconfigure=False)

    # ------------------------------------------------------------------
    # per-device discovery (teach-in telegrams heard by the hub)
    # ------------------------------------------------------------------
    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        address = discovery_info[KEY_ADDRESS]
        eep = discovery_info[KEY_EEP]
        # Unique ID per device: dedupes concurrent cards and makes the
        # native Ignore button stick across future teach-ins.
        await self.async_set_unique_id(f"device-{address}")
        self._abort_if_unique_id_configured()
        hub = self._hub_entry()
        if hub is None:
            return self.async_abort(reason="no_gateway")
        if any(
            raw[KEY_ADDRESS] == address for raw in hub.options.get(CONF_DEVICES, [])
        ):
            return self.async_abort(reason="already_configured")
        self._discovered = {KEY_ADDRESS: address, KEY_EEP: eep}
        self.context["title_placeholders"] = {"name": f"{eep} {address}"}
        return await self.async_step_discovered_device()

    async def async_step_discovered_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._discovered[KEY_NAME] = (
                user_input[KEY_NAME].strip() or self._discovered[KEY_ADDRESS]
            )
            self._discovered[KEY_AREA] = user_input.get(KEY_AREA)
            if self._discovered[KEY_EEP] in EEP_CHANNEL_COUNT:
                return await self.async_step_pair_or_manual()
            return self._async_add_discovered_device()
        return self.async_show_form(
            step_id="discovered_device",
            data_schema=vol.Schema(
                {
                    vol.Required(KEY_NAME, default=""): TextSelector(),
                    vol.Optional(KEY_AREA): AreaSelector(),
                }
            ),
            description_placeholders={
                "address": self._discovered[KEY_ADDRESS],
                "eep": self._discovered[KEY_EEP],
            },
        )

    async def async_step_pair_or_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """New actuators can be paired (sender allocated automatically, the
        teach-in answered) or configured manually with a known sender."""
        return self.async_show_menu(
            step_id="pair_or_manual",
            menu_options=["pair", "discovered_actuator"],
            description_placeholders={"address": self._discovered[KEY_ADDRESS]},
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Guided pairing: allocate the first free sender, open a focused
        learning window and wait for the device's next teach-in."""
        from .gateway import PairingError

        address = self._discovered[KEY_ADDRESS]
        if self._pair_task is None:
            hub_entry = self._hub_entry()
            hub = getattr(hub_entry, "runtime_data", None) if hub_entry else None
            if hub is None or hub.gateway is None:
                return self.async_abort(reason="not_loaded")
            self._pair_task = self.hass.async_create_task(hub.async_pair(address))
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="pair_waiting",
                progress_task=self._pair_task,
                description_placeholders={"address": address},
            )
        try:
            _, _, sender_id = self._pair_task.result()
        except PairingError as err:
            self._pair_error = err.reason
            return self.async_show_progress_done(next_step_id="pair_failed")
        finally:
            self._pair_task = None
        self._discovered[KEY_SENDER_ID] = sender_id
        self._discovered[KEY_CHANNEL] = 0
        return self.async_show_progress_done(next_step_id="pair_done")

    async def async_step_pair_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self._async_add_discovered_device()

    async def async_step_pair_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason=self._pair_error or "pair_timeout")

    async def async_step_discovered_actuator(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        hub = self._hub_entry()
        base_id = hub.data.get(CONF_BASE_ID) if hub else None
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                sender_id = normalize_address(user_input[KEY_SENDER_ID])
            except AddressError:
                errors[KEY_SENDER_ID] = "invalid_sender"
            else:
                if base_id is not None and not sender_in_base_range(sender_id, base_id):
                    errors[KEY_SENDER_ID] = "sender_out_of_range"
            if not errors:
                self._discovered[KEY_SENDER_ID] = sender_id
                self._discovered[KEY_CHANNEL] = int(user_input[KEY_CHANNEL])
                return self._async_add_discovered_device()

        max_channel = EEP_CHANNEL_COUNT[self._discovered[KEY_EEP]] - 1
        schema = vol.Schema(
            {
                vol.Required(KEY_SENDER_ID, default=base_id or ""): TextSelector(),
                vol.Required(KEY_CHANNEL, default=0): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=max_channel, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="discovered_actuator",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "address": self._discovered[KEY_ADDRESS],
                "base_id": base_id or "unknown",
            },
        )

    def _async_add_discovered_device(self) -> ConfigFlowResult:
        hub = self._hub_entry()
        if hub is None:
            return self.async_abort(reason="no_gateway")
        existing = {raw[KEY_ADDRESS] for raw in hub.options.get(CONF_DEVICES, [])}
        record, _errors = validate_record(
            self._discovered, hub.data.get(CONF_BASE_ID), existing
        )
        if record is None:  # only duplicates can slip through the form checks
            return self.async_abort(reason="already_configured")
        devices = [*hub.options.get(CONF_DEVICES, []), record.as_dict()]
        self.hass.config_entries.async_update_entry(
            hub, options={**hub.options, CONF_DEVICES: devices}
        )
        self.hass.config_entries.async_schedule_reload(hub.entry_id)
        return self.async_abort(reason="device_added")

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
