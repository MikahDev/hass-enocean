"""Per-device diagnostic sensors: RSSI, last seen, telegram count, sender ID."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import (
    ACTUATOR,
    BASE_ID_HEX,
    CONTACT,
    COVER,
    ROCKER,
    FakeDongle,
    d2_05_reply_frame,
    d2_status_frame,
    d5_frame,
    f6_frame,
    flush,
    make_entry,
    setup_entry,
)

CONTACT_INT = int(CONTACT["address"], 16)
ROCKER_INT = int(ROCKER["address"], 16)
ACTUATOR_INT = int(ACTUATOR["address"], 16)
COVER_INT = int(COVER["address"], 16)


async def test_sensors_created_for_all_kinds(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT, ROCKER, ACTUATOR, COVER])
    assert await setup_entry(hass, entry)
    for prefix in ("test_contact", "test_rocker", "pilot_relay", "test_blind"):
        assert hass.states.get(f"sensor.{prefix}_signal_strength") is not None
        assert hass.states.get(f"sensor.{prefix}_last_seen") is not None
    # sender ID only exists for devices commanded with one, and shows the
    # record's own sender, not the transceiver base ID
    assert hass.states.get("sensor.pilot_relay_sender_id").state == BASE_ID_HEX
    assert hass.states.get("sensor.test_blind_sender_id").state == "FF974101"
    assert hass.states.get("sensor.test_contact_sender_id") is None
    assert hass.states.get("sensor.test_rocker_sender_id") is None
    # telegram count is registered but disabled by default
    assert hass.states.get("sensor.pilot_relay_telegram_count") is None
    registry = er.async_get(hass)
    assert registry.async_get("sensor.pilot_relay_telegram_count") is not None


async def test_rssi_and_last_seen_update(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    assert hass.states.get("sensor.test_contact_signal_strength").state == "unknown"
    assert hass.states.get("sensor.test_contact_last_seen").state == "unknown"

    dongle.inject(d5_frame(CONTACT_INT, closed=True, rssi=45))
    await flush(hass)
    assert hass.states.get("sensor.test_contact_signal_strength").state == "-45"
    last_seen = hass.states.get("sensor.test_contact_last_seen").state
    assert dt_util.parse_datetime(last_seen) is not None

    dongle.inject(d5_frame(CONTACT_INT, closed=False, rssi=60))
    await flush(hass)
    assert hass.states.get("sensor.test_contact_signal_strength").state == "-60"

    # 0x00 means "not reported": honest unknown, not a stale value
    dongle.inject(d5_frame(CONTACT_INT, closed=True, rssi=0))
    await flush(hass)
    assert hass.states.get("sensor.test_contact_signal_strength").state == "unknown"


async def test_actuator_status_feeds_diagnostics(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """Library-registered devices flow through the same _on_erp1 path."""
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)
    dongle.inject(d2_status_frame(ACTUATOR_INT, channel=0, output_value=100, rssi=52))
    await flush(hass)
    assert hass.states.get("sensor.pilot_relay_signal_strength").state == "-52"
    last_seen = hass.states.get("sensor.pilot_relay_last_seen").state
    assert dt_util.parse_datetime(last_seen) is not None


async def test_cover_reply_feeds_diagnostics(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [COVER])
    assert await setup_entry(hass, entry)
    dongle.inject(d2_05_reply_frame(COVER_INT, position=100, rssi=48))
    await flush(hass)
    assert hass.states.get("sensor.test_blind_signal_strength").state == "-48"


async def test_telegram_count(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [ROCKER])
    assert await setup_entry(hass, entry)

    # enable the default-disabled counter, then reload so it is created
    registry = er.async_get(hass)
    registry.async_update_entity("sensor.test_rocker_telegram_count", disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.test_rocker_telegram_count").state == "0"
    dongle.inject(f6_frame(ROCKER_INT, 0x30))  # press
    dongle.inject(f6_frame(ROCKER_INT, 0x00, status=0x20))  # release
    await flush(hass)
    assert hass.states.get("sensor.test_rocker_telegram_count").state == "2"


async def test_unconfigured_sender_updates_nothing(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    dongle.inject(d5_frame(0x0084BBBB, closed=True, rssi=45))  # foreign device
    await flush(hass)
    assert hass.states.get("sensor.test_contact_signal_strength").state == "unknown"
