"""Serial transport owner. The only module that imports enocean_async.

One EnOceanHub instance owns the serial descriptor for one config entry.
Every lifecycle path (unload, reload, failed setup, USB disconnect, HA stop)
funnels through async_stop(), which closes the transport deterministically.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from enocean_async import (
    EEP,
    EURID,
    BaseAddress,
    Gateway,
    Observable,
    Observation,
    device_type_for_eep,
)
from enocean_async.protocol.erp1.rorg import RORG
from enocean_async.protocol.esp3.response import ResponseCode
from enocean_async.semantics.instructions.switch import SetSwitchOutput

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_BASE_ID,
    CONF_DEVICE_PATH,
    CONF_DEVICES,
    DOMAIN,
    EEP_ACTUATOR,
    EEP_CONTACT,
    EEP_ROCKERS,
    EVENT_BUTTON,
    ISSUE_SERIAL_DISCONNECTED,
    SIGNAL_CONNECTION,
    SIGNAL_CONTACT,
    SIGNAL_SWITCH_STATE,
)
from .inbox import RadioInbox
from .models import DeviceRecord, record_from_dict
from .profiles import decode_d5, decode_f6

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


def _rssi_dbm(raw: int | None) -> int | None:
    """Convert the ERP1 optional-data RSSI byte to dBm (0xFF = not available)."""
    if raw is None or raw in (0x00, 0xFF):
        return None
    return -raw


class EnOceanHub:
    """Owns the gateway connection and routes telegrams to HA."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.port: str = entry.data[CONF_DEVICE_PATH]
        self.devices: dict[str, DeviceRecord] = {
            raw["address"]: record_from_dict(raw)
            for raw in entry.options.get(CONF_DEVICES, [])
        }
        self.inbox = RadioInbox()
        self.gateway: Gateway | None = None
        self.connected = False
        self.base_id: str | None = entry.data.get(CONF_BASE_ID)
        self._stopped = False

    async def async_start(self) -> None:
        """Open the serial port and register configured devices.

        Raises ConnectionError if the port cannot be opened or the module
        does not answer; enocean_async closes the descriptor itself in that
        case, so there is nothing to release here.
        """
        gateway = Gateway(self.port)
        gateway.add_erp1_received_callback(self._on_erp1)
        gateway.add_observation_callback(self._on_observation)
        await gateway.start(auto_reconnect=True)

        self.gateway = gateway
        self.connected = True
        base = gateway.base_id
        if base is not None:
            self.base_id = f"{int(base):08X}"

        # Register actuators with the library so incoming D2-01 status
        # telegrams are decoded. Contacts and rockers are decoded locally.
        for record in self.devices.values():
            if record.kind != "actuator":
                continue
            try:
                gateway.add_device(
                    address=EURID(int(record.address, 16)),
                    device_type=device_type_for_eep(EEP(record.eep)),
                    sender=BaseAddress(int(record.sender_id, 16)),
                    name=record.name,
                )
            except ValueError as err:
                _LOGGER.error(
                    "Could not register actuator %s: %s", record.address, err
                )

    async def async_stop(self) -> None:
        """Close the serial connection. Safe to call more than once."""
        self._stopped = True
        if self.gateway is not None:
            await self.gateway.stop()
            self.gateway = None
        self.connected = False

    # ------------------------------------------------------------------
    # incoming
    # ------------------------------------------------------------------
    @callback
    def _on_erp1(self, erp1: Any) -> None:
        if self._stopped:
            return
        address = f"{int(erp1.sender):08X}"
        rssi = _rssi_dbm(erp1.rssi)
        declared_eep = None
        if erp1.rorg == RORG.RORG_1BS and erp1.is_learning_telegram:
            # 1BS carries no EEP data, but D5-00-01 is the only 1BS profile.
            declared_eep = EEP_CONTACT
        record = self.devices.get(address)
        self.inbox.record(
            address=address,
            rorg=int(erp1.rorg),
            telegram_type=erp1.rorg.simple_name,
            configured=record is not None,
            rssi_dbm=rssi,
            declared_eep=declared_eep,
        )
        if record is None:
            return

        if record.eep == EEP_CONTACT and erp1.rorg == RORG.RORG_1BS:
            reading = decode_d5(bytes(erp1.telegram_data))
            if reading is not None and not reading.is_teach_in:
                async_dispatcher_send(
                    self.hass,
                    SIGNAL_CONTACT.format(address),
                    reading.is_open,
                    rssi,
                )
        elif record.eep in EEP_ROCKERS and erp1.rorg == RORG.RORG_RPS:
            action = decode_f6(bytes(erp1.telegram_data), erp1.status)
            if action is not None:
                self._fire_button_event(record, action)

    def _fire_button_event(self, record: DeviceRecord, action) -> None:
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, record.address)})
        self.hass.bus.async_fire(
            EVENT_BUTTON,
            {
                "device_id": device.id if device else None,
                "address": record.address,
                "type": action.action,
                "button": action.button,
                "second_button": action.second_button,
            },
        )

    @callback
    def _on_observation(self, observation: Observation) -> None:
        if self._stopped:
            return
        values = observation.values
        if Observable.CONNECTION_STATUS in values:
            self._on_connection_status(values[Observable.CONNECTION_STATUS])
            return
        if Observable.SWITCH_STATE in values and observation.entity.endswith(
            "_switch_state"
        ):
            # Library entity ids are ch1_..., ch2_... (radio channel + 1).
            try:
                channel = int(observation.entity[2:].split("_", 1)[0]) - 1
            except ValueError:
                return
            address = f"{int(observation.device):08X}"
            async_dispatcher_send(
                self.hass,
                SIGNAL_SWITCH_STATE.format(address),
                channel,
                bool(values[Observable.SWITCH_STATE]),
            )

    def _on_connection_status(self, status: str) -> None:
        connected = status == "connected"
        if connected == self.connected:
            return
        self.connected = connected
        _LOGGER.log(
            logging.INFO if connected else logging.WARNING,
            "EnOcean gateway on %s is %s",
            self.port,
            status,
        )
        if connected:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_SERIAL_DISCONNECTED)
        else:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_SERIAL_DISCONNECTED,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_SERIAL_DISCONNECTED,
                translation_placeholders={"port": self.port},
            )
        async_dispatcher_send(
            self.hass, SIGNAL_CONNECTION.format(self.entry.entry_id), connected
        )

    # ------------------------------------------------------------------
    # outgoing (bounded to configured D2-01 switch entities)
    # ------------------------------------------------------------------
    async def async_set_output(self, record: DeviceRecord, turn_on: bool) -> bool:
        """Send a D2-01 CMD 0x1 Actuator Set Output. Returns True if the
        transceiver acknowledged the transmission with RET_OK."""
        gateway = self.gateway
        if gateway is None or not self.connected:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_connected"
            )
        sender = BaseAddress(int(record.sender_id, 16))
        if not gateway.is_valid_sender(sender):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_sender",
                translation_placeholders={"sender_id": record.sender_id},
            )
        result = await gateway.send_command(
            EURID(int(record.address, 16)),
            SetSwitchOutput(
                output_value=100 if turn_on else 0,
                entity_id=str(record.channel),
            ),
            sender=sender,
        )
        response = result.response
        return response is not None and response.return_code == ResponseCode.OK
