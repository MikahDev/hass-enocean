"""Test harness: a fake USB300 dongle behind serialx.

The real enocean_async Gateway and ESP3 parser run unmodified; only
serialx.create_serial_connection is replaced, so every test exercises the
genuine framing, CRC, dedup and send/response paths against scripted bytes.
No test ever opens a real serial device.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_direct.const import (
    CONF_BASE_ID,
    CONF_DEVICE_PATH,
    CONF_DEVICES,
    DOMAIN,
)

BASE_ID = 0xFF974100
BASE_ID_HEX = "FF974100"
CHIP_EURID = 0x0102A1B2
PORT = "/dev/serial/by-id/usb-FTDI_TEST-if00-port0"

CONTACT = {"address": "0084ACF3", "eep": "D5-00-01", "name": "Test contact"}
ROCKER = {"address": "002909BE", "eep": "F6-02-01", "name": "Test rocker"}
ACTUATOR = {
    "address": "050A5C20",
    "eep": "D2-01-0F",
    "name": "Pilot relay",
    "sender_id": BASE_ID_HEX,
    "channel": 0,
}


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in all tests."""
    return


def _response_frame(response_data: bytes, optional: bytes = b"") -> bytes:
    from enocean_async.protocol.esp3.packet import ESP3Packet, ESP3PacketType

    return ESP3Packet(ESP3PacketType.RESPONSE, response_data, optional).to_bytes()


class FakeTransport:
    """Stands in for a serialx.SerialTransport."""

    def __init__(self, dongle: FakeDongle, protocol: Any) -> None:
        self._dongle = dongle
        self._protocol = protocol
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.closed:
            raise OSError("write to closed transport")
        self._dongle.handle_host_frame(self, data)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._protocol.connection_lost(None)


class FakeDongle:
    """Scriptable USB300: answers common commands and records radio sends."""

    def __init__(self) -> None:
        self.transport: FakeTransport | None = None
        self.protocol: Any = None
        self.transports: list[FakeTransport] = []
        self.connect_count = 0
        self.fail_connect = False
        self.respond_to_common = True
        self.respond_to_radio = True
        self.radio_return_code = 0x00  # RET_OK
        self.sent_radio: list[tuple[bytes, bytes]] = []  # (data, optional)

    async def create(self, loop, protocol_factory, port, baudrate=57600, **kwargs):
        if self.fail_connect:
            raise OSError(f"could not open port {port}")
        self.connect_count += 1
        protocol = protocol_factory()
        transport = FakeTransport(self, protocol)
        self.transports.append(transport)
        self.transport = transport
        self.protocol = protocol
        protocol.connection_made(transport)
        return transport, protocol

    # -- host -> module ------------------------------------------------
    def handle_host_frame(self, transport: FakeTransport, frame: bytes) -> None:
        assert frame[0] == 0x55
        data_len = (frame[1] << 8) | frame[2]
        opt_len = frame[3]
        packet_type = frame[4]
        payload = frame[6 : 6 + data_len]
        optional = frame[6 + data_len : 6 + data_len + opt_len]

        if packet_type == 0x05:  # COMMON_COMMAND
            if not self.respond_to_common:
                return
            if payload[0] == 0x08:  # CO_RD_IDBASE
                self.reply(bytes([0x00]) + BASE_ID.to_bytes(4, "big"), b"\x0a")
            elif payload[0] == 0x03:  # CO_RD_VERSION
                data = (
                    bytes([0x00])  # RET_OK
                    + bytes([1, 0, 0, 0])  # app version
                    + bytes([1, 0, 0, 0])  # api version
                    + CHIP_EURID.to_bytes(4, "big")
                    + bytes([1])  # device version
                    + bytes(3)
                    + b"FAKE USB 300".ljust(16, b"\x00")
                )
                self.reply(data)
        elif packet_type == 0x01:  # RADIO_ERP1
            self.sent_radio.append((bytes(payload), bytes(optional)))
            if self.respond_to_radio:
                self.reply(bytes([self.radio_return_code]))

    def reply(self, response_data: bytes, optional: bytes = b"") -> None:
        self.protocol.data_received(_response_frame(response_data, optional))

    # -- module -> host ------------------------------------------------
    def inject(self, frame: bytes) -> None:
        self.protocol.data_received(frame)

    def unplug(self) -> None:
        """Simulate the USB stick disappearing."""
        transport = self.transport
        assert transport is not None
        transport.closed = True
        self.protocol.connection_lost(OSError("device disconnected"))


@pytest.fixture
def dongle(monkeypatch: pytest.MonkeyPatch) -> FakeDongle:
    import enocean_async.gateway as gateway_module

    fake = FakeDongle()
    monkeypatch.setattr(
        gateway_module.serialx, "create_serial_connection", fake.create
    )
    return fake


# ----------------------------------------------------------------------
# telegram builders (RX fixtures)
# ----------------------------------------------------------------------
def erp1_frame(
    rorg: int,
    payload: list[int] | bytes,
    sender: int,
    status: int = 0x00,
    rssi: int = 45,
    destination: int | None = None,
) -> bytes:
    from enocean_async.address import BaseAddress, BroadcastAddress, EURID
    from enocean_async.protocol.erp1.rorg import RORG
    from enocean_async.protocol.erp1.telegram import ERP1Telegram

    sender_address = (
        EURID(sender) if sender <= 0xFF7FFFFF else BaseAddress(sender)
    )
    telegram = ERP1Telegram(
        rorg=RORG(rorg),
        telegram_data=bytes(payload),
        sender=sender_address,
        status=status,
        rssi=rssi,
        destination=(
            BroadcastAddress() if destination is None else EURID(destination)
        ),
    )
    return telegram.to_esp3().to_bytes()


def d5_frame(sender: int, closed: bool, teach_in: bool = False, **kwargs) -> bytes:
    db0 = 0x00 if teach_in else (0x09 if closed else 0x08)
    return erp1_frame(0xD5, [db0], sender, **kwargs)


def f6_frame(sender: int, db0: int, status: int = 0x30, **kwargs) -> bytes:
    """status 0x30 = T21|NU (press), 0x20 = T21 (release)."""
    return erp1_frame(0xF6, [db0], sender, status=status, **kwargs)


def d2_status_frame(sender: int, channel: int, output_value: int, **kwargs) -> bytes:
    """D2-01 CMD 0x4 Actuator Status Response."""
    payload = [0x04, channel & 0x1F, output_value & 0x7F]
    return erp1_frame(0xD2, payload, sender, **kwargs)


# ----------------------------------------------------------------------
# entry helpers
# ----------------------------------------------------------------------
def make_entry(hass, devices: list[dict] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EnOcean gateway FF974100",
        data={CONF_DEVICE_PATH: PORT, CONF_BASE_ID: BASE_ID_HEX},
        options={CONF_DEVICES: devices or []},
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(hass, entry: MockConfigEntry) -> bool:
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return result


async def flush(hass) -> None:
    """Let loop.call_soon-scheduled library observations propagate."""
    for _ in range(3):
        await asyncio.sleep(0)
    await hass.async_block_till_done()
