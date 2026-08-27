"""English and French translation files must exist and mirror each other."""

import json
from pathlib import Path

BASE = Path("custom_components/enocean_direct")


def _keys(node: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out |= _keys(value, path)
        else:
            out.add(path)
    return out


def test_translation_key_parity() -> None:
    strings = json.loads((BASE / "strings.json").read_text())
    english = json.loads((BASE / "translations/en.json").read_text())
    french = json.loads((BASE / "translations/fr.json").read_text())
    assert _keys(english) == _keys(strings)
    assert _keys(french) == _keys(strings)


def test_translations_cover_flow_strings() -> None:
    strings = json.loads((BASE / "strings.json").read_text())
    keys = _keys(strings)
    for required in (
        "config.step.user.data.device",
        "config.error.cannot_connect",
        "options.step.init.menu_options.inbox",
        "options.step.actuator.data.sender_id",
        "options.error.sender_out_of_range",
        "options.error.import_invalid",
        "device_automation.trigger_type.pressed",
        "device_automation.trigger_subtype.ai",
        "issues.serial_disconnected.title",
        "exceptions.command_not_acknowledged.message",
    ):
        assert required in keys, required


def test_placeholder_parity_between_languages() -> None:
    """A {placeholder} dropped or renamed in a translation renders as literal
    text (or raises) at runtime, which key parity alone does not catch."""
    import re

    english = json.loads((BASE / "translations/en.json").read_text())
    french = json.loads((BASE / "translations/fr.json").read_text())

    def walk(node: dict, other: dict, path: str = "") -> None:
        for key, value in node.items():
            where = f"{path}.{key}" if path else key
            if isinstance(value, dict):
                walk(value, other.get(key, {}), where)
            elif isinstance(value, str) and isinstance(other.get(key), str):
                assert set(re.findall(r"\{(\w+)\}", value)) == set(
                    re.findall(r"\{(\w+)\}", other[key])
                ), where

    walk(english, french)
