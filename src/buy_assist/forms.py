"""The item form: its string values for display, and validation into ItemFields."""

import re
from dataclasses import dataclass, field, fields
from decimal import Decimal, InvalidOperation

from buy_assist.models import Condition, Item, ItemDraft, ItemFields

_ZIP = re.compile(r"\d{5}")
_UPC = re.compile(r"\d{8,14}")
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
    variant: str = ""
    zip: str = ""
    target_price: str = ""
    notes: str = ""
    watcher_ids: frozenset[int] = field(default_factory=frozenset[int])

    @classmethod
    def text_fields(cls) -> list[str]:
        return [f.name for f in fields(cls) if f.type is str]

    @classmethod
    def from_draft(cls, draft: ItemDraft, *, watcher_id: int) -> ItemForm:
        return cls(
            name=draft.name,
            condition=draft.condition.value,
            upc=draft.upc or "",
            epid=draft.epid or "",
            model_number=draft.model_number or "",
            reference_url=draft.reference_url or "",
            variant=draft.variant or "",
            target_price=format_dollars(draft.target_price_cents),
            notes=draft.notes,
            watcher_ids=frozenset({watcher_id}),
        )

    @classmethod
    def from_item(cls, item: Item) -> ItemForm:
        item_fields = item.fields
        return cls(
            name=item_fields.name,
            condition=item_fields.condition.value,
            upc=item_fields.upc or "",
            epid=item_fields.epid or "",
            model_number=item_fields.model_number or "",
            reference_url=item_fields.reference_url or "",
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
        if self.zip.strip() and not _ZIP.fullmatch(self.zip.strip()):
            errors.append("ZIP must be 5 digits.")
        if self.upc.strip() and not _UPC.fullmatch(self.upc.strip()):
            errors.append("UPC must be 8 to 14 digits.")
        url = self.reference_url.strip()
        if url and not url.startswith(("http://", "https://")):
            errors.append("Reference link must start with http:// or https://.")
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
            upc=_blank_to_none(self.upc),
            epid=_blank_to_none(self.epid),
            model_number=_blank_to_none(self.model_number),
            reference_url=_blank_to_none(url),
            variant=_blank_to_none(self.variant),
            zip=_blank_to_none(self.zip),
            target_price_cents=target_price_cents,
            notes=self.notes.strip(),
            watcher_ids=self.watcher_ids,
        )


def is_zip(text: str) -> bool:
    return bool(_ZIP.fullmatch(text))


def format_dollars(cents: int | None) -> str:
    return "" if cents is None else f"{cents // 100}.{cents % 100:02d}"


def parse_dollars(text: str) -> int | None:
    match = _DOLLARS.fullmatch(text.strip().replace(",", ""))
    if not match:
        return None
    try:
        return int(Decimal(match.group(1)) * 100)
    except InvalidOperation:
        return None


def _blank_to_none(text: str) -> str | None:
    return text.strip() or None
