"""Wave-2 receive-only sensor profiles: decode fixtures and entity creation.

Flagship profiles use hand-pinned telegram bytes computed from the published
EEP spec (guards bit offsets). The parametrised decode tests build telegrams
with the library's own encoder from raw field values (guards scaling,
inversion, enum labels and resolver logic). Together with the description
drift guard this covers every wave-2 profile.
"""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.enocean_direct.binary_sensor import (
    PROFILE_DESCRIPTIONS as BINARY_DESCRIPTIONS,
)
from custom_components.enocean_direct.const import CONF_DEVICES, DOMAIN, EEP_SENSORS
from custom_components.enocean_direct.gateway import sensor_entities_for_eep
from custom_components.enocean_direct.sensor import PROFILE_DESCRIPTIONS

from .conftest import (
    FakeDongle,
    a5_data_frame,
    erp1_frame,
    flush,
    fourbs_teach_in_frame,
    make_entry,
    setup_entry,
    ute_teach_in_frame,
)

SENSOR = 0x05201000
NEW_SENSOR = 0x05201099


def _record(eep: str, name: str = "Dev", address: int = SENSOR) -> dict:
    return {"address": f"{address:08X}", "eep": eep, "name": name}


def test_every_profile_entity_has_a_description() -> None:
    """Guard against drift between the library's specs and our tables."""
    for eep in EEP_SENSORS:
        entities = sensor_entities_for_eep(eep)
        assert entities, eep
        for entity_id, _observable, is_binary in entities:
            table = BINARY_DESCRIPTIONS if is_binary else PROFILE_DESCRIPTIONS
            assert entity_id in table, (eep, entity_id)


def test_binary_label_vocabulary_pinned() -> None:
    """Every enum label the pinned library can emit for a binary observable
    must be classified on or off, and window_state labels must be within the
    declared ENUM options. A library bump that rewords a label fails here
    instead of silently leaving entities unknown."""
    from enocean_async.eep import EEP_SPECIFICATIONS
    from enocean_async.eep.id import EEP
    from enocean_async.semantics.value_kind import ValueKind

    window_options = set(PROFILE_DESCRIPTIONS["window_state"].options)
    for eep in EEP_SENSORS:
        spec = EEP_SPECIFICATIONS[EEP(eep)]
        for telegram in spec.telegrams.values():
            for field in telegram.datafields:
                if field.observable is None or not field.range_enum:
                    continue
                if field.observable.value == "window_state":
                    assert set(field.range_enum.values()) <= window_options, eep
                elif field.observable.kind is ValueKind.BINARY:
                    _desc, on_labels, off_labels = BINARY_DESCRIPTIONS[
                        field.observable.value
                    ]
                    for label in field.range_enum.values():
                        assert label.lower() in on_labels | off_labels, (eep, label)


# ----------------------------------------------------------------------
# flagship profiles: hand-pinned bytes from the published EEP spec
# ----------------------------------------------------------------------
async def test_a5_04_01_pinned_bytes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [_record("A5-04-01")])
    assert await setup_entry(hass, entry)
    # DB3=0, HUM raw 100 -> 40.0%, TMP raw 125 -> 20.0degC, DB0 = LRN data bit
    dongle.inject(erp1_frame(0xA5, [0x00, 100, 125, 0x08], SENSOR))
    await flush(hass)
    assert hass.states.get("sensor.dev_humidity").state == "40.0"
    assert hass.states.get("sensor.dev_temperature").state == "20.0"


async def test_a5_02_05_pinned_bytes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [_record("A5-02-05")])
    assert await setup_entry(hass, entry)
    # inverted scaling: raw 51 -> (255-51)/255 * 40 = 32.0degC
    dongle.inject(erp1_frame(0xA5, [0x00, 0x00, 51, 0x08], SENSOR))
    await flush(hass)
    assert hass.states.get("sensor.dev_temperature").state == "32.0"


async def test_a5_07_03_pinned_bytes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [_record("A5-07-03")])
    assert await setup_entry(hass, entry)
    # SVC raw 125 -> 2.5V; ILL 10-bit raw 600 -> 600lx (0x96 + top bits 00);
    # DB0 = PIR motion bit (0x80) | LRN data bit (0x08)
    dongle.inject(erp1_frame(0xA5, [125, 0x96, 0x00, 0x88], SENSOR))
    await flush(hass)
    assert hass.states.get("sensor.dev_supply_voltage").state == "2.5"
    assert hass.states.get("sensor.dev_illuminance").state == "600.0"
    assert hass.states.get("binary_sensor.dev_motion").state == "on"


async def test_a5_12_01_pinned_bytes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [_record("A5-12-01")])
    assert await setup_entry(hass, entry)
    # MR raw 123456 = 0x01E240, DB0 = LRN (0x08) | DIV 1 (x/10), DT 0 =
    # cumulative -> energy 12345.6 Wh; power (current value) stays unknown
    dongle.inject(erp1_frame(0xA5, [0x01, 0xE2, 0x40, 0x09], SENSOR))
    await flush(hass)
    assert hass.states.get("sensor.dev_energy").state == "12345.6"
    assert hass.states.get("sensor.dev_power").state == "unknown"


async def test_f6_10_00_pinned_bytes(hass: HomeAssistant, dongle: FakeDongle) -> None:
    entry = make_entry(hass, [_record("F6-10-00")])
    assert await setup_entry(hass, entry)
    # WIN bits 2-3 of the single data byte: 3 = closed, 1 = tilted
    dongle.inject(erp1_frame(0xF6, [0x30], SENSOR, status=0x20))
    await flush(hass)
    assert hass.states.get("sensor.dev_window_handle").state == "closed"
    dongle.inject(erp1_frame(0xF6, [0x10], SENSOR, status=0x20))
    await flush(hass)
    assert hass.states.get("sensor.dev_window_handle").state == "tilted"


