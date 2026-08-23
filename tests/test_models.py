"""Address normalisation, record validation, import/export schema."""

import json

import pytest

from custom_components.enocean_direct.models import (
    AddressError,
    DeviceRecord,
    build_export,
    normalize_address,
    parse_import,
    sender_in_base_range,
    validate_record,
)

BASE = "FF974100"


# ---------------------------------------------------------------- address
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("050A5C20", "050A5C20"),
        ("05:0a:5c:20", "050A5C20"),
        ("0x050A5C20", "050A5C20"),
        ("00-84-AC-F3", "0084ACF3"),
        (" 0084ACF3 ", "0084ACF3"),
    ],
)
def test_normalize_address(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["84ACF3", "1234567", "123456789", "GGGGGGGG", "", "05 0A 5C 2"],
)
def test_normalize_address_rejects(raw: str) -> None:
    with pytest.raises(AddressError):
        normalize_address(raw)


def test_leading_zero_preserved() -> None:
    assert normalize_address("0084ACF3") == "0084ACF3"
    # 6-digit shorthand must NOT be zero-padded silently.
    with pytest.raises(AddressError):
        normalize_address("84ACF3")


# ---------------------------------------------------------------- sender
def test_sender_range() -> None:
    assert sender_in_base_range(BASE, BASE)  # offset 0
    assert sender_in_base_range("FF97417F", BASE)  # offset 127
    assert not sender_in_base_range("FF974180", BASE)  # offset 128
    assert not sender_in_base_range("FF9740FF", BASE)  # below base


# ---------------------------------------------------------------- records
def test_validate_contact() -> None:
    record, errors = validate_record(
        {"address": "00:84:AC:F3", "eep": "D5-00-01", "name": "Door"}, BASE, set()
    )
    assert errors == []
    assert record == DeviceRecord("0084ACF3", "D5-00-01", "Door")
    assert record.kind == "contact"


def test_validate_actuator_requires_sender_and_channel() -> None:
    record, errors = validate_record(
        {"address": "050A5C20", "eep": "D2-01-0F", "name": "Relay"}, BASE, set()
    )
    assert record is None
    assert any("sender_id is required" in error for error in errors)
    assert any("channel is required" in error for error in errors)


def test_validate_actuator_ok() -> None:
    record, errors = validate_record(
        {
            "address": "050A5C20",
            "eep": "D2-01-0F",
            "name": "Relay",
            "sender_id": "FF974100",
            "channel": 0,
        },
        BASE,
        set(),
    )
    assert errors == []
    assert record.sender_id == "FF974100"
    assert record.channel == 0
    assert record.channel_number == 1


def test_validate_actuator_sender_out_of_range() -> None:
    record, errors = validate_record(
        {
            "address": "050A5C20",
            "eep": "D2-01-0F",
            "name": "Relay",
            "sender_id": "FF974180",
            "channel": 0,
        },
        BASE,
        set(),
    )
    assert record is None
    assert any("outside valid range" in error for error in errors)


def test_validate_contact_rejects_sender() -> None:
    record, errors = validate_record(
        {
            "address": "0084ACF3",
            "eep": "D5-00-01",
            "name": "Door",
            "sender_id": "FF974100",
        },
        BASE,
        set(),
    )
    assert record is None
    assert any("does not apply" in error for error in errors)


def test_validate_rejects_non_eurid_device_address() -> None:
    # FF900000 is in the base/sender range: valid sender, invalid device
    record, errors = validate_record(
        {"address": "FF900000", "eep": "D5-00-01"}, BASE, set()
    )
    assert record is None
    assert any("not a device address" in error for error in errors)


def test_validate_unknown_eep_and_duplicate() -> None:
    record, errors = validate_record(
        {"address": "0084ACF3", "eep": "A5-02-05"}, BASE, set()
    )
    assert record is None and any("unsupported EEP" in error for error in errors)

    record, errors = validate_record(
        {"address": "0084ACF3", "eep": "D5-00-01"}, BASE, {"0084ACF3"}
    )
    assert record is None and any("duplicate" in error for error in errors)


def test_validate_channel_bounds() -> None:
    # D2-01-0F is single-channel: only radio channel 0 is valid
    for bad in (-1, 1, 30, "0", True):
        record, errors = validate_record(
            {
                "address": "050A5C20",
                "eep": "D2-01-0F",
                "sender_id": BASE,
                "channel": bad,
            },
            BASE,
            set(),
        )
        assert record is None, bad


# ---------------------------------------------------------------- import
def _doc(devices: list[dict]) -> str:
    return json.dumps({"version": 1, "devices": devices})


def test_import_valid() -> None:
    result = parse_import(
        _doc(
            [
                {"address": "0084ACF3", "eep": "D5-00-01", "name": "Door"},
                {
                    "address": "050A5C20",
                    "eep": "D2-01-0F",
                    "name": "Relay",
                    "sender_id": "FF974100",
                    "channel": 0,
                },
            ]
        ),
        BASE,
        set(),
    )
    assert result.ok
    assert [record.address for record in result.records] == ["0084ACF3", "050A5C20"]


def test_import_all_or_nothing() -> None:
    result = parse_import(
        _doc(
            [
                {"address": "0084ACF3", "eep": "D5-00-01"},
                {"address": "050A5C20", "eep": "D2-01-0F"},  # missing sender/channel
            ]
        ),
        BASE,
        set(),
    )
    assert not result.ok
    assert result.records == []  # nothing to persist on any error


def test_import_duplicates() -> None:
    duplicate_in_file = parse_import(
        _doc(
            [
                {"address": "0084ACF3", "eep": "D5-00-01"},
                {"address": "00:84:AC:F3", "eep": "D5-00-01"},
            ]
        ),
        BASE,
        set(),
    )
    assert not duplicate_in_file.ok

    duplicate_vs_existing = parse_import(
        _doc([{"address": "0084ACF3", "eep": "D5-00-01"}]), BASE, {"0084ACF3"}
    )
    assert not duplicate_vs_existing.ok


def test_import_bad_documents() -> None:
    assert not parse_import("not json", BASE, set()).ok
    assert not parse_import("{}", BASE, set()).ok
    assert not parse_import('{"version": 2, "devices": []}', BASE, set()).ok
    assert not parse_import('{"version": 1, "devices": []}', BASE, set()).ok
    assert not parse_import('{"version": 1, "devices": ["x"]}', BASE, set()).ok


# ---------------------------------------------------------------- export
def test_export_roundtrip() -> None:
    records = [
        DeviceRecord("0084ACF3", "D5-00-01", "Door"),
        DeviceRecord("050A5C20", "D2-01-0F", "Relay", "FF974100", 0),
    ]
    exported = build_export(records, BASE)
    doc = json.loads(exported)
    assert doc["gateway"]["base_id"] == BASE
    assert "token" not in exported and "password" not in exported
    result = parse_import(exported, BASE, set())
    assert result.ok
    assert result.records == records
