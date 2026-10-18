"""Times written for people: in their time zone, as a short phrase."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from buy_assist.models import PACIFIC, Listing


@dataclass(frozen=True)
class LocalTime:
    """Writes times as seen in one time zone, each followed by `label` when there is one."""

    zone: ZoneInfo = PACIFIC
    label: str = ""

    @classmethod
    def named(cls, name: str | None) -> LocalTime:
        """The IANA zone `name`, or Pacific when it's missing or unknown."""
        try:
            return cls(ZoneInfo(name)) if name else cls()
        except ValueError, ZoneInfoNotFoundError:
            return cls()

    def time(self, when: datetime) -> str:
        """'Mon Oct 19, 8:15pm'."""
        local = when.astimezone(self.zone)
        hour = f"{local:%-I:%M}{local:%p}".lower()
        return f"{local:%a %b %-d}, {hour}{' ' + self.label if self.label else ''}"

    def day(self, when: datetime) -> str:
        """'Oct 19'."""
        return f"{when.astimezone(self.zone):%b %-d}"

    def date(self, when: datetime) -> str:
        """'Oct 19, 2026'."""
        return f"{when.astimezone(self.zone):%b %-d, %Y}"

    def auction(self, listing: Listing) -> str:
        """'auction, 3 bids, ends Mon Oct 19, 8:15pm'; '' for Buy It Now."""
        if listing.auction_ends_at is None:
            return ""
        bids = listing.bid_count or 0
        ends = self.time(listing.auction_ends_at)
        return f"auction, {bids} bid{'' if bids == 1 else 's'}, ends {ends}"
