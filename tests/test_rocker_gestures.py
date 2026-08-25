"""Synthesised rocker gestures: held, released_after_hold, double_pressed.

Timing is driven with the freezer fixture and exact time-changed events, so
the hold timer and the double-press window are pinned to the constants in
const.py. The plain pressed/released events must stay untouched.
"""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed_exact,
    async_mock_service,
)

from custom_components.enocean_direct.const import (
    DOMAIN,
    EVENT_BUTTON,
    ROCKER_DOUBLE_PRESS_SECONDS,
    ROCKER_HOLD_SECONDS,
)

from .conftest import ROCKER, FakeDongle, f6_frame, flush, make_entry, setup_entry

ROCKER_INT = int(ROCKER["address"], 16)
ROCKER_2 = {"address": "002909BF", "eep": "F6-02-01", "name": "Test rocker 2"}
ROCKER_2_INT = int(ROCKER_2["address"], 16)
AO_PRESS = 0x30
BI_PRESS = 0x50
RELEASE = 0x00


async def _setup(hass: HomeAssistant) -> list[dict]:
    entry = make_entry(hass, [ROCKER])
    assert await setup_entry(hass, entry)
    events: list[dict] = []
    hass.bus.async_listen(EVENT_BUTTON, lambda e: events.append(e.data))
    return events


async def _advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed_exact(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def _inject(
    hass: HomeAssistant, dongle: FakeDongle, db0: int, sender: int = ROCKER_INT
) -> None:
    """Feed one F6 telegram and let the library's call_soon dispatch run, so
    the hub processes it (and arms its timers) before the clock moves."""
    status = 0x20 if db0 == RELEASE else 0x30
    dongle.inject(f6_frame(sender, db0, status=status))
    await flush(hass)


def _types(events: list[dict]) -> list[tuple[str, str | None]]:
    return [(e["type"], e["button"]) for e in events]


async def test_hold_fires_while_down_then_release_after_hold(
    hass: HomeAssistant, dongle: FakeDongle, freezer
) -> None:
    events = await _setup(hass)
    await _inject(hass, dongle, AO_PRESS)
    assert _types(events) == [("pressed", "ao")]

    # just short of the threshold: nothing yet
    await _advance(hass, freezer, ROCKER_HOLD_SECONDS - 0.1)
    assert _types(events) == [("pressed", "ao")]

    # threshold reached while the button is still down: held carries the button
    await _advance(hass, freezer, 0.1)
    assert _types(events) == [("pressed", "ao"), ("held", "ao")]

    await _inject(hass, dongle, RELEASE)
    assert _types(events) == [
        ("pressed", "ao"),
        ("held", "ao"),
        ("released", None),
        ("released_after_hold", "ao"),
    ]

    # a hold followed by a quick tap is not a double press
    await _inject(hass, dongle, AO_PRESS)
    assert _types(events)[-1] == ("pressed", "ao")


async def test_short_press_cancels_hold(
    hass: HomeAssistant, dongle: FakeDongle, freezer
) -> None:
    events = await _setup(hass)
    await _inject(hass, dongle, AO_PRESS)
    await _advance(hass, freezer, 0.2)
    await _inject(hass, dongle, RELEASE)
    assert _types(events) == [("pressed", "ao"), ("released", None)]

    # well past the hold threshold: the cancelled timer must stay silent
    await _advance(hass, freezer, ROCKER_HOLD_SECONDS * 2)
    assert _types(events) == [("pressed", "ao"), ("released", None)]


async def test_double_press_same_button_within_window(
    hass: HomeAssistant, dongle: FakeDongle, freezer
) -> None:
    events = await _setup(hass)
    await _inject(hass, dongle, AO_PRESS)
    # held 0.4 s (short of hold), so the second press below is inside the
    # window measured from the RELEASE (0.4 s) but outside one measured from
    # the first press (0.8 s): pins the documented anchor
    await _advance(hass, freezer, ROCKER_HOLD_SECONDS - 0.1)
    await _inject(hass, dongle, RELEASE)
    await _advance(hass, freezer, ROCKER_DOUBLE_PRESS_SECONDS - 0.1)
    await _inject(hass, dongle, AO_PRESS)  # second press inside the window
    assert _types(events) == [
        ("pressed", "ao"),
        ("released", None),
        ("pressed", "ao"),
        ("double_pressed", "ao"),
    ]

    # the second press starts its own hold timer like any press
    await _advance(hass, freezer, ROCKER_HOLD_SECONDS)
    assert _types(events)[-1] == ("held", "ao")


async def test_no_double_press_outside_window_or_other_button(
    hass: HomeAssistant, dongle: FakeDongle, freezer
) -> None:
    events = await _setup(hass)
    # too slow
    await _inject(hass, dongle, AO_PRESS)
    await _advance(hass, freezer, 0.1)
    await _inject(hass, dongle, RELEASE)
    await _advance(hass, freezer, ROCKER_DOUBLE_PRESS_SECONDS + 0.1)
    await _inject(hass, dongle, AO_PRESS)
    await _advance(hass, freezer, 0.1)
    await _inject(hass, dongle, RELEASE)
    assert "double_pressed" not in [t for t, _ in _types(events)]

    # other button inside the window
    await _advance(hass, freezer, 0.1)
    await _inject(hass, dongle, BI_PRESS)
    assert _types(events)[-1] == ("pressed", "bi")
    assert "double_pressed" not in [t for t, _ in _types(events)]


async def test_triple_tap_fires_one_double(
    hass: HomeAssistant, dongle: FakeDongle, freezer
) -> None:
    """The double-press window resets after firing, so three taps give one
    double_pressed (taps 1+2), not two."""
    events = await _setup(hass)
    for _ in range(3):
        await _inject(hass, dongle, AO_PRESS)
        await _advance(hass, freezer, 0.1)
        await _inject(hass, dongle, RELEASE)
        await _advance(hass, freezer, 0.1)
    assert [t for t, _ in _types(events)].count("double_pressed") == 1


async def test_missed_release_restarts_hold(
    hass: HomeAssistant, dongle: FakeDongle, freezer
) -> None:
    """A second press without a release in between (lost telegram) replaces
    the tracked press: one held, for the new button, at the new deadline."""
    events = await _setup(hass)
    await _inject(hass, dongle, AO_PRESS)
    await _advance(hass, freezer, 0.3)
    await _inject(hass, dongle, BI_PRESS)
    await _advance(hass, freezer, 0.3)  # 0.6 s after AO, 0.3 s after BI
    assert "held" not in [t for t, _ in _types(events)]
    await _advance(hass, freezer, 0.3)  # 0.6 s after BI
    assert _types(events) == [("pressed", "ao"), ("pressed", "bi"), ("held", "bi")]


async def test_unload_while_held_fires_nothing(
    hass: HomeAssistant, dongle: FakeDongle, freezer
) -> None:
    entry = make_entry(hass, [ROCKER])
    assert await setup_entry(hass, entry)
    events: list[dict] = []
    hass.bus.async_listen(EVENT_BUTTON, lambda e: events.append(e.data))
    await _inject(hass, dongle, AO_PRESS)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await _advance(hass, freezer, ROCKER_HOLD_SECONDS * 2)
    assert _types(events) == [("pressed", "ao")]


async def test_held_trigger_runs_automation(
    hass: HomeAssistant, dongle: FakeDongle, freezer
) -> None:
    from homeassistant.helpers import device_registry as dr

    entry = make_entry(hass, [ROCKER])
    assert await setup_entry(hass, entry)
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, ROCKER["address"])}
    )
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
                        "device_id": device.id,
                        "type": "held",
                        "subtype": "ao",
                    },
                    "action": {"service": "test.automation"},
                },
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device.id,
                        "type": "released_after_hold",
                        "subtype": "ao",
                    },
                    "action": {"service": "test.automation"},
                },
            ]
        },
    )
    await hass.async_block_till_done()

    await _inject(hass, dongle, AO_PRESS)
    await _advance(hass, freezer, ROCKER_HOLD_SECONDS)
    assert len(calls) == 1  # held
    await _inject(hass, dongle, RELEASE)
    assert len(calls) == 2  # released_after_hold