# ----------------------------------------------------------------------
# encoder-built decode cases: scaling, inversion, enum labels, resolvers
# ----------------------------------------------------------------------
DECODE_CASES = [
    ("A5-02-01", {"TMP": 255}, {"sensor.dev_temperature": "-40.0"}),
    ("A5-02-20", {"TMP": 1023}, {"sensor.dev_temperature": "-10.0"}),  # 10-bit
    (
        "A5-04-03",
        {"HUM": 51, "TMP": 1023},
        {"sensor.dev_humidity": "20.0", "sensor.dev_temperature": "60.0"},
    ),
    (
        "A5-10-01",
        {"FAN": 200, "SP": 100, "TMP": 51, "OCC": 1},
        {
            "sensor.dev_fan_speed": "Stage 0",
            "sensor.dev_set_point": "100.0",
            "sensor.dev_temperature": "32.0",  # inverted
            "binary_sensor.dev_occupancy_button": "off",  # active-low button
        },
    ),
    (
        "A5-10-0A",
        {"SP": 0, "TMP": 255, "CTST": 1},
        # CTST 1 = Open; is_on means open, same polarity rule as D5-00-01
        {"binary_sensor.dev_contact": "on", "sensor.dev_temperature": "0.0"},
    ),
    (
        "A5-08-01",
        {"PIRS": 0, "OCC": 0},
        # active-low on the wire: raw 0 = motion / pressed; labels decide
        {
            "binary_sensor.dev_motion": "on",
            "binary_sensor.dev_occupancy_button": "on",
        },
    ),
    (
        "A5-12-02",
        {"MR": 123456, "DT": 0, "DIV": 3},
        {"sensor.dev_gas_volume": "123.456", "sensor.dev_gas_flow": "unknown"},
    ),
    (
        "A5-12-02",
        {"MR": 123456, "DT": 1, "DIV": 3},
        {"sensor.dev_gas_flow": "123.456"},
    ),
]


@pytest.mark.parametrize(("eep", "raw", "expected"), DECODE_CASES)
async def test_profile_decode(
    hass: HomeAssistant, dongle: FakeDongle, eep: str, raw: dict, expected: dict
) -> None:
    entry = make_entry(hass, [_record(eep)])
    assert await setup_entry(hass, entry)
    dongle.inject(a5_data_frame(SENSOR, eep, raw))
    await flush(hass)
    for entity_id, state in expected.items():
        assert hass.states.get(entity_id).state == state, (eep, entity_id)


# ----------------------------------------------------------------------
# every profile creates exactly the entities its spec declares
# ----------------------------------------------------------------------
async def test_all_profiles_create_expected_entities(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    devices = [
        _record(eep, name=f"Dev{i}", address=0x05100000 + i)
        for i, eep in enumerate(EEP_SENSORS)
    ]
    entry = make_entry(hass, devices)
    assert await setup_entry(hass, entry)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for i, eep in enumerate(EEP_SENSORS):
        address = f"{0x05100000 + i:08X}"
        device = device_registry.async_get_device(identifiers={(DOMAIN, address)})
        assert device is not None, eep
        entries = er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        )
        # profile entities + rssi/last_seen/telegram_count diagnostics
        assert len(entries) == len(sensor_entities_for_eep(eep)) + 3, eep


# ----------------------------------------------------------------------
# 4BS teach-ins: discovery with the declared EEP; pairing not needed
# ----------------------------------------------------------------------
async def test_4bs_teach_in_creates_discovery(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    dongle.inject(fourbs_teach_in_frame(NEW_SENSOR, 0x04, 0x01))  # A5-04-01
    await hass.async_block_till_done()
    assert dongle.sent_radio == []  # never answered outside a pairing window
    assert entry.runtime_data.inbox.get(f"{NEW_SENSOR:08X}").eep == "A5-04-01"

    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN
    ]
    assert len(flows) == 1
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {"name": "Office climate"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_added"
    await hass.async_block_till_done()
    assert entry.options[CONF_DEVICES] == [
        {"address": f"{NEW_SENSOR:08X}", "eep": "A5-04-01", "name": "Office climate"}
    ]
    assert hass.states.get("sensor.office_climate_temperature") is not None


async def test_menu_pairing_receive_only_aborts(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """A UTE teach-in declaring a receive-only profile ends pairing with an
    explanation instead of storing anything."""
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "pair_device"}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS

    dongle.inject(ute_teach_in_frame(NEW_SENSOR, 0xA5, 0x04, 0x01))
    await flush(hass)
    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "pair_receive_only"
    assert entry.options.get(CONF_DEVICES, []) == []


async def test_menu_pairing_4bs_receive_only(
    hass: HomeAssistant, dongle: FakeDongle
) -> None:
    """The realistic wave-2 flow: pairing window open, LRN pressed on a plain
    4BS sensor. The window resolves with the receive-only explanation (the
    library itself ignores plain 4BS teach-ins), no discovery card appears
    mid-window, and a repeat teach-in after the window creates the card."""
    entry = make_entry(hass)
    assert await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "pair_device"}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS

    dongle.inject(fourbs_teach_in_frame(NEW_SENSOR, 0x04, 0x01))
    await flush(hass)
    assert not [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN
    ]
    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "pair_receive_only"
    assert dongle.sent_radio == []  # 4BS teach-ins are never answered

    # window closed: the same teach-in now surfaces a discovery card
    dongle.inject(fourbs_teach_in_frame(NEW_SENSOR, 0x04, 0x01))
    await hass.async_block_till_done()
    assert (
        len(
            [
                flow
                for flow in hass.config_entries.flow.async_progress()
                if flow["handler"] == DOMAIN
            ]
        )
        == 1
    )
