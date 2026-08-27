"""Options flow: radio inbox, pairing, manual add, manage (edit / remove),
import and export."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    AreaSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_BASE_ID,
    CONF_DEVICES,
    CONF_QUERY_STARTUP,
    CONF_REPEATER,
    DOMAIN,
    EEP_ACTUATORS,
    EEP_CHANNEL_COUNT,
    EEP_COVER,
    EURID_MAX,
    KEY_ADDRESS,
    KEY_AREA,
    KEY_CHANNEL,
    KEY_EEP,
    KEY_INVERT,
    KEY_NAME,
    KEY_SENDER_ID,
    REPEATER_MODES,
    SUPPORTED_EEPS,
)
from .models import (
    AddressError,
    DeviceRecord,
    build_export,
    normalize_address,
    parse_import,
    record_from_dict,
    sender_in_base_range,
)

_EEP_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=list(SUPPORTED_EEPS), mode=SelectSelectorMode.DROPDOWN)
)


class EnOceanOptionsFlow(OptionsFlowWithReload):
    """Manage devices behind the gateway entry."""

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}
        self._import_records: list[DeviceRecord] | None = None
        self._manage_address: str | None = None
        self._pair_task: asyncio.Task | None = None
        self._pair_error: str | None = None
        self._params_address: str | None = None
        self._new_base_id: str | None = None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def _raw_devices(self) -> list[dict[str, Any]]:
        return list(self.config_entry.options.get(CONF_DEVICES, []))

    @property
    def _addresses(self) -> set[str]:
        return {raw[KEY_ADDRESS] for raw in self._raw_devices}

    @property
    def _base_id(self) -> str | None:
        return self.config_entry.data.get(CONF_BASE_ID)

    def _save(self, raw_devices: list[dict[str, Any]]) -> ConfigFlowResult:
        # Preserve non-device options (e.g. the startup-query setting).
        return self.async_create_entry(
            data={**self.config_entry.options, CONF_DEVICES: raw_devices}
        )

    # ------------------------------------------------------------------
    # steps
    # ------------------------------------------------------------------
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "inbox",
                "pair_device",
                "add_manual",
                "module_params",
                "manage",
                "settings",
                "base_id",
                "import_devices",
                "export_devices",
            ],
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **self.config_entry.options,
                    CONF_QUERY_STARTUP: user_input[CONF_QUERY_STARTUP],
                    CONF_REPEATER: user_input[CONF_REPEATER],
                }
            )
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_QUERY_STARTUP,
                        default=self.config_entry.options.get(
                            CONF_QUERY_STARTUP, False
                        ),
                    ): bool,
                    vol.Required(
                        CONF_REPEATER,
                        default=self.config_entry.options.get(CONF_REPEATER, "off"),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(REPEATER_MODES),
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key="repeater",
                        )
                    ),
                }
            ),
        )

    # ------------------------------------------------------------------
    # Base ID recovery: write an exported Base ID onto a replacement stick.
    # Two steps (value, then retype) because the module allows 10 writes ever.
    # ------------------------------------------------------------------
    async def async_step_base_id(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        hub = getattr(self.config_entry, "runtime_data", None)
        if hub is None or hub.gateway is None:
            return self.async_abort(reason="not_loaded")
        if not hub.connected or hub.base_id is None:
            return self.async_abort(reason="not_connected")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                new_base_id = normalize_address(user_input["new_base_id"])
            except AddressError:
                errors["new_base_id"] = "invalid_base_id"
            else:
                if not 0xFF800000 <= int(new_base_id, 16) <= 0xFFFFFF80:
                    errors["new_base_id"] = "base_id_out_of_range"
                elif new_base_id == hub.base_id:
                    errors["new_base_id"] = "base_id_unchanged"
            if not errors:
                self._new_base_id = new_base_id
                return await self.async_step_base_id_confirm()
        remaining = hub.base_id_remaining_write_cycles
        return self.async_show_form(
            step_id="base_id",
            data_schema=vol.Schema({vol.Required("new_base_id"): TextSelector()}),
            errors=errors,
            description_placeholders={
                "base_id": hub.base_id,
                "remaining": "?" if remaining is None else str(remaining),
            },
        )

    async def async_step_base_id_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from .gateway import BaseIDError

        hub = getattr(self.config_entry, "runtime_data", None)
        if hub is None or hub.gateway is None:
            return self.async_abort(reason="not_loaded")
        if not hub.connected or hub.base_id is None:
            return self.async_abort(reason="not_connected")
        assert self._new_base_id is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                retyped = normalize_address(user_input["confirm_base_id"])
            except AddressError:
                retyped = None
            if retyped != self._new_base_id:
                errors["confirm_base_id"] = "confirm_mismatch"
            else:
                try:
                    await hub.async_change_base_id(self._new_base_id)
                except BaseIDError as err:
                    errors["base"] = err.reason
                else:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={
                            **self.config_entry.data,
                            CONF_BASE_ID: self._new_base_id,
                        },
                        title=f"EnOcean gateway {self._new_base_id}",
                    )
                    # Options are unchanged, so OptionsFlowWithReload would
                    # not reload; the hub must restart on the new Base ID.
                    self.hass.config_entries.async_schedule_reload(
                        self.config_entry.entry_id
                    )
                    return self.async_abort(
                        reason="base_id_changed",
                        description_placeholders={"base_id": self._new_base_id},
                    )
        remaining = hub.base_id_remaining_write_cycles
        return self.async_show_form(
            step_id="base_id_confirm",
            data_schema=vol.Schema({vol.Required("confirm_base_id"): TextSelector()}),
            errors=errors,
            description_placeholders={
                "base_id": hub.base_id,
                "new_base_id": self._new_base_id,
                "remaining": "?" if remaining is None else str(remaining),
            },
        )

    # ------------------------------------------------------------------
    # module parameters: D2-01 CMD 0x2 Actuator Set Local
    # ------------------------------------------------------------------
    async def async_step_module_params(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        actuators = [raw for raw in self._raw_devices if raw[KEY_EEP] in EEP_ACTUATORS]
        if not actuators:
            return self.async_abort(reason="no_actuators")
        if user_input is not None:
            self._params_address = user_input[KEY_ADDRESS]
            return await self.async_step_module_params_form()
        options = [
            SelectOptionDict(
                value=raw[KEY_ADDRESS],
                label=f"{raw[KEY_NAME]} | {raw[KEY_ADDRESS]} | {raw[KEY_EEP]}",
            )
            for raw in actuators
        ]
        return self.async_show_form(
            step_id="module_params",
            data_schema=vol.Schema(
                {
                    vol.Required(KEY_ADDRESS): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def async_step_module_params_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One CMD 0x2 telegram sets every local parameter at once, so every
        consequential field is shown; nothing is written silently."""
        from homeassistant.exceptions import HomeAssistantError

        hub = getattr(self.config_entry, "runtime_data", None)
        record = hub.devices.get(self._params_address) if hub else None
        if hub is None or hub.gateway is None or record is None:
            return self.async_abort(reason="not_loaded")
        if not hub.connected:
            return self.async_abort(reason="not_connected")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                acknowledged = await hub.async_set_local(
                    record,
                    taught_in_enabled=user_input["taught_in_enabled"],
                    overcurrent_restart=user_input["overcurrent_restart"],
                    local_control=user_input["local_control"],
                    power_failure_detection=user_input["power_failure_detection"],
                    default_state={"off": 0, "on": 1, "previous": 2}[
                        user_input["default_state"]
                    ],
                )
            except HomeAssistantError:
                # e.g. the transceiver disconnected between render and submit
                acknowledged = False
                errors["base"] = "cannot_send"
            else:
                if acknowledged:
                    return self.async_abort(reason="params_sent")
                errors["base"] = "not_acknowledged"
        schema = vol.Schema(
            {
                vol.Required("local_control", default=True): bool,
                vol.Required("taught_in_enabled", default=True): bool,
                vol.Required("overcurrent_restart", default=False): bool,
                vol.Required("power_failure_detection", default=False): bool,
                vol.Required("default_state", default="previous"): SelectSelector(
                    SelectSelectorConfig(
                        options=["off", "on", "previous"],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="default_state",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="module_params_form",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "name": record.name,
                "address": record.address,
            },
        )

    # ------------------------------------------------------------------
    # one-press pairing: window first, then a single teach-in press
    # ------------------------------------------------------------------
    async def async_step_pair_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from .gateway import PairingError

        if self._pair_task is None:
            hub = getattr(self.config_entry, "runtime_data", None)
            if hub is None or hub.gateway is None:
                return self.async_abort(reason="not_loaded")
            self._pair_task = self.hass.async_create_task(hub.async_pair())
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair_device",
                progress_action="pair_waiting_any",
                progress_task=self._pair_task,
            )
        try:
            address, eep, sender_id = self._pair_task.result()
        except PairingError as err:
            self._pair_error = err.reason
            return self.async_show_progress_done(next_step_id="pair_failed")
        finally:
            self._pair_task = None
        if address in self._addresses:
            self._pair_error = "already_configured"
            return self.async_show_progress_done(next_step_id="pair_failed")
        self._pending = {
            KEY_ADDRESS: address,
            KEY_EEP: eep,
            KEY_SENDER_ID: sender_id,
        }
        return self.async_show_progress_done(next_step_id="pair_name")

    async def async_step_pair_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            record = DeviceRecord(
                address=self._pending[KEY_ADDRESS],
                eep=self._pending[KEY_EEP],
                name=user_input[KEY_NAME].strip() or self._pending[KEY_ADDRESS],
                sender_id=self._pending[KEY_SENDER_ID],
                channel=0,
                area_id=user_input.get(KEY_AREA),
            )
            return self._save([*self._raw_devices, record.as_dict()])
        return self.async_show_form(
            step_id="pair_name",
            data_schema=vol.Schema(
                {
                    vol.Required(KEY_NAME, default=""): TextSelector(),
                    vol.Optional(KEY_AREA): AreaSelector(),
                }
            ),
            description_placeholders={
                "address": self._pending[KEY_ADDRESS],
                "eep": self._pending[KEY_EEP],
                "sender_id": self._pending[KEY_SENDER_ID],
            },
        )

    async def async_step_pair_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason=self._pair_error or "pair_timeout")

    async def async_step_inbox(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        hub = getattr(self.config_entry, "runtime_data", None)
        if hub is None or hub.gateway is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            address = user_input[KEY_ADDRESS]
            entry = hub.inbox.get(address)
            self._pending = {KEY_ADDRESS: address}
            if entry is not None and entry.eep is not None:
                self._pending[KEY_EEP] = entry.eep
            return await self.async_step_add_manual()

        entries = hub.inbox.unconfigured()
        if not entries:
            return self.async_abort(reason="inbox_empty")
        options = [
            SelectOptionDict(
                value=entry.address,
                label=(
                    f"{entry.address} | {entry.telegram_type} | "
                    f"{entry.eep or 'profile unknown'} | "
                    f"RSSI {entry.rssi_dbm if entry.rssi_dbm is not None else '?'} dBm | "
                    f"{entry.count}x | last {entry.last_seen:%H:%M:%S}"
                ),
            )
            for entry in entries
        ]
        return self.async_show_form(
            step_id="inbox",
            data_schema=vol.Schema(
                {
                    vol.Required(KEY_ADDRESS): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def async_step_add_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                address = normalize_address(user_input[KEY_ADDRESS])
            except AddressError:
                errors[KEY_ADDRESS] = "invalid_address"
            else:
                if int(address, 16) > EURID_MAX:
                    errors[KEY_ADDRESS] = "not_eurid"
                elif address in self._addresses:
                    errors[KEY_ADDRESS] = "duplicate_address"
            if not errors:
                self._pending = {
                    KEY_ADDRESS: address,
                    KEY_EEP: user_input[KEY_EEP],
                    KEY_NAME: user_input[KEY_NAME].strip() or address,
                    KEY_AREA: user_input.get(KEY_AREA),
                }
                if user_input[KEY_EEP] in EEP_CHANNEL_COUNT:
                    return await self.async_step_actuator()
                record = DeviceRecord(
                    address=address,
                    eep=user_input[KEY_EEP],
                    name=self._pending[KEY_NAME],
                    area_id=self._pending[KEY_AREA],
                )
                return self._save([*self._raw_devices, record.as_dict()])

        defaults = self._pending
        schema = vol.Schema(
            {
                vol.Required(
                    KEY_ADDRESS, default=defaults.get(KEY_ADDRESS, "")
                ): TextSelector(),
                vol.Required(
                    KEY_EEP, default=defaults.get(KEY_EEP, SUPPORTED_EEPS[0])
                ): _EEP_SELECTOR,
                vol.Required(KEY_NAME, default=""): TextSelector(),
                vol.Optional(KEY_AREA): AreaSelector(),
            }
        )
        return self.async_show_form(
            step_id="add_manual", data_schema=schema, errors=errors
        )

    async def async_step_actuator(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Actuator specifics: sender ID and radio channel are explicit,
        never allocated. The Base ID is only ever a proposed default."""
        errors: dict[str, str] = {}
        base_id = self._base_id
        if user_input is not None:
            try:
                sender_id = normalize_address(user_input[KEY_SENDER_ID])
            except AddressError:
                errors[KEY_SENDER_ID] = "invalid_sender"
            else:
                if base_id is not None and not sender_in_base_range(sender_id, base_id):
                    errors[KEY_SENDER_ID] = "sender_out_of_range"
            if not errors:
                record = DeviceRecord(
                    address=self._pending[KEY_ADDRESS],
                    eep=self._pending[KEY_EEP],
                    name=self._pending[KEY_NAME],
                    sender_id=sender_id,
                    channel=int(user_input[KEY_CHANNEL]),
                    area_id=self._pending.get(KEY_AREA),
                )
                return self._save([*self._raw_devices, record.as_dict()])

        max_channel = EEP_CHANNEL_COUNT[self._pending[KEY_EEP]] - 1
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
            step_id="actuator",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "base_id": base_id or "unknown",
                "address": self._pending.get(KEY_ADDRESS, ""),
            },
        )

    async def async_step_manage(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        devices = self._raw_devices
        if not devices:
            return self.async_abort(reason="no_devices")
        if user_input is not None:
            self._manage_address = user_input[KEY_ADDRESS]
            return await self.async_step_manage_device()
        options = [
            SelectOptionDict(
                value=raw[KEY_ADDRESS],
                label=f"{raw[KEY_NAME]} | {raw[KEY_ADDRESS]} | {raw[KEY_EEP]}",
            )
            for raw in devices
        ]
        return self.async_show_form(
            step_id="manage",
            data_schema=vol.Schema(
                {
                    vol.Required(KEY_ADDRESS): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    def _manage_target(self) -> dict[str, Any] | None:
        return next(
            (
                raw
                for raw in self._raw_devices
                if raw[KEY_ADDRESS] == self._manage_address
            ),
            None,
        )

    async def async_step_manage_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        target = self._manage_target()
        if target is None:
            return self.async_abort(reason="no_devices")
        return self.async_show_menu(
            step_id="manage_device",
            menu_options=["edit_device", "remove_confirm"],
            description_placeholders={
                "name": target[KEY_NAME],
                "address": target[KEY_ADDRESS],
                "eep": target[KEY_EEP],
            },
        )

    async def async_step_edit_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit in place: name and room for every device, sender ID and
        channel for transmitting ones, direction inversion for covers. The
        address and EEP are identity and stay fixed, so entity registry
        entries (unique_id = address-channel) survive the edit."""
        target = self._manage_target()
        if target is None:
            return self.async_abort(reason="no_devices")
        record = record_from_dict(target)
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, record.address)})
        # What the user currently sees: a UI rename (name_by_user) wins.
        current_name = (device.name_by_user or device.name) if device else None
        current_name = current_name or record.name
        current_area = device.area_id if device else record.area_id
        transmits = record.eep in EEP_CHANNEL_COUNT
        base_id = self._base_id
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[KEY_NAME].strip() or record.address
            area_id = user_input.get(KEY_AREA)
            sender_id = record.sender_id
            channel = record.channel
            if transmits:
                try:
                    sender_id = normalize_address(user_input[KEY_SENDER_ID])
                except AddressError:
                    errors[KEY_SENDER_ID] = "invalid_sender"
                else:
                    if base_id is not None and not sender_in_base_range(
                        sender_id, base_id
                    ):
                        errors[KEY_SENDER_ID] = "sender_out_of_range"
                channel = int(user_input[KEY_CHANNEL])
            if not errors:
                updated = DeviceRecord(
                    address=record.address,
                    eep=record.eep,
                    name=name,
                    sender_id=sender_id,
                    channel=channel,
                    area_id=area_id,
                    invert=bool(user_input.get(KEY_INVERT, False))
                    if record.eep == EEP_COVER
                    else False,
                )
                if device is not None:
                    # async_get_or_create on reload refreshes the integration
                    # name but a UI rename would still mask it; the name typed
                    # here is what the user wants to see, so clear the override.
                    changes: dict[str, Any] = {}
                    if name != current_name:
                        changes.update(name=name, name_by_user=None)
                    if area_id != current_area:
                        changes["area_id"] = area_id
                    if changes:
                        registry.async_update_device(device.id, **changes)
                devices = [
                    updated.as_dict() if raw[KEY_ADDRESS] == record.address else raw
                    for raw in self._raw_devices
                ]
                return self._save(devices)

        schema: dict[Any, Any] = {
            vol.Required(KEY_NAME, default=current_name): TextSelector(),
            vol.Optional(
                KEY_AREA, description={"suggested_value": current_area}
            ): AreaSelector(),
        }
        if transmits:
            max_channel = EEP_CHANNEL_COUNT[record.eep] - 1
            schema[vol.Required(KEY_SENDER_ID, default=record.sender_id or "")] = (
                TextSelector()
            )
            schema[vol.Required(KEY_CHANNEL, default=record.channel)] = NumberSelector(
                NumberSelectorConfig(
                    min=0, max=max_channel, step=1, mode=NumberSelectorMode.BOX
                )
            )
        if record.eep == EEP_COVER:
            schema[vol.Required(KEY_INVERT, default=record.invert)] = bool
        data_schema = vol.Schema(schema)
        if user_input is not None:
            # Re-render after a validation error keeps what was typed.
            data_schema = self.add_suggested_values_to_schema(data_schema, user_input)
        return self.async_show_form(
            step_id="edit_device",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "name": current_name,
                "address": record.address,
                "eep": record.eep,
                "base_id": base_id or "unknown",
            },
        )

    async def async_step_remove_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        target = self._manage_target()
        if target is None:
            return self.async_abort(reason="no_devices")
        if user_input is not None:
            remaining = [
                raw
                for raw in self._raw_devices
                if raw[KEY_ADDRESS] != self._manage_address
            ]
            return self._save(remaining)
        return self.async_show_form(
            step_id="remove_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": target[KEY_NAME],
                "address": target[KEY_ADDRESS],
            },
        )

    async def async_step_import_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        detail = ""
        if user_input is not None:
            result = parse_import(
                user_input["document"], self._base_id, self._addresses
            )
            if result.ok:
                self._import_records = result.records
                return await self.async_step_import_confirm()
            errors["document"] = "import_invalid"
            detail = "\n".join(result.errors[:15])

        return self.async_show_form(
            step_id="import_devices",
            data_schema=vol.Schema(
                {
                    vol.Required("document"): TextSelector(
                        TextSelectorConfig(multiline=True)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"detail": detail},
        )

    async def async_step_import_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dry-run summary. Nothing is persisted until this step is submitted."""
        assert self._import_records is not None
        if user_input is not None:
            merged = self._raw_devices + [
                record.as_dict() for record in self._import_records
            ]
            return self._save(merged)
        summary = "\n".join(
            f"- {record.name} | {record.address} | {record.eep}"
            + (
                f" | sender {record.sender_id} | channel {record.channel}"
                if record.sender_id is not None
                else ""
            )
            + (" | inverted" if record.invert else "")
            for record in self._import_records
        )
        return self.async_show_form(
            step_id="import_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "count": str(len(self._import_records)),
                "summary": summary,
            },
        )

    async def async_step_export_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=dict(self.config_entry.options))
        records = [record_from_dict(raw) for raw in self._raw_devices]
        return self.async_show_form(
            step_id="export_devices",
            data_schema=vol.Schema({}),
            description_placeholders={
                "export_json": build_export(records, self._base_id)
            },
        )
