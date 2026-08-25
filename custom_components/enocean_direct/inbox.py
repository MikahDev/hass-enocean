"""Bounded in-memory radio inbox of recently heard senders."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.util import dt as dt_util

from .const import INBOX_MAX_ENTRIES


@dataclass
class InboxEntry:
    address: str  # 8 uppercase hex digits
    rorg: int
    telegram_type: str  # e.g. RPS, 1BS, 4BS, VLD, UTE
    configured: bool
    eep: str | None = None  # only when declared by a valid teach-in
    rssi_dbm: int | None = None
    first_seen: datetime = field(default_factory=dt_util.utcnow)
    last_seen: datetime = field(default_factory=dt_util.utcnow)
    count: int = 0

    def as_dict(self) -> dict:
        return {
            "address": self.address,
            "rorg": f"{self.rorg:02X}",
            "telegram_type": self.telegram_type,
            "eep": self.eep,
            "rssi_dbm": self.rssi_dbm,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "count": self.count,
            "configured": self.configured,
        }


class RadioInbox:
    """LRU-bounded map of sender address to InboxEntry."""

    def __init__(self, max_entries: int = INBOX_MAX_ENTRIES) -> None:
        self._max = max_entries
        self._entries: OrderedDict[str, InboxEntry] = OrderedDict()

    def record(
        self,
        address: str,
        rorg: int,
        telegram_type: str,
        configured: bool,
        rssi_dbm: int | None = None,
        declared_eep: str | None = None,
    ) -> InboxEntry:
        entry = self._entries.get(address)
        if entry is None:
            entry = InboxEntry(
                address=address,
                rorg=rorg,
                telegram_type=telegram_type,
                configured=configured,
            )
            self._entries[address] = entry
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        entry.rorg = rorg
        entry.telegram_type = telegram_type
        entry.configured = configured
        entry.last_seen = dt_util.utcnow()
        entry.count += 1
        if rssi_dbm is not None:
            entry.rssi_dbm = rssi_dbm
        if declared_eep is not None:
            entry.eep = declared_eep
        self._entries.move_to_end(address)
        return entry

    def mark_configured(self, address: str) -> None:
        if address in self._entries:
            self._entries[address].configured = True

    def sync_configured(self, configured_addresses: set[str]) -> None:
        """Align every entry's configured flag with the given device set."""
        for entry in self._entries.values():
            entry.configured = entry.address in configured_addresses

    @property
    def entries(self) -> list[InboxEntry]:
        """Entries, most recently heard first."""
        return list(reversed(self._entries.values()))

    def unconfigured(self) -> list[InboxEntry]:
        return [entry for entry in self.entries if not entry.configured]

    def get(self, address: str) -> InboxEntry | None:
        return self._entries.get(address)