async def test_release_without_press_is_ignored(
    hass: HomeAssistant, dongle: FakeDongle, freezer
) -> None:
    """A release heard with no tracked press (HA restarted while a button was
    down) is only the plain event; a duplicate release after a short press
    neither seeds nor breaks the double-press window."""
    events = await _setup(hass)
    await _inject(hass, dongle, RELEASE)
    assert _types(events) == [("released", None)]
    await _advance(hass, freezer, 0.1)
    await _inject(hass, dongle, AO_PRESS)
    assert _types(events)[-1] == ("pressed", "ao")  # not a double
    await _advance(hass, freezer, 0.1)
    await _inject(hass, dongle, RELEASE)  # t0
    await _advance(hass, freezer, ROCKER_DOUBLE_PRESS_SECONDS - 0.1)
    await _inject(hass, dongle, RELEASE)  # duplicate release
    await _advance(hass, freezer, 0.2)  # t0 + 0.6: outside the window from t0
    await _inject(hass, dongle, AO_PRESS)
    assert "double_pressed" not in [t for t, _ in _types(events)]
    assert "released_after_hold" not in [t for t, _ in _types(events)]


async def test_gestures_are_per_rocker(
    hass: HomeAssistant, dongle: FakeDongle, freezer
) -> None:
    """Rocker 2's telegrams must not disturb rocker 1's hold or window."""
    entry = make_entry(hass, [ROCKER, ROCKER_2])
    assert await setup_entry(hass, entry)
    events: list[dict] = []
    hass.bus.async_listen(EVENT_BUTTON, lambda e: events.append(e.data))

    await _inject(hass, dongle, AO_PRESS)  # rocker 1 down
    await _advance(hass, freezer, 0.1)
    await _inject(hass, dongle, RELEASE, ROCKER_2_INT)  # unrelated release
    await _advance(hass, freezer, ROCKER_HOLD_SECONDS)
    await _inject(hass, dongle, RELEASE)
    assert [(e["address"], e["type"], e["button"]) for e in events] == [
        (ROCKER["address"], "pressed", "ao"),
        (ROCKER_2["address"], "released", None),
        (ROCKER["address"], "held", "ao"),
        (ROCKER["address"], "released", None),
        (ROCKER["address"], "released_after_hold", "ao"),
    ]

    # a short press on rocker 1 and the same button on rocker 2 is no double
    events.clear()
    await _inject(hass, dongle, AO_PRESS)
    await _advance(hass, freezer, 0.1)
    await _inject(hass, dongle, RELEASE)
    await _advance(hass, freezer, 0.1)
    await _inject(hass, dongle, AO_PRESS, ROCKER_2_INT)
    assert "double_pressed" not in [e["type"] for e in events]
