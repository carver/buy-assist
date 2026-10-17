"""Which reference-link sites items point at, and which deserve a dedicated reader."""

from dataclasses import dataclass

from buy_assist.links import DEDICATED_DOMAINS
from buy_assist.store import Store

READER_WORTHWHILE_ITEMS = 3
"""Items linking to one domain before a dedicated reader for it is worth writing."""


@dataclass(frozen=True)
class ReferenceDomain:
    domain: str
    item_count: int
    reader: str | None

    @property
    def wants_reader(self) -> bool:
        """Enough items link here that a dedicated reader would pay off."""
        return self.reader is None and self.item_count >= READER_WORTHWHILE_ITEMS


def reference_domains(store: Store) -> list[ReferenceDomain]:
    """Every domain items link to, most-used first."""
    counts = store.reference_domain_counts()
    return [
        ReferenceDomain(domain, count, DEDICATED_DOMAINS.get(domain))
        for domain, count in sorted(counts.items(), key=lambda dc: (-dc[1], dc[0]))
    ]
