"""Auto-discovery: teach-in telegrams surface as native discovery flows.

Only deliberate teach-ins (D5 LRN press, UTE) from unconfigured EURIDs with a
supported profile create a flow. Plain data telegrams stay in the inbox.
"""

from homeassistant.config_entries import SOURCE_IGNORE, SOURCE_INTEGRATION_DISCOVERY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.enocean_direct.const import CONF_DEVICES, DOMAIN

from .conftest import (
    BASE_ID_HEX,
    CONTACT,
    FakeDongle,
    d5_frame,
    f6_frame,
    make_entry,
    setup_entry,
    ute_teach_in_frame,
)

NEW_CONTACT = 0x0084AAAA
NEW_ACTUATOR = 0x050A5C99


def _discovery_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN
        and flow["context"]["source"] == SOURCE_INTEGRATION_DISCOVERY
    ]


async def test_d5_teach_in_creates_single_flow(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)

    dongle.inject(d5_frame(NEW_CONTACT, closed=False, teach_in=True))
    await hass.async_block_till_done()
    flows = _discovery_flows(hass)
    assert len(flows) == 1
    assert flows[0]["step_id"] == "discovered_device"

    # a second teach-in must not create a second card
    dongle.inject(d5_frame(NEW_CONTACT, closed=False, teach_in=True))
    await hass.async_block_till_done()
    assert len(_discovery_flows(hass)) == 1


async def test_complete_contact_discovery(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(d5_frame(NEW_CONTACT, closed=False, teach_in=True))
    await hass.async_block_till_done()
    flow = _discovery_flows(hass)[0]

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"name": "Porch door"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_added"
    await hass.async_block_till_done()

    assert entry.options[CONF_DEVICES] == [
        {"address": "0084AAAA", "eep": "D5-00-01", "name": "Porch door"}
    ]
    assert hass.states.get("binary_sensor.porch_door") is not None
    # completed flow leaves no discovery card behind
    assert _discovery_flows(hass) == []


async def test_ute_actuator_discovery(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(ute_teach_in_frame(NEW_ACTUATOR, 0xD2, 0x01, 0x0F))
    await hass.async_block_till_done()
    assert dongle.sent_radio == []  # discovery must never acknowledge the UTE

    flow = _discovery_flows(hass)[0]
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"name": "New relay"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "pair_or_manual"

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "discovered_actuator"}
    )
    assert result["step_id"] == "discovered_actuator"

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"sender_id": "FF974180", "channel": 0}
    )
    assert result["errors"] == {"sender_id": "sender_out_of_range"}

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"sender_id": BASE_ID_HEX, "channel": 0}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_added"
    await hass.async_block_till_done()

    assert entry.options[CONF_DEVICES] == [
        {
            "address": "050A5C99",
            "eep": "D2-01-0F",
            "name": "New relay",
            "sender_id": BASE_ID_HEX,
            "channel": 0,
        }
    ]
    assert hass.states.get("switch.new_relay") is not None


async def test_data_telegrams_do_not_discover(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(d5_frame(NEW_CONTACT, closed=True))  # data, not teach-in
    dongle.inject(f6_frame(0x002909AA, 0x30))  # rocker press
    await hass.async_block_till_done()
    assert _discovery_flows(hass) == []
    # but both are visible in the inbox
    hub = entry.runtime_data
    assert hub.inbox.get("0084AAAA") is not None
    assert hub.inbox.get("002909AA") is not None


async def test_configured_device_not_rediscovered(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass, [CONTACT])
    assert await setup_entry(hass, entry)
    dongle.inject(d5_frame(int(CONTACT["address"], 16), closed=False, teach_in=True))
    await hass.async_block_till_done()
    assert _discovery_flows(hass) == []


async def test_unsupported_eep_not_discovered(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(ute_teach_in_frame(0x05AABB01, 0xD2, 0x03, 0x0A))  # a button
    await hass.async_block_till_done()
    assert _discovery_flows(hass) == []
    # still recorded in the inbox with its declared profile
    hub = entry.runtime_data
    assert hub.inbox.get("05AABB01").eep == "D2-03-0A"


async def test_ignored_device_stays_ignored(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(d5_frame(NEW_CONTACT, closed=False, teach_in=True))
    await hass.async_block_till_done()
    assert len(_discovery_flows(hass)) == 1

    # the user clicks Ignore on the discovery card
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IGNORE},
        data={"unique_id": "device-0084AAAA", "title": "D5-00-01 0084AAAA"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert _discovery_flows(hass) == []

    dongle.inject(d5_frame(NEW_CONTACT, closed=False, teach_in=True))
    await hass.async_block_till_done()
    assert _discovery_flows(hass) == []


async def test_discovery_with_area(hass: HomeAssistant, dongle: FakeDongle) -> None:
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr

    area = ar.async_get(hass).async_create("Porch")
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(d5_frame(NEW_CONTACT, closed=False, teach_in=True))
    await hass.async_block_till_done()
    flow = _discovery_flows(hass)[0]

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"name": "Porch door", "area_id": area.id}
    )
    assert result["reason"] == "device_added"
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "0084AAAA")})
    assert device.area_id == area.id
