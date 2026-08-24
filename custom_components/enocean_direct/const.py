"""Constants for the EnOcean Direct integration."""

from homeassistant.const import Platform

DOMAIN = "enocean_direct"

PLATFORMS = [Platform.BINARY_SENSOR, Platform.COVER, Platform.SENSOR, Platform.SWITCH]

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
EEP_COVER = "D2-05-00"

# Receive-only sensor profiles (wave 2): every type of these families that the
# enocean-async library decodes. No sender ID, no channel, no transmission.
EEP_SENSORS = (
    "A5-02-01", "A5-02-02", "A5-02-03", "A5-02-04", "A5-02-05", "A5-02-06",
    "A5-02-07", "A5-02-08", "A5-02-09", "A5-02-0A", "A5-02-0B", "A5-02-10",
    "A5-02-11", "A5-02-12", "A5-02-13", "A5-02-14", "A5-02-15", "A5-02-16",
    "A5-02-17", "A5-02-18", "A5-02-19", "A5-02-1A", "A5-02-1B", "A5-02-20",
    "A5-02-30", "A5-04-01", "A5-04-02", "A5-04-03", "A5-06-01", "A5-06-02",
    "A5-06-03", "A5-06-04", "A5-06-05", "A5-07-03", "A5-08-01", "A5-08-02",
    "A5-08-03", "A5-10-01", "A5-10-02", "A5-10-03", "A5-10-04", "A5-10-05",
    "A5-10-06", "A5-10-07", "A5-10-08", "A5-10-09", "A5-10-0A", "A5-10-0B",
    "A5-10-0C", "A5-10-0D", "A5-10-10", "A5-10-11", "A5-10-12", "A5-10-13",
    "A5-10-14", "A5-10-15", "A5-10-16", "A5-10-17", "A5-10-18", "A5-10-19",
    "A5-10-1A", "A5-10-1B", "A5-10-1C", "A5-10-1D", "A5-10-1E", "A5-10-1F",
    "A5-10-20", "A5-10-21", "A5-10-22", "A5-10-23", "A5-12-00", "A5-12-01",
    "A5-12-02", "A5-12-03", "F6-10-00",
)  # fmt: skip

SUPPORTED_EEPS = (EEP_CONTACT, *EEP_ROCKERS, EEP_ACTUATOR, EEP_COVER, *EEP_SENSORS)

# Room panels whose DB0 carries a battery OK/low flag (decoded locally).
EEP_BATTERY_FLAG = ("A5-10-20", "A5-10-21")

# Radio channels per transmitting EEP (both are single-channel). Membership
# doubles as "needs a sender ID and a channel".
EEP_CHANNEL_COUNT = {EEP_ACTUATOR: 1, EEP_COVER: 1}

# Device (radio) addresses must be EURIDs; base-range addresses are senders.
EURID_MAX = 0xFF7FFFFF

# Sender IDs must be within base ID .. base ID + 127.
SENDER_OFFSET_MAX = 127

# Guided pairing: how long the focused learning window stays open (seconds).
PAIRING_TIMEOUT = 60

INBOX_MAX_ENTRIES = 128

IMPORT_SCHEMA_VERSION = 1

SIGNAL_CONNECTION = f"{DOMAIN}_connection_{{}}"  # .format(entry_id)
SIGNAL_CONTACT = f"{DOMAIN}_contact_{{}}"  # .format(address)
SIGNAL_SWITCH_STATE = f"{DOMAIN}_switch_{{}}"  # .format(address)
SIGNAL_COVER_STATE = f"{DOMAIN}_cover_{{}}"  # .format(address)
# Every telegram received from a configured device: (rssi_dbm, utc datetime).
SIGNAL_TELEGRAM = f"{DOMAIN}_telegram_{{}}"  # .format(address)
# Decoded values from a sensor-kind device: (entity_id, {observable: value}).
SIGNAL_SENSOR = f"{DOMAIN}_sensor_{{}}"  # .format(address)
# Battery low flag from an EEP_BATTERY_FLAG device: (is_low,).
SIGNAL_BATTERY = f"{DOMAIN}_battery_{{}}"  # .format(address)

EVENT_BUTTON = f"{DOMAIN}_event"

ISSUE_SERIAL_DISCONNECTED = "serial_disconnected"
