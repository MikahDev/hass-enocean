"""Device records and the import/export schema.

Validation never invents missing data: a record that lacks a required field or
carries a field that does not apply to its profile is rejected with an explicit
reason, so the caller can surface it to the user for manual correction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .const import (
    EEP_CHANNEL_COUNT,
    EEP_CONTACT,
    EEP_COVER,
    EEP_FAN,
    EEP_ROCKERS,
    EEP_SENSORS,
    EURID_MAX,
    IMPORT_SCHEMA_VERSION,
    KEY_ADDRESS,
    KEY_AREA,
    KEY_CHANNEL,
    KEY_EEP,
    KEY_NAME,
    KEY_SENDER_ID,
    SENDER_OFFSET_MAX,
    SUPPORTED_EEPS,
)

_HEX_DIGITS = frozenset("0123456789ABCDEF")


class AddressError(ValueError):
    """Raised when a radio address or sender ID cannot be parsed."""


def normalize_address(value: str) -> str:
    """Normalise a radio address to exactly eight uppercase hex digits.

    Accepts colon/dash/space separated forms and an optional 0x prefix.
    Rejects anything that is not exactly eight hex digits after cleanup:
    leading zeroes are significant and must be supplied, never invented.
    """
    if not isinstance(value, str):
        raise AddressError(f"not a string: {value!r}")
    cleaned = value.strip().upper()
    for sep in (":", "-", " "):
        cleaned = cleaned.replace(sep, "")
    cleaned = cleaned.removeprefix("0X")
    if len(cleaned) != 8 or not set(cleaned) <= _HEX_DIGITS:
        raise AddressError(f"invalid address {value!r}: expected 8 hex digits")
    return cleaned


def sender_in_base_range(sender: str, base_id: str) -> bool:
    """Return True if sender (8 hex digits) is within base_id .. base_id+127."""
    offset = int(sender, 16) - int(base_id, 16)
    return 0 <= offset <= SENDER_OFFSET_MAX


@dataclass(frozen=True)
class DeviceRecord:
    """One configured EnOcean device."""

    address: str  # 8 uppercase hex digits
    eep: str
    name: str
    sender_id: str | None = None  # actuators only
    channel: int | None = None  # actuators only, radio channel (0-based)
    area_id: str | None = None  # HA area, applied when the device is created

    @property
    def kind(self) -> str:
        if self.eep == EEP_CONTACT:
            return "contact"
        if self.eep in EEP_ROCKERS:
            return "rocker"
        if self.eep == EEP_COVER:
            return "cover"
        if self.eep == EEP_FAN:
            return "fan"
        if self.eep in EEP_SENSORS:
            return "sensor"
        return "actuator"

    @property
    def channel_number(self) -> int | None:
        """User-facing channel number (radio channel + 1)."""
        return None if self.channel is None else self.channel + 1

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            KEY_ADDRESS: self.address,
            KEY_EEP: self.eep,
            KEY_NAME: self.name,
        }
        if self.sender_id is not None:
            out[KEY_SENDER_ID] = self.sender_id
        if self.channel is not None:
            out[KEY_CHANNEL] = self.channel
        if self.area_id is not None:
            out[KEY_AREA] = self.area_id
        return out


def record_from_dict(raw: dict[str, Any]) -> DeviceRecord:
    """Build a DeviceRecord from stored options (already validated)."""
    return DeviceRecord(
        address=raw[KEY_ADDRESS],
        eep=raw[KEY_EEP],
        name=raw[KEY_NAME],
        sender_id=raw.get(KEY_SENDER_ID),
        channel=raw.get(KEY_CHANNEL),
        area_id=raw.get(KEY_AREA),
    )


def validate_record(
    raw: dict[str, Any],
    base_id: str | None,
    existing_addresses: set[str],
) -> tuple[DeviceRecord | None, list[str]]:
    """Validate one raw record. Returns (record, errors); record is None on error."""
    errors: list[str] = []

    try:
        address = normalize_address(raw.get(KEY_ADDRESS, ""))
    except AddressError as err:
        return None, [str(err)]

    if int(address, 16) > EURID_MAX:
        return None, [f"{address}: not a device address (EURIDs end at FF7FFFFF)"]

    if address in existing_addresses:
        errors.append(f"{address}: duplicate address")

    eep = str(raw.get(KEY_EEP, "")).strip().upper()
    if eep not in SUPPORTED_EEPS:
        errors.append(f"{address}: unsupported EEP {raw.get(KEY_EEP)!r}")
        return None, errors

    name = str(raw.get(KEY_NAME) or "").strip() or address

    area_id: str | None = None
    raw_area = raw.get(KEY_AREA)
    if raw_area is not None:
        if not isinstance(raw_area, str) or not raw_area.strip():
            errors.append(f"{address}: area_id must be a non-empty string")
        else:
            area_id = raw_area

    sender_id: str | None = None
    channel: int | None = None

    if eep in EEP_CHANNEL_COUNT:
        raw_sender = raw.get(KEY_SENDER_ID)
        if raw_sender is None:
            errors.append(f"{address}: sender_id is required for {eep}")
        else:
            try:
                sender_id = normalize_address(str(raw_sender))
            except AddressError as err:
                errors.append(f"{address}: {err}")
            else:
                if base_id is not None and not sender_in_base_range(sender_id, base_id):
                    errors.append(
                        f"{address}: sender_id {sender_id} outside valid range "
                        f"{base_id}..+{SENDER_OFFSET_MAX}"
                    )

        max_channel = EEP_CHANNEL_COUNT[eep] - 1
        raw_channel = raw.get(KEY_CHANNEL)
        if raw_channel is None:
            errors.append(f"{address}: channel is required for {eep}")
        elif (
            isinstance(raw_channel, bool)
            or not isinstance(raw_channel, int)
            or not 0 <= raw_channel <= max_channel
        ):
            errors.append(
                f"{address}: channel must be an integer 0..{max_channel} "
                f"for {eep}, got {raw_channel!r}"
            )
        else:
            channel = raw_channel
    else:
        for key in (KEY_SENDER_ID, KEY_CHANNEL):
            if raw.get(key) is not None:
                errors.append(f"{address}: {key} does not apply to {eep}")

    if errors:
        return None, errors
    return DeviceRecord(address, eep, name, sender_id, channel, area_id), []


@dataclass(frozen=True)
class ImportResult:
    records: list[DeviceRecord]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.records)


def parse_import(
    text: str, base_id: str | None, existing_addresses: set[str]
) -> ImportResult:
    """Parse and validate an import document. All-or-nothing: any error
    invalidates the whole import so nothing is partially persisted."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as err:
        return ImportResult([], [f"invalid JSON: {err}"])

    if not isinstance(doc, dict) or doc.get("version") != IMPORT_SCHEMA_VERSION:
        return ImportResult(
            [], [f'expected an object with "version": {IMPORT_SCHEMA_VERSION}']
        )
    raw_devices = doc.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        return ImportResult([], ['"devices" must be a non-empty list'])

    records: list[DeviceRecord] = []
    errors: list[str] = []
    seen = set(existing_addresses)
    for index, raw in enumerate(raw_devices):
        if not isinstance(raw, dict):
            errors.append(f"entry {index + 1}: not an object")
            continue
        record, record_errors = validate_record(raw, base_id, seen)
        errors.extend(record_errors)
        if record is not None:
            seen.add(record.address)
            records.append(record)

    if errors:
        return ImportResult([], errors)
    return ImportResult(records, [])


def build_export(records: list[DeviceRecord], base_id: str | None) -> str:
    """Build the export document. Contains recovery configuration, no secrets."""
    doc = {
        "version": IMPORT_SCHEMA_VERSION,
        "gateway": {"base_id": base_id},
        "devices": [record.as_dict() for record in records],
    }
    return json.dumps(doc, indent=2)
