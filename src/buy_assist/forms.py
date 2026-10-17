"""The item form: its string values for display, and validation into ItemFields."""

import re
from dataclasses import dataclass, field, fields
from decimal import Decimal

from buy_assist.models import Condition, ItemFields, is_upc, is_zip, optional_text

ZIP_ERROR = "ZIP must be 5 digits."
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
    variant: str = ""
    zip: str = ""
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
            variant=item_fields.variant or "",
            zip=item_fields.zip or "",
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
            variant=optional_text(self.variant),
            zip=optional_text(self.zip),
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


def _is_web_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))
