"""D5-00-01 and F6-02-x decoders against spec fixtures."""

from custom_components.enocean_direct.profiles import decode_d5, decode_f6


# ---------------------------------------------------------------- D5-00-01
def test_d5_open() -> None:
    reading = decode_d5(bytes([0x08]))
    assert reading.is_open is True
    assert reading.is_teach_in is False


def test_d5_closed() -> None:
    reading = decode_d5(bytes([0x09]))
    assert reading.is_open is False
    assert reading.is_teach_in is False


def test_d5_teach_in() -> None:
    reading = decode_d5(bytes([0x00]))
    assert reading.is_teach_in is True


def test_d5_malformed() -> None:
    assert decode_d5(b"") is None
    assert decode_d5(bytes([0x08, 0x00])) is None


# ---------------------------------------------------------------- F6-02-x
PRESS_STATUS = 0x30  # T21 + NU
RELEASE_STATUS = 0x20  # T21


def test_f6_presses() -> None:
    # R1 in bits 7-5, EB bit 4: 0=AI 1=AO 2=BI 3=BO
    for db0, button in ((0x10, "ai"), (0x30, "ao"), (0x50, "bi"), (0x70, "bo")):
        action = decode_f6(bytes([db0]), PRESS_STATUS)
        assert action.action == "pressed"
        assert action.button == button
        assert action.second_button is None


def test_f6_release() -> None:
    action = decode_f6(bytes([0x00]), RELEASE_STATUS)
    assert action.action == "released"
    assert action.button is None


def test_f6_second_action() -> None:
    # AO pressed with BO as valid second action: R1=1 EB=1 R2=3 SA=1
    db0 = (1 << 5) | 0x10 | (3 << 1) | 0x01
    action = decode_f6(bytes([db0]), PRESS_STATUS)
    assert action.action == "pressed"
    assert action.button == "ao"
    assert action.second_button == "bo"


def test_f6_multi_press() -> None:
    # NU=0 with energy bow set: "3 or 4 buttons" press, no single button.
    action = decode_f6(bytes([0x70]), RELEASE_STATUS)
    assert action.action == "pressed"
    assert action.button is None


def test_f6_malformed() -> None:
    assert decode_f6(b"", PRESS_STATUS) is None
    assert decode_f6(bytes([0x10, 0x00]), PRESS_STATUS) is None
    # NU set but energy bow released: undefined for F6-02
    assert decode_f6(bytes([0x00]), PRESS_STATUS) is None
