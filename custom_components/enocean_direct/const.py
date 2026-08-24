"""Constants for the EnOcean Direct integration."""

from homeassistant.const import Platform

DOMAIN = "enocean_direct"

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SWITCH]

CONF_DEVICE_PATH = "device"
CONF_BASE_ID = "base_id"
CONF_DEVICES = "devices"

KEY_ADDRESS = "address"
KEY_EEP = "eep"
KEY_NAME = "name"
KEY_SENDER_ID = "sender_id"
KEY_CHANNEL = "channel"
KEY_AREA = "area_id"

EEP_CONTACT = "D5-00-01"
EEP_ROCKERS = ("F6-02-01", "F6-02-02")
EEP_ACTUATOR = "D2-01-0F"
SUPPORTED_EEPS = (EEP_CONTACT, *EEP_ROCKERS, EEP_ACTUATOR)

# Radio channels per supported actuator EEP (D2-01-0F is single-channel).
EEP_CHANNEL_COUNT = {EEP_ACTUATOR: 1}

# Device (radio) addresses must be EURIDs; base-range addresses are senders.
EURID_MAX = 0xFF7FFFFF

# Sender IDs must be within base ID .. base ID + 127.
SENDER_OFFSET_MAX = 127

INBOX_MAX_ENTRIES = 128

IMPORT_SCHEMA_VERSION = 1

SIGNAL_CONNECTION = f"{DOMAIN}_connection_{{}}"  # .format(entry_id)
SIGNAL_CONTACT = f"{DOMAIN}_contact_{{}}"  # .format(address)
SIGNAL_SWITCH_STATE = f"{DOMAIN}_switch_{{}}"  # .format(address)

EVENT_BUTTON = f"{DOMAIN}_event"

ISSUE_SERIAL_DISCONNECTED = "serial_disconnected"
