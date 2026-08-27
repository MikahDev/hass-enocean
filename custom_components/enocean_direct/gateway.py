"""Serial transport owner. The only module that imports enocean_async.

One EnOceanHub instance owns the serial descriptor for one config entry.
Every lifecycle path (unload, reload, failed setup, USB disconnect, HA stop)
funnels through async_stop(), which closes the transport deterministically.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
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
from enocean_async.eep import EEP_SPECIFICATIONS
from enocean_async.eep.handler import EEPHandler
from enocean_async.eep.message import EEPMessageType, RawEEPMessage
from enocean_async.gateway import BaseIDChangeError
from enocean_async.protocol.erp1.fourbs import FourBSTeachInTelegram
from enocean_async.protocol.erp1.rorg import RORG
from enocean_async.protocol.erp1.ute import UTEMessage
from enocean_async.protocol.esp3.common_command import CommonCommandTelegram
from enocean_async.protocol.esp3.response import ResponseCode
from enocean_async.semantics.entity import EntityCategory, EntityType
from enocean_async.semantics.instruction import Instruction
from enocean_async.semantics.instructions.cover import (
    CoverClose,
    CoverOpen,
    CoverQueryPositionAndAngle,
    CoverSetPositionAndAngle,
    CoverStop,
)
from enocean_async.semantics.instructions.fan import SetFanSpeed
from enocean_async.semantics.instructions.switch import (
    QueryActuatorMeasurement,
    QueryActuatorStatus,
    SetSwitchOutput,
)
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
    CONF_REPEATER,
    DOMAIN,
    EEP_BATTERY_FLAG,
    EEP_CHANNEL_COUNT,
    EEP_CONTACT,
    EEP_METERING,
    EEP_ROCKERS,
    EURID_MAX,
    EVENT_BUTTON,
    ISSUE_SERIAL_DISCONNECTED,
    KEY_ADDRESS,
    KEY_EEP,
    PAIRING_TIMEOUT,
    ROCKER_DOUBLE_PRESS_SECONDS,
    ROCKER_HOLD_SECONDS,
    SENDER_OFFSET_MAX,
    SIGNAL_BATTERY,
    SIGNAL_CONNECTION,
    SIGNAL_CONTACT,
    SIGNAL_COVER_STATE,
    SIGNAL_FAN,
    SIGNAL_METERING,
    SIGNAL_SENSOR,
    SIGNAL_SWITCH_STATE,
    SIGNAL_TELEGRAM,
    SUPPORTED_EEPS,
)
from .inbox import RadioInbox
from .models import DeviceRecord, record_from_dict
from .profiles import (
    decode_a5_10_battery,
    decode_d2_01_measurement,
    decode_d5,
    decode_f6,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class PairingError(Exception):
    """Guided pairing failed; reason is a translation key for the flow."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BaseIDError(Exception):
    """Base ID write failed; reason is a translation key for the flow."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ESP3 common command CO_WR_REPEATER (spec 1.10.9): REP_ENABLE, REP_LEVEL.
CO_WR_REPEATER = 0x09
_REPEATER_BYTES = {
    "off": (0x00, 0x00),
    "level_1": (0x01, 0x01),
    "level_2": (0x01, 0x02),
}


def _base_id_reason(message: str) -> str:
    """Map the library's BaseIDChangeError to a flow error key. The exception
    carries no code, only wording; pinned to enocean-async 0.16.0 and covered
    by tests for every branch, so a wording change fails loudly there."""
    lowered = message.lower()
    if "not supported" in lowered:
        return "base_id_not_supported"
    if "out of allowed range" in lowered:
        return "base_id_out_of_range"
    if "maximum number" in lowered:
        return "base_id_max_reached"
    if "still the same" in lowered:
        return "base_id_not_written"
    if "different base id" in lowered:
        return "base_id_mismatch_after_write"
    return "base_id_failed"


@dataclass
class _RockerGesture:
    """Per-rocker press tracking for the synthesised held / released-after-hold
    / double-pressed events. The F6 release telegram carries no button, so the
    button of the press currently down is remembered here."""

    down: bool = False
    button: str | None = None
    hold_timer: asyncio.TimerHandle | None = None
    held: bool = False
    # True while the press that completed a double press is down, so its
    # release does not seed yet another double (three taps = one double).
    in_double: bool = False
    # (button, release time) of the last short press, for double-press detection
    last_short_release: tuple[str | None, datetime] | None = None

    def cancel_hold(self) -> None:
        if self.hold_timer is not None:
            self.hold_timer.cancel()
            self.hold_timer = None


def sensor_entities_for_eep(eep: str) -> list[tuple[str, str, bool]]:
    """List the (entity_id, observable, is_binary) triples a sensor profile
    reports, from the library's own EEP specification. Keeps enocean_async
    types out of the platform modules."""
    spec = EEP_SPECIFICATIONS.get(EEP(eep))
    if spec is None:
        return []
    out: list[tuple[str, str, bool]] = []
    for entity in spec.entities:
        if entity.category is not EntityCategory.DEFAULT:
            continue
        entity_type = entity.entity_type
        if entity_type not in (EntityType.SENSOR, EntityType.BINARY):
            continue
        for observable in sorted(entity.observables):
            out.append((entity.id, observable.value, entity_type is EntityType.BINARY))
    return out


# Library cover states as seen from an inverted (backwards-wired) unit.
_INVERTED_COVER_STATE = {
    "open": "closed",
    "closed": "open",
    "opening": "closing",
    "closing": "opening",
}


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
        # The inbox lives in hass.data so it survives entry reloads (adding a
        # device reloads the entry). Still in-memory only, per design.
        domain_data = hass.data.setdefault(DOMAIN, {})
        self.inbox: RadioInbox = domain_data.setdefault(
            f"{entry.entry_id}_inbox", RadioInbox()
        )
        # Sync both ways: a device added earlier disappears from the inbox's
        # unconfigured list, and a removed one becomes addable again without
        # waiting for its next telegram.
        self.inbox.sync_configured(set(self.devices))
        self.gateway: Gateway | None = None
        self.connected = False
        self.base_id: str | None = entry.data.get(CONF_BASE_ID)
        self._stopped = False
        # Pairing waiters keyed by address; "*" is an unfocused menu-pairing
        # window that accepts the first device heard.
        self._pairing: dict[str, asyncio.Future[tuple[str, str]]] = {}
        self._gestures: dict[str, _RockerGesture] = {}

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

        # Register transmitting devices (D2-01 switches, D2-05 covers) and
        # receive-only sensor profiles with the library so their telegrams are
        # decoded. Contacts and rockers are decoded locally.
        for record in self.devices.values():
            if record.eep not in EEP_CHANNEL_COUNT and record.kind != "sensor":
                continue
            sender = (
                BaseAddress(int(record.sender_id, 16))
                if record.sender_id is not None
                else None
            )
            try:
                gateway.add_device(
                    address=EURID(int(record.address, 16)),
                    device_type=device_type_for_eep(EEP(record.eep)),
                    sender=sender,
                    name=record.name,
                )
            except ValueError as err:
                _LOGGER.error("Could not register device %s: %s", record.address, err)

        if (mode := self.entry.options.get(CONF_REPEATER)) is not None:
            await self._async_apply_repeater(mode)

    @property
    def base_id_remaining_write_cycles(self) -> int | None:
        """Lifetime CO_WR_IDBASE writes the module reports as still available."""
        return (
            self.gateway.base_id_remaining_write_cycles
            if self.gateway is not None
            else None
        )

    async def async_stop(self) -> None:
        """Close the serial connection. Safe to call more than once."""
        self._stopped = True
        for gesture in self._gestures.values():
            gesture.cancel_hold()
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
            # user-initiated pairing window.
            try:
                ute = UTEMessage.from_erp1(erp1)
                declared_eep = (
                    f"{ute.eep.rorg:02X}-{ute.eep.func:02X}-{ute.eep.type:02X}"
                )
            except ValueError, IndexError:
                declared_eep = None
        elif erp1.rorg == RORG.RORG_4BS and erp1.is_learning_telegram:
            # 4BS teach-ins can declare an A5 EEP (LRN type bit set). Parsed
            # for inbox display and discovery; never answered outside a
            # pairing window, and receive-only profiles need no answer at all.
            try:
                teach_in = FourBSTeachInTelegram.from_erp1(erp1)
            except ValueError:
                declared_eep = None
            else:
                if teach_in.eep is not None:
                    declared_eep = (
                        f"{teach_in.eep.rorg:02X}-{teach_in.eep.func:02X}-"
                        f"{teach_in.eep.type:02X}"
                    )
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
            # During an unfocused pairing window, a teach-in from a
            # receive-only profile resolves the wait directly so the user gets
            # the "no pairing needed" explanation instead of a timeout (the
            # library answers UTE teach-ins itself but ignores plain 4BS ones).
            waiter = self._pairing.get("*")
            if (
                waiter is not None
                and not waiter.done()
                and declared_eep in SUPPORTED_EEPS
                and declared_eep not in EEP_CHANNEL_COUNT
                and int(address, 16) <= EURID_MAX
            ):
                waiter.set_result((address, declared_eep))
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

        if record.eep in EEP_METERING and erp1.rorg == RORG.RORG_VLD:
            # CMD 0x7 measurement responses are decoded locally: the wire unit
            # (Ws/Wh/kWh/W/kW) varies per device and the library's observation
            # drops it, so values are normalised to Wh and W here.
            measurement = decode_d2_01_measurement(bytes(erp1.telegram_data))
            if measurement is not None:
                async_dispatcher_send(
                    self.hass,
                    SIGNAL_METERING.format(address),
                    measurement.channel,
                    measurement.energy_wh,
                    measurement.power_w,
                )

        if record.eep in EEP_BATTERY_FLAG and erp1.rorg == RORG.RORG_4BS:
            # The BATT flag rides every data telegram; the panel's other
            # values reach HA through the library's observations.
            is_low = decode_a5_10_battery(bytes(erp1.telegram_data))
            if is_low is not None:
                async_dispatcher_send(self.hass, SIGNAL_BATTERY.format(address), is_low)

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
                self._fire_button_event(
                    record, action.action, action.button, action.second_button
                )
                self._track_gesture(record, action.action, action.button)

    def _fire_button_event(
        self,
        record: DeviceRecord,
        event_type: str,
        button: str | None,
        second_button: str | None = None,
    ) -> None:
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, record.address)})
        self.hass.bus.async_fire(
            EVENT_BUTTON,
            {
                "device_id": device.id if device else None,
                "address": record.address,
                "type": event_type,
                "button": button,
                "second_button": second_button,
            },
        )

    def _track_gesture(
        self, record: DeviceRecord, event_type: str, button: str | None
    ) -> None:
        """Synthesise held / released_after_hold / double_pressed from the
        press and release telegrams the rocker sends anyway. The plain
        pressed/released events above are untouched."""
        gesture = self._gestures.setdefault(record.address, _RockerGesture())
        now = dt_util.utcnow()
        if event_type == "pressed":
            # A press while another is still down means its release was never
            # heard: drop the stale hold timer and start over.
            gesture.cancel_hold()
            last = gesture.last_short_release
            gesture.last_short_release = None
            gesture.in_double = (
                last is not None
                and last[0] == button
                and (now - last[1]).total_seconds() <= ROCKER_DOUBLE_PRESS_SECONDS
            )
            if gesture.in_double:
                self._fire_button_event(record, "double_pressed", button)
            gesture.down = True
            gesture.button = button
            gesture.held = False
            gesture.hold_timer = self.hass.loop.call_later(
                ROCKER_HOLD_SECONDS, self._on_hold, record
            )
            return
        # released: the telegram is generic, the tracked press tells which button
        gesture.cancel_hold()
        if gesture.down:
            if gesture.held:
                self._fire_button_event(record, "released_after_hold", gesture.button)
            elif not gesture.in_double:
                gesture.last_short_release = (gesture.button, now)
        gesture.down = False
        gesture.held = False
        gesture.in_double = False

    @callback
    def _on_hold(self, record: DeviceRecord) -> None:
        gesture = self._gestures.get(record.address)
        if self._stopped or gesture is None or not gesture.down:
            return
        gesture.hold_timer = None
        gesture.held = True
        self._fire_button_event(record, "held", gesture.button)

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
            # 100 = open, so convert here. 101-127 mean "unknown". A record
            # marked invert (unit wired backwards) mirrors the conversion and
            # the derived movement direction, matching the swapped TX below.
            address = f"{int(observation.device):08X}"
            record = self.devices.get(address)
            inverted = record is not None and record.invert
            raw_pos = values.get(Observable.POSITION)
            position = None
            if isinstance(raw_pos, (int, float)) and 0 <= raw_pos <= 100:
                position = int(raw_pos) if inverted else 100 - int(raw_pos)
            cover_state = values.get(Observable.COVER_STATE)
            if raw_pos is not None and position is None:
                # "Unknown" position (101..127): the library still derives a
                # direction by comparing it with the previous position, which
                # is meaningless. Watchdog "stopped" observations carry no
                # POSITION at all and still pass through.
                cover_state = None
            if inverted and cover_state is not None:
                cover_state = _INVERTED_COVER_STATE.get(cover_state, cover_state)
            async_dispatcher_send(
                self.hass,
                SIGNAL_COVER_STATE.format(address),
                position,
                cover_state,
            )
            return
        if observation.entity == "fan" and Observable.FAN_SPEED in values:
            # D2-20-02 status: numeric percent only (the library drops the
            # Auto/Default sentinels). A5-10 panels use entity id "fan_speed"
            # with label strings and take the sensor path below instead.
            address = f"{int(observation.device):08X}"
            async_dispatcher_send(
                self.hass,
                SIGNAL_FAN.format(address),
                int(values[Observable.FAN_SPEED]),
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
            return
        address = f"{int(observation.device):08X}"
        record = self.devices.get(address)
        if record is not None and record.kind == "sensor":
            # Decoded profile values for receive-only sensors. Metadata
            # observations (rssi, last_seen, telegram_count) also pass through
            # here; no profile entity subscribes to those ids, so they are
            # simply ignored by every listener.
            async_dispatcher_send(
                self.hass,
                SIGNAL_SENSOR.format(address),
                observation.entity,
                {obs.value: value for obs, value in values.items()},
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
            self.entry.async_create_background_task(
                self.hass, self._async_on_reconnect(), "enocean_direct_reconnect"
            )
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
        # An inverted record (unit wired backwards) sends the opposite command.
        return await self._async_send(
            record, CoverClose() if record.invert else CoverOpen()
        )

    async def async_close_cover(self, record: DeviceRecord) -> bool:
        return await self._async_send(
            record, CoverOpen() if record.invert else CoverClose()
        )

    async def async_stop_cover(self, record: DeviceRecord) -> bool:
        return await self._async_send(record, CoverStop())

    async def async_set_cover_position(
        self, record: DeviceRecord, ha_position: int
    ) -> bool:
        """Move to an HA position (100 = open); D2-05 uses 0 = open, so the
        value is mirrored unless the record is inverted."""
        position = ha_position if record.invert else 100 - ha_position
        return await self._async_send(
            record, CoverSetPositionAndAngle(position=position, angle=None)
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

    async def async_set_fan_speed(self, record: DeviceRecord, fan_speed: int) -> bool:
        """Send a D2-20-02 fan control message. fan_speed: 0-100 percent,
        253 = Auto, 254 = device default. Room size fields stay 'no change'."""
        return await self._async_send(record, SetFanSpeed(fan_speed=fan_speed))

    async def async_query_startup_states(self) -> None:
        """Opt-in (CONF_QUERY_STARTUP): one status query per switch and cover
        so entities confirm right after a restart instead of on their first
        command. Failures are logged, never fatal: a dead actuator must not
        take the entry down."""
        for record in self.devices.values():
            try:
                if record.kind == "actuator":
                    await self._async_send(record, QueryActuatorStatus())
                elif record.kind == "cover":
                    await self._async_send(record, CoverQueryPositionAndAngle())
            except HomeAssistantError as err:
                _LOGGER.warning(
                    "Startup status query for %s failed: %s", record.address, err
                )

    async def async_query_measurement(self, record: DeviceRecord) -> bool:
        """Send D2-01 CMD 0x6 queries for energy and power. Returns True only
        if the transceiver acknowledged both."""
        energy_ok = await self._async_send(
            record, QueryActuatorMeasurement(query_power=False)
        )
        power_ok = await self._async_send(
            record, QueryActuatorMeasurement(query_power=True)
        )
        return energy_ok and power_ok

    async def async_set_local(
        self,
        record: DeviceRecord,
        *,
        taught_in_enabled: bool,
        overcurrent_restart: bool,
        local_control: bool,
        power_failure_detection: bool,
        default_state: int,
    ) -> bool:
        """Send a D2-01 CMD 0x2 Actuator Set Local. The telegram writes every
        local parameter at once, so all consequential fields are explicit
        arguments; the dim timers and day/night flag have no effect on the
        supported relay type and are sent as their 'not used' values. Encoded
        through the library's own EEP specification. Returns True on RET_OK."""
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
        message = RawEEPMessage(
            sender=sender,
            destination=EURID(int(record.address, 16)),
            message_type=EEPMessageType(id=2, description="Actuator set local"),
            raw={
                "d/e": 1 if taught_in_enabled else 0,
                "OC": 1 if overcurrent_restart else 0,
                "RO": 0,  # over-current trigger signal: not exposed
                "LC": 1 if local_control else 0,
                "I/O": record.channel,
                "DT2": 0,
                "DT3": 0,
                "d/n": 0,
                "PF": 1 if power_failure_detection else 0,
                "DS": default_state,
                "DT1": 0,
            },
        )
        erp1 = EEPHandler(EEP_SPECIFICATIONS[EEP(record.eep)]).encode(message)
        result = await gateway.send_esp3_packet(erp1.to_esp3())
        response = result.response
        return response is not None and response.return_code == ResponseCode.OK

    # ------------------------------------------------------------------
    # transceiver management (ESP3 common commands: local module writes,
    # nothing is transmitted over the air)
    # ------------------------------------------------------------------
    async def async_set_repeater(self, mode: str) -> bool:
        """Send CO_WR_REPEATER. Returns True if the module answered RET_OK."""
        gateway = self.gateway
        if gateway is None or not self.connected:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_connected"
            )
        enable, level = _REPEATER_BYTES[mode]
        command = CommonCommandTelegram(
            common_command_code=CO_WR_REPEATER,
            common_command_data=bytes([enable, level]),
        )
        result = await gateway.send_esp3_packet(command.to_esp3_packet())
        response = result.response
        if response is None:
            _LOGGER.warning("Repeater mode %s: no response from the module", mode)
            return False
        if response.return_code != ResponseCode.OK:
            _LOGGER.warning(
                "Repeater mode %s rejected by the module: %s",
                mode,
                response.return_code.name,
            )
            return False
        return True

    async def _async_read_module_base_id(self) -> str | None:
        """Read the Base ID straight from the module (CO_RD_IDBASE).

        The library caches the Base ID for the lifetime of its Gateway object
        and does not re-read it when it reconnects, so asking the module is the
        only way to notice that a different transceiver is on the port now.
        """
        gateway = self.gateway
        if gateway is None:
            return None
        result = await gateway.send_esp3_packet(
            CommonCommandTelegram.CO_RD_IDBASE().to_esp3_packet()
        )
        response = result.response
        if (
            response is None
            or response.return_code != ResponseCode.OK
            or len(response.response_data) < 4
        ):
            _LOGGER.debug("Base ID re-read after reconnect got no usable answer")
            return None
        return f"{int.from_bytes(response.response_data[:4], 'big'):08X}"

    async def _async_on_reconnect(self) -> None:
        """After the library reconnects, check we still have the same
        transceiver, then re-apply the settings the module loses on power."""
        if self._stopped:
            return
        base_id = await self._async_read_module_base_id()
        if base_id is not None and base_id != self.base_id:
            # A replacement stick on the same port. Every cached value the
            # library holds (Base ID, remaining write cycles, chip version)
            # belongs to the old one, so restart the entry: a fresh Gateway
            # reads the real ones, and the Base ID recovery step can then
            # restore the exported Base ID onto this module.
            _LOGGER.warning(
                "Transceiver on %s now reports Base ID %s instead of %s; "
                "reloading the entry",
                self.port,
                base_id,
                self.base_id,
            )
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, CONF_BASE_ID: base_id}
            )
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)
            return
        if (mode := self.entry.options.get(CONF_REPEATER)) is not None:
            await self._async_apply_repeater(mode)

    async def _async_apply_repeater(self, mode: str) -> None:
        """Re-apply the stored repeater mode; never fatal for the entry."""
        if self._stopped:
            return
        if mode not in _REPEATER_BYTES:
            # Only reachable from a hand-edited entry or a renamed mode; a bad
            # option value must not stop the gateway from loading.
            _LOGGER.warning("Unknown repeater mode %r in options; not applied", mode)
            return
        try:
            await self.async_set_repeater(mode)
        except HomeAssistantError as err:
            _LOGGER.warning("Repeater mode %s not applied: %s", mode, err)

    async def async_change_base_id(self, new_base_id: str) -> None:
        """Write a new Base ID (CO_WR_IDBASE) through the library, which
        re-reads it afterwards to prove the write took. Burns one of the
        module's lifetime write cycles; the flow confirms twice before this."""
        gateway = self.gateway
        if gateway is None or not self.connected:
            raise BaseIDError("not_connected")
        try:
            await gateway.change_base_id(
                BaseAddress(int(new_base_id, 16)), safety_flag=0x7B
            )
        except BaseIDChangeError as err:
            # The reason keys below are user-facing; the module's own wording
            # (and error code) only exists here, so log it before mapping.
            _LOGGER.warning("Base ID change failed: %s", err)
            raise BaseIDError(_base_id_reason(str(err))) from err
        except ValueError as err:
            # same as the current Base ID (the form checks this first)
            raise BaseIDError("base_id_unchanged") from err
        except ConnectionError as err:
            raise BaseIDError("not_connected") from err
        self.base_id = new_base_id

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
        if eep not in EEP_CHANNEL_COUNT:
            # Receive-only profile: it needs no sender and no pairing. Add it
            # from its discovery card or the radio inbox instead.
            raise PairingError("pair_receive_only")
        return taught_address, eep, sender_id

    @callback
    def _on_taught_in(self, address: Any, eep: Any) -> None:
        address_hex = f"{int(address):08X}"
        future = self._pairing.get(address_hex) or self._pairing.get("*")
        if future is not None and not future.done():
            future.set_result(
                (address_hex, f"{eep.rorg:02X}-{eep.func:02X}-{eep.type:02X}")
            )
