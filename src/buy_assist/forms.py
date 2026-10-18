"""The item form: its string values for display, and validation into ItemFields."""

import math
import re
from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal, InvalidOperation

from buy_assist.models import PACIFIC, Condition, ItemFields, Listing, is_upc, is_zip, optional_text

ZIP_ERROR = "ZIP must be 5 digits."
_BOX_SEPARATOR = re.compile(r"[\sx\u00d7,*]+", re.IGNORECASE)
_DOLLARS = re.compile(r"\$?\s*(\d+(?:\.\d{1,2})?)")


@dataclass(frozen=True)
class ItemForm:
    """Form values exactly as a person sees and types them."""

    name: str = ""
    condition: str = Condition.ANY.value
    upc: str = ""
    epid: str = ""
    model_number: str = ""
    reference_url: str = ""
    image_url: str = ""
    search_query: str = ""
    variant: str = ""
    zip: str = ""
    weight_lb: str = ""
    box_in: str = ""
    target_price: str = ""
    notes: str = ""
    watcher_ids: frozenset[int] = field(default_factory=frozenset[int])

    @classmethod
    def text_fields(cls) -> list[str]:
        return [f.name for f in fields(cls) if f.type is str]

    @classmethod
    def from_fields(cls, item_fields: ItemFields) -> ItemForm:
        return cls(
            name=item_fields.name,
            condition=item_fields.condition.value,
            upc=item_fields.upc or "",
            epid=item_fields.epid or "",
            model_number=item_fields.model_number or "",
            reference_url=item_fields.reference_url or "",
            image_url=item_fields.image_url or "",
            search_query=item_fields.search_query or "",
            variant=item_fields.variant or "",
            zip=item_fields.zip or "",
            weight_lb=format_pounds(item_fields.weight_oz),
            box_in=format_box(item_fields),
            target_price=format_dollars(item_fields.target_price_cents),
            notes=item_fields.notes,
            watcher_ids=item_fields.watcher_ids,
        )

    def validate(self, known_user_ids: set[int]) -> ItemFields | list[str]:
        """Return the fields to save, or the problems to show the person."""
        errors: list[str] = []
        if not self.name.strip():
            errors.append("Name is required.")
        if self.condition not in Condition:
            errors.append("Condition must be new, used or any.")
        if self.zip.strip() and not is_zip(self.zip.strip()):
            errors.append(ZIP_ERROR)
        if self.upc.strip() and not is_upc(self.upc.strip()):
            errors.append("UPC must be 8 to 14 digits.")
        url = self.reference_url.strip()
        if url and not _is_web_url(url):
            errors.append("Reference link must start with http:// or https://.")
        image_url = self.image_url.strip()
        if image_url and not _is_web_url(image_url):
            errors.append("Image link must start with http:// or https://.")
        target_price_cents = parse_dollars(self.target_price)
        if self.target_price.strip() and target_price_cents is None:
            errors.append("Target price must be a dollar amount, like 35 or 35.50.")
        weight_oz = parse_pounds(self.weight_lb)
        if self.weight_lb.strip() and weight_oz is None:
            errors.append("Weight must be a number of pounds, like 1.9.")
        box = parse_box(self.box_in)
        if self.box_in.strip() and box is None:
            errors.append("Box size must be three numbers of inches, like 17.8 x 12.9 x 2.1.")
        if not self.watcher_ids:
            errors.append("Pick at least one person to alert.")
        elif not self.watcher_ids <= known_user_ids:
            errors.append("Unknown watcher.")
        if errors:
            return errors
        return ItemFields(
            name=self.name.strip(),
            condition=Condition(self.condition),
            upc=optional_text(self.upc),
            epid=optional_text(self.epid),
            model_number=optional_text(self.model_number),
            reference_url=optional_text(url),
            image_url=optional_text(image_url),
            search_query=optional_text(self.search_query),
            variant=optional_text(self.variant),
            zip=optional_text(self.zip),
            weight_oz=weight_oz,
            length_in=box[0] if box else None,
            width_in=box[1] if box else None,
            depth_in=box[2] if box else None,
            target_price_cents=target_price_cents,
            notes=self.notes.strip(),
            watcher_ids=self.watcher_ids,
        )


def format_dollars(cents: int | None) -> str:
    return "" if cents is None else f"{cents // 100}.{cents % 100:02d}"


def parse_dollars(text: str) -> int | None:
    match = _DOLLARS.fullmatch(text.strip().replace(",", ""))
    if not match:
        return None
    return int(Decimal(match.group(1)) * 100)


def parse_pounds(text: str) -> int | None:
    """Pounds as typed, in whole ounces, or None if it isn't a positive weight."""
    try:
        pounds = Decimal(text.strip())
    except InvalidOperation:
        return None
    ounces = round(pounds * 16) if pounds.is_finite() else 0
    return ounces if ounces > 0 else None


def format_pounds(ounces: int | None) -> str:
    return "" if ounces is None else f"{ounces / 16:.2f}"


def parse_box(text: str) -> tuple[float, float, float] | None:
    """Three positive sizes in inches, like '17.8 x 12.9 x 2.1'. Between them can go x, a
    multiplication sign, commas or spaces."""
    parts = [p for p in _BOX_SEPARATOR.split(text.strip()) if p]
    try:
        sizes = [float(p) for p in parts]
    except ValueError:
        return None
    if len(sizes) != 3 or not all(math.isfinite(s) and s > 0 for s in sizes):
        return None
    return (sizes[0], sizes[1], sizes[2])


def format_box(fields: ItemFields, separator: str = " x ") -> str:
    """The box size as typed into the form."""
    sizes = (fields.length_in, fields.width_in, fields.depth_in)
    return separator.join(f"{s:g}" for s in sizes if s) if all(sizes) else ""


def describe_box(fields: ItemFields) -> str:
    """The box size for reading, to a tenth of an inch: '17.8 \u00d7 12.9 \u00d7 2.1'."""
    sizes = (fields.length_in, fields.width_in, fields.depth_in)
    return " \u00d7 ".join(f"{round(s, 1):g}" for s in sizes if s) if all(sizes) else ""


def describe_auction(listing: Listing) -> str:
    """'auction, 3 bids, ends Mon Oct 19, 8:15pm', in Pacific time; '' for Buy It Now."""
    if listing.auction_ends_at is None:
        return ""
    bids = listing.bid_count or 0
    ends = describe_time(listing.auction_ends_at)
    return f"auction, {bids} bid{'' if bids == 1 else 's'}, ends {ends}"


def describe_time(when: datetime) -> str:
    """'Mon Oct 19, 8:15pm', in Pacific time."""
    local = when.astimezone(PACIFIC)
    return f"{local:%a %b %-d}, {local:%-I:%M}{local:%p}".replace("AM", "am").replace("PM", "pm")


def _is_web_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))
