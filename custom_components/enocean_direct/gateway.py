"""Serial transport owner. The only module that imports enocean_async.

One EnOceanHub instance owns the serial descriptor for one config entry.
Every lifecycle path (unload, reload, failed setup, USB disconnect, HA stop)
funnels through async_stop(), which closes the transport deterministically.
"""

from __future__ import annotations

import asyncio
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
from enocean_async.protocol.erp1.ute import UTEMessage
from enocean_async.protocol.esp3.response import ResponseCode
from enocean_async.semantics.instruction import Instruction
from enocean_async.semantics.instructions.cover import (
    CoverClose,
    CoverOpen,
    CoverSetPositionAndAngle,
    CoverStop,
)
from enocean_async.semantics.instructions.switch import SetSwitchOutput
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import discovery_flow
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_ID,
    CONF_DEVICE_PATH,
    CONF_DEVICES,
    DOMAIN,
    EEP_CHANNEL_COUNT,
    EEP_CONTACT,
    EEP_ROCKERS,
    EURID_MAX,
    EVENT_BUTTON,
    ISSUE_SERIAL_DISCONNECTED,
    KEY_ADDRESS,
    KEY_EEP,
    PAIRING_TIMEOUT,
    SENDER_OFFSET_MAX,
    SIGNAL_CONNECTION,
    SIGNAL_CONTACT,
    SIGNAL_COVER_STATE,
    SIGNAL_SWITCH_STATE,
    SIGNAL_TELEGRAM,
    SUPPORTED_EEPS,
)
from .inbox import RadioInbox
from .models import DeviceRecord, record_from_dict
from .profiles import decode_d5, decode_f6

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class PairingError(Exception):
    """Guided pairing failed; reason is a translation key for the flow."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
        # Pairing waiters keyed by address; "*" is an unfocused menu-pairing
        # window that accepts the first device heard.
        self._pairing: dict[str, asyncio.Future[tuple[str, str]]] = {}

    async def async_start(self) -> None:
        """Open the serial port and register configured devices.

        Raises ConnectionError if the port cannot be opened or the module
        does not answer; enocean_async closes the descriptor itself in that
        case, so there is nothing to release here.
        """
        gateway = Gateway(self.port)
        gateway.add_erp1_received_callback(self._on_erp1)
        gateway.add_observation_callback(self._on_observation)
        gateway.add_device_taught_in_callback(self._on_taught_in)
        await gateway.start(auto_reconnect=True)

        self.gateway = gateway
        self.connected = True
        base = gateway.base_id
        if base is not None:
            self.base_id = f"{int(base):08X}"

        # Register transmitting devices (D2-01 switches, D2-05 covers) with the
        # library so their status telegrams are decoded. Contacts and rockers
        # are decoded locally.
        for record in self.devices.values():
            if record.eep not in EEP_CHANNEL_COUNT:
                continue
            try:
                gateway.add_device(
                    address=EURID(int(record.address, 16)),
                    device_type=device_type_for_eep(EEP(record.eep)),
                    sender=BaseAddress(int(record.sender_id, 16)),
                    name=record.name,
                )
            except ValueError as err:
                _LOGGER.error("Could not register actuator %s: %s", record.address, err)

    async def async_stop(self) -> None:
        """Close the serial connection. Safe to call more than once."""
        self._stopped = True
        if self.gateway is not None:
            await self.gateway.stop()
            self.gateway = None
        self.connected = False
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_SERIAL_DISCONNECTED)

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
        elif erp1.rorg == RORG.RORG_UTE:
            # A UTE teach-in explicitly declares the device's EEP. Parsed for
            # inbox display; the library only acknowledges it inside a
            # user-initiated pairing window focused on that one device.
            try:
                ute = UTEMessage.from_erp1(erp1)
                declared_eep = (
                    f"{ute.eep.rorg:02X}-{ute.eep.func:02X}-{ute.eep.type:02X}"
                )
            except ValueError, IndexError:
                declared_eep = None
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
            # A teach-in is a deliberate user action: surface a native
            # discovery card. Data telegrams stay in the inbox so foreign
            # devices don't spam discovery. Base-range senders are other
            # controllers, not devices. While an unfocused pairing window is
            # open, the teach-in belongs to that flow, not to a new card.
            if (
                declared_eep in SUPPORTED_EEPS
                and int(address, 16) <= EURID_MAX
                and "*" not in self._pairing
            ):
                discovery_flow.async_create_flow(
                    self.hass,
                    DOMAIN,
                    context={"source": SOURCE_INTEGRATION_DISCOVERY},
                    data={KEY_ADDRESS: address, KEY_EEP: declared_eep},
                )
            return

        # Diagnostics: every telegram from a configured device counts, whatever
        # its RORG. This single path covers locally decoded devices (contacts,
        # rockers) and library-registered ones (actuators, covers) alike, since
        # all incoming ERP1 telegrams pass through here before EEP decoding.
        async_dispatcher_send(
            self.hass, SIGNAL_TELEGRAM.format(address), rssi, dt_util.utcnow()
        )

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
        if observation.entity == "cover" and (
            Observable.POSITION in values or Observable.COVER_STATE in values
        ):
            # EnOcean D2-05 position: 0 = open, 100 = closed. HA covers use
            # 100 = open, so invert here. 101-127 mean "unknown".
            raw_pos = values.get(Observable.POSITION)
            position = (
                100 - int(raw_pos)
                if isinstance(raw_pos, (int, float)) and 0 <= raw_pos <= 100
                else None
            )
            address = f"{int(observation.device):08X}"
            async_dispatcher_send(
                self.hass,
                SIGNAL_COVER_STATE.format(address),
                position,
                values.get(Observable.COVER_STATE),
            )
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
    # outgoing (bounded to configured D2-01 switch and D2-05 cover entities)
    # ------------------------------------------------------------------
    async def async_set_output(self, record: DeviceRecord, turn_on: bool) -> bool:
        """Send a D2-01 CMD 0x1 Actuator Set Output."""
        return await self._async_send(
            record, SetSwitchOutput(output_value=100 if turn_on else 0)
        )

    async def async_open_cover(self, record: DeviceRecord) -> bool:
        return await self._async_send(record, CoverOpen())

    async def async_close_cover(self, record: DeviceRecord) -> bool:
        return await self._async_send(record, CoverClose())

    async def async_stop_cover(self, record: DeviceRecord) -> bool:
        return await self._async_send(record, CoverStop())

    async def async_set_cover_position(
        self, record: DeviceRecord, ha_position: int
    ) -> bool:
        """Move to an HA position (100 = open); D2-05 uses 0 = open."""
        return await self._async_send(
            record, CoverSetPositionAndAngle(position=100 - ha_position, angle=None)
        )

    async def _async_send(self, record: DeviceRecord, command: Instruction) -> bool:
        """Send one typed command to a configured device. Returns True if the
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
        command.entity_id = str(record.channel)
        try:
            result = await gateway.send_command(
                EURID(int(record.address, 16)),
                command,
                sender=sender,
            )
        except ValueError as err:
            # e.g. address invalid or device not registered with the library
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="send_failed",
                translation_placeholders={
                    "address": record.address,
                    "error": str(err),
                },
            ) from err
        response = result.response
        return response is not None and response.return_code == ResponseCode.OK

    # ------------------------------------------------------------------
    # guided pairing (one device, one time-bounded learning window)
    # ------------------------------------------------------------------
    def allocate_sender(self) -> str:
        """Return the first free Base ID+offset sender (offset 1..127).

        Offset 0 stays the proposed default for manual configuration, so
        guided pairing always hands out a sender unique to the device.
        """
        if self.base_id is None:
            raise PairingError("not_connected")
        base = int(self.base_id, 16)
        used = {
            int(record.sender_id, 16) - base
            for record in self.devices.values()
            if record.sender_id is not None
        }
        offset = next(
            (o for o in range(1, SENDER_OFFSET_MAX + 1) if o not in used), None
        )
        if offset is None:
            raise PairingError("pair_no_free_sender")
        return f"{base + offset:08X}"

    async def async_pair(self, address: str | None = None) -> tuple[str, str, str]:
        """Open a pairing window and answer the next UTE teach-in with a
        freshly allocated sender ID. Focused on one device when address is
        given (discovery card), otherwise the first device heard (menu
        pairing). Returns (address, eep, sender_id); raises PairingError on
        timeout, exhausted sender pool, or an unsupported profile."""
        gateway = self.gateway
        if gateway is None or not self.connected:
            raise PairingError("not_connected")
        sender_id = self.allocate_sender()
        key = address or "*"
        future: asyncio.Future[tuple[str, str]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pairing[key] = future
        await gateway.start_learning(
            timeout=PAIRING_TIMEOUT,
            sender_id=BaseAddress(int(sender_id, 16)),
            for_device=EURID(int(address, 16)) if address is not None else None,
        )
        try:
            async with asyncio.timeout(PAIRING_TIMEOUT):
                taught_address, eep = await future
        except TimeoutError:
            raise PairingError("pair_timeout") from None
        finally:
            self._pairing.pop(key, None)
            if self.gateway is not None:
                self.gateway.stop_learning()
        if eep not in SUPPORTED_EEPS:
            # The library already answered the teach-in (it knows more EEPs
            # than this integration exposes); nothing is stored though.
            _LOGGER.warning(
                "Pairing: device %s declared unsupported EEP %s", taught_address, eep
            )
            raise PairingError("pair_unsupported_eep")
        return taught_address, eep, sender_id

    @callback
    def _on_taught_in(self, address: Any, eep: Any) -> None:
        address_hex = f"{int(address):08X}"
        future = self._pairing.get(address_hex) or self._pairing.get("*")
        if future is not None and not future.done():
            future.set_result(
                (address_hex, f"{eep.rorg:02X}-{eep.func:02X}-{eep.type:02X}")
            )
