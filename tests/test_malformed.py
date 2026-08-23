"""Corrupted, truncated and alien byte streams must never break the reader."""

from homeassistant.core import HomeAssistant

from .conftest import CONTACT, FakeDongle, d5_frame, make_entry, setup_entry

CONTACT_INT = int(CONTACT["address"], 16)
ENTITY = "binary_sensor.test_contact"


async def _setup(hass: HomeAssistant) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)


async def _valid_frame_still_processed(hass: HomeAssistant, dongle: FakeDongle) -> None:
    dongle.inject(d5_frame(CONTACT_INT, closed=False))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "on"


async def test_garbage_then_valid(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    dongle.inject(b"\x00\x12\x34garbage-without-sync-byte")
    await _valid_frame_still_processed(hass, dongle)


async def test_truncated_header_recovers_with_traffic(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """A stray sync byte plus bogus length stalls the parser until enough
    bytes arrive (ESP3 resync rule); later traffic is then parsed."""
    await _setup(hass)
    dongle.inject(b"\x55\x00")  # sync + start of a phantom 85-byte frame
    for _ in range(5):
        dongle.inject(d5_frame(CONTACT_INT, closed=False))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "on"


async def test_bad_header_crc_then_valid(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    frame = bytearray(d5_frame(CONTACT_INT, closed=True))
    frame[5] ^= 0xFF
    dongle.inject(bytes(frame))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "unknown"  # corrupted frame dropped
    await _valid_frame_still_processed(hass, dongle)


async def test_bad_data_crc_then_valid(hass: HomeAssistant, dongle: FakeDongle) -> None:
    await _setup(hass)
    frame = bytearray(d5_frame(CONTACT_INT, closed=True))
    frame[-1] ^= 0xFF
    dongle.inject(bytes(frame))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "unknown"
    await _valid_frame_still_processed(hass, dongle)


async def test_truncated_telegram_then_valid(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    await _setup(hass)
    valid = d5_frame(CONTACT_INT, closed=True)
    dongle.inject(valid[: len(valid) // 2])
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "unknown"
    # the parser resynchronises on the next frame's sync byte
    await _valid_frame_still_processed(hass, dongle)


async def test_unknown_rorg_ignored(hass: HomeAssistant, dongle: FakeDongle) -> None:
    from enocean_async.protocol.esp3.packet import ESP3Packet, ESP3PacketType

    await _setup(hass)
    data = bytes([0xC5, 0x00]) + CONTACT_INT.to_bytes(4, "big") + b"\x00"
    dongle.inject(ESP3Packet(ESP3PacketType.RADIO_ERP1, data, b"").to_bytes())
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "unknown"
    await _valid_frame_still_processed(hass, dongle)
