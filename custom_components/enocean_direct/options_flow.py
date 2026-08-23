"""Options flow: radio inbox, manual add, manage, import and export."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.helpers.selector import (
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
    EEP_ACTUATOR,
    EEP_CHANNEL_COUNT,
    EURID_MAX,
    KEY_ADDRESS,
    KEY_CHANNEL,
    KEY_EEP,
    KEY_NAME,
    KEY_SENDER_ID,
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
        self._remove_address: str | None = None

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
        return self.async_create_entry(data={CONF_DEVICES: raw_devices})

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
                "add_manual",
                "manage",
                "import_devices",
                "export_devices",
            ],
        )

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
                }
                if user_input[KEY_EEP] == EEP_ACTUATOR:
                    return await self.async_step_actuator()
                record = DeviceRecord(
                    address=address,
                    eep=user_input[KEY_EEP],
                    name=self._pending[KEY_NAME],
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
            self._remove_address = user_input[KEY_ADDRESS]
            return await self.async_step_remove_confirm()
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

    async def async_step_remove_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        target = next(
            (
                raw
                for raw in self._raw_devices
                if raw[KEY_ADDRESS] == self._remove_address
            ),
            None,
        )
        if target is None:
            return self.async_abort(reason="no_devices")
        if user_input is not None:
            remaining = [
                raw
                for raw in self._raw_devices
                if raw[KEY_ADDRESS] != self._remove_address
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
                if record.kind == "actuator"
                else ""
            )
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
