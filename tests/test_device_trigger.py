"""F6 rocker device triggers: enumeration, press/release firing."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.enocean_direct.const import DOMAIN, EVENT_BUTTON
from custom_components.enocean_direct.device_trigger import async_get_triggers

from .conftest import (
    CONTACT,
    ROCKER,
    FakeDongle,
    f6_frame,
    flush,
    make_entry,
    setup_entry,
)

ROCKER_INT = int(ROCKER["address"], 16)


def _rocker_device_id(hass: HomeAssistant) -> str:
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, ROCKER["address"])}
    )
    assert device is not None
    return device.id


async def test_get_triggers(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [ROCKER, CONTACT])
    assert await setup_entry(hass, entry)
    triggers = await async_get_triggers(hass, _rocker_device_id(hass))
    types = {(t["type"], t.get("subtype")) for t in triggers}
    per_button = ("pressed", "held", "released_after_hold", "double_pressed")
    assert types == {
        (trigger_type, subtype)
        for trigger_type in per_button
        for subtype in ("ai", "ao", "bi", "bo")
    } | {("released", None)}

    # a contact device offers no rocker triggers
    contact_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, CONTACT["address"])}
    )
    assert await async_get_triggers(hass, contact_device.id) == []


async def test_press_and_release_events(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [ROCKER])
    assert await setup_entry(hass, entry)
    events = []
    hass.bus.async_listen(EVENT_BUTTON, lambda e: events.append(e.data))

    dongle.inject(f6_frame(ROCKER_INT, 0x30))  # AO pressed
    dongle.inject(f6_frame(ROCKER_INT, 0x00, status=0x20))  # released
    await hass.async_block_till_done()

    assert [e["type"] for e in events] == ["pressed", "released"]
    assert events[0]["button"] == "ao"
    assert events[0]["second_button"] is None
    assert events[0]["address"] == ROCKER["address"]
    assert events[0]["device_id"] == _rocker_device_id(hass)
    assert events[1]["button"] is None
    assert events[1]["second_button"] is None

    # two buttons at once: R1 = AO, SA set, R2 = BO
    dongle.inject(f6_frame(ROCKER_INT, 0x37))
    await hass.async_block_till_done()
    assert events[2]["type"] == "pressed"
    assert events[2]["button"] == "ao"
    assert events[2]["second_button"] == "bo"


async def test_trigger_fires_automation(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [ROCKER])
    assert await setup_entry(hass, entry)
    device_id = _rocker_device_id(hass)
    calls = async_mock_service(hass, "test", "automation")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device_id,
                        "type": "pressed",
                        "subtype": "bi",
                    },
                    "action": {"service": "test.automation"},
                }
            ]
        },
    )
    await hass.async_block_till_done()

    dongle.inject(f6_frame(ROCKER_INT, 0x30))  # AO press: must not fire
    await flush(hass)
    assert len(calls) == 0

    dongle.inject(f6_frame(ROCKER_INT, 0x50))  # BI press: must fire
    await flush(hass)
    assert len(calls) == 1


async def test_rocker_creates_no_state_entities(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """No misleading persistent button state for momentary rockers; every
    rocker entity must be a diagnostic (checked in the registry, which also
    covers default-disabled entities)."""
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import entity_registry as er

    entry = make_entry(hass, [ROCKER])
    assert await setup_entry(hass, entry)
    registry = er.async_get(hass)
    rocker_entries = [
        entry for entry in registry.entities.values() if "rocker" in entry.entity_id
    ]
    assert rocker_entries  # diagnostics are expected
    assert all(
        entry.entity_category is EntityCategory.DIAGNOSTIC for entry in rocker_entries
    )
