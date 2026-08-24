"""Guided pairing: sender auto-allocation and the answered UTE teach-in.

The pairing window is focused on one device, time-bounded, and only opened by
an explicit user action on the discovery card.
"""

import asyncio

import pytest
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.enocean_direct.const import CONF_DEVICES, DOMAIN
from custom_components.enocean_direct.gateway import EnOceanHub, PairingError

from .conftest import (
    ACTUATOR,
    FakeDongle,
    flush,
    make_entry,
    setup_entry,
    ute_teach_in_frame,
)

NEW_COVER = 0x051B2C99
NEW_COVER_HEX = "051B2C99"


def _discovery_flow(hass: HomeAssistant) -> dict:
    return next(
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN
        and flow["context"]["source"] == SOURCE_INTEGRATION_DISCOVERY
    )


async def test_guided_pairing_full_flow(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    # the existing actuator occupies offset 0, so pairing must allocate +1
    entry = make_entry(hass, [ACTUATOR])
    assert await setup_entry(hass, entry)

    dongle.inject(ute_teach_in_frame(NEW_COVER, 0xD2, 0x05, 0x00))
    await hass.async_block_till_done()
    assert dongle.sent_radio == []  # discovery alone never answers the UTE

    flow = _discovery_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"name": "Bedroom blind"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "pair_or_manual"

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "pair"}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert dongle.sent_radio == []  # window open, nothing transmitted yet

    # the user presses the teach-in button again during the window
    dongle.inject(ute_teach_in_frame(NEW_COVER, 0xD2, 0x05, 0x00))
    await flush(hass)

    # the gateway answered with an ACCEPTED teach-in from the allocated sender
    assert len(dongle.sent_radio) == 1
    data, optional = dongle.sent_radio[0]
    assert data[0] == 0xD4  # UTE
    assert data[1] == 0x91  # bidirectional | ACCEPTED_TEACH_IN | response cmd
    assert data[8:12] == bytes.fromhex("FF974101")  # allocated sender
    assert optional[1:5] == NEW_COVER.to_bytes(4, "big")  # addressed reply

    # the flow manager advances through SHOW_PROGRESS_DONE to the final step
    result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_added"
    await hass.async_block_till_done()

    assert entry.options[CONF_DEVICES][-1] == {
        "address": NEW_COVER_HEX,
        "eep": "D2-05-00",
        "name": "Bedroom blind",
        "sender_id": "FF974101",
        "channel": 0,
    }
    assert hass.states.get("cover.bedroom_blind") is not None


async def test_pairing_timeout_aborts(
    hass: HomeAssistant, dongle: FakeDongle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "custom_components.enocean_direct.gateway.PAIRING_TIMEOUT", 0.05
    )
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(ute_teach_in_frame(NEW_COVER, 0xD2, 0x05, 0x00))
    await hass.async_block_till_done()

    flow = _discovery_flow(hass)
    await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"name": "Bedroom blind"}
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "pair"}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS

    await asyncio.sleep(0.1)  # no teach-in arrives
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "pair_timeout"
    assert dongle.sent_radio == []
    assert entry.options.get(CONF_DEVICES, []) == []


async def test_allocate_sender_skips_used_and_exhausts(hass: HomeAssistant) -> None:
    entry = make_entry(hass, [ACTUATOR])  # offset 0 in use
    hub = EnOceanHub(hass, entry)
    assert hub.allocate_sender() == "FF974101"

    all_used = [
        {
            "address": f"05000{i:03X}",
            "eep": "D2-01-0F",
            "name": f"d{i}",
            "sender_id": f"{0xFF974100 + i:08X}",
            "channel": 0,
        }
        for i in range(1, 128)
    ]
    hub = EnOceanHub(hass, make_entry(hass, all_used))
    with pytest.raises(PairingError) as err:
        hub.allocate_sender()
    assert err.value.reason == "pair_no_free_sender"
