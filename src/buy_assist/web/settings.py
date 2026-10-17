"""Household settings, and which reference-link sites have their own reader."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from buy_assist.forms import ZIP_ERROR
from buy_assist.links import DEDICATED_DOMAINS
from buy_assist.models import User, is_zip
from buy_assist.web.pages import Me, StoreDep, render, store_of

router = APIRouter()

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


def _settings_page(
    request: Request, me: User, home_zip: str, errors: list[str], status_code: int = 200
) -> HTMLResponse:
    counts = store_of(request).reference_domain_counts()
    domains = [
        ReferenceDomain(domain, count, DEDICATED_DOMAINS.get(domain))
        for domain, count in sorted(counts.items(), key=lambda dc: (-dc[1], dc[0]))
    ]
    return render(
        request,
        "settings.html",
        me,
        status_code=status_code,
        home_zip=home_zip,
        errors=errors,
        domains=domains,
    )


@router.get("/settings")
def settings(request: Request, store: StoreDep, me: Me) -> HTMLResponse:
    return _settings_page(request, me, store.home_zip() or "", [])


@router.post("/settings")
def save_settings(
    request: Request, store: StoreDep, me: Me, home_zip: Annotated[str, Form()]
) -> Response:
    if not is_zip(home_zip.strip()):
        return _settings_page(request, me, home_zip, [ZIP_ERROR], status_code=422)
    store.set_home_zip(home_zip.strip())
    return RedirectResponse("/settings", status_code=303)
