"""Local EEP decoders for profiles handled by this integration.

Field layouts transcribed from the public EnOcean Alliance EEP specification.
D2-01 encoding/decoding is done by the enocean-async library and is not here.

D5-00-01 (1BS single input contact), data byte DB0:
  bit 3 (LRN): 0 = teach-in telegram, 1 = data telegram
  bit 0 (CO):  0 = contact open, 1 = contact closed
So DB0 0x08 = open, 0x09 = closed, 0x00 = teach-in.

F6-02-01 / F6-02-02 (2-rocker switch), data byte DB0 with status NU bit set:
  bits 7-5 (R1): first action, 0=AI 1=AO 2=BI 3=BO
  bit 4 (EB):    energy bow, 1 = pressed
  bits 3-1 (R2): second action button
  bit 0 (SA):    1 = second action valid
With NU clear and EB clear the telegram is the energy-bow release.
Status byte: bit 5 = T21, bit 4 = NU.
"""

from __future__ import annotations

from dataclasses import dataclass

BUTTONS = {0: "ai", 1: "ao", 2: "bi", 3: "bo"}


@dataclass(frozen=True)
class D5Reading:
    is_open: bool
    is_teach_in: bool


def decode_d5(telegram_data: bytes) -> D5Reading | None:
    """Decode a D5-00-01 data byte. Returns None for malformed payloads."""
    if len(telegram_data) != 1:
        return None
    db0 = telegram_data[0]
    return D5Reading(is_open=(db0 & 0x01) == 0, is_teach_in=(db0 & 0x08) == 0)


@dataclass(frozen=True)
class F6Action:
    action: str  # "pressed" or "released"
    button: str | None  # ai/ao/bi/bo, None for release or multi-press
    second_button: str | None = None


def decode_f6(telegram_data: bytes, status: int) -> F6Action | None:
    """Decode an F6-02-01/02 rocker telegram. Returns None if not decodable."""
    if len(telegram_data) != 1:
        return None
    db0 = telegram_data[0]
    nu = (status >> 4) & 0x01
    energy_bow = bool(db0 & 0x10)

    if nu:
        r1 = (db0 >> 5) & 0x07
        button = BUTTONS.get(r1)
        if button is None or not energy_bow:
            # R1 values above 3 are not defined for F6-02; NU without EB
            # has no defined meaning either.
            return None
        second = BUTTONS.get((db0 >> 1) & 0x07) if db0 & 0x01 else None
        return F6Action("pressed", button, second)

    if energy_bow:
        # "3 or 4 buttons pressed": a press with no identifiable button.
        return F6Action("pressed", None)
    if db0 == 0x00:
        return F6Action("released", None)
    return None
