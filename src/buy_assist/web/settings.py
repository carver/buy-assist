"""Household settings: home ZIP, email addresses, the app's address, tax rates, and which
reference-link sites have their own reader."""

import re
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from buy_assist.domains import reference_domains
from buy_assist.forms import ZIP_ERROR
from buy_assist.models import User, is_zip
from buy_assist.web.pages import Me, StoreDep, render, store_of

router = APIRouter()

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_MAX_TAX_PERCENT = Decimal(25)


def _settings_page(
    request: Request, me: User, errors: list[str] | None = None, *, home_zip: str | None = None
) -> HTMLResponse:
    store = store_of(request)
    return render(
        request,
        "settings.html",
        me,
        status_code=422 if errors else 200,
        home_zip=store.home_zip() or "" if home_zip is None else home_zip,
        app_address=store.app_address() or "",
        tax_rates={zip_code: rate * 100 for zip_code, rate in store.tax_rates().items()},
        errors=errors or [],
        domains=reference_domains(store),
    )


def _saved() -> Response:
    return RedirectResponse("/settings", status_code=303)


@router.get("/settings")
def settings(request: Request, me: Me) -> HTMLResponse:
    return _settings_page(request, me)


@router.post("/settings")
def save_home_zip(
    request: Request, store: StoreDep, me: Me, home_zip: Annotated[str, Form()]
) -> Response:
    if not is_zip(home_zip.strip()):
        return _settings_page(request, me, [ZIP_ERROR], home_zip=home_zip)
    store.set_home_zip(home_zip.strip())
    return _saved()


@router.post("/settings/people")
async def save_emails(request: Request, store: StoreDep, me: Me) -> Response:
    form = await request.form()
    emails: dict[int, str | None] = {}
    for user in store.users():
        email = str(form.get(f"email_{user.id}", user.email or "")).strip()
        if email and not _EMAIL.fullmatch(email):
            return _settings_page(request, me, [f"{email} doesn't look like an email address."])
        emails[user.id] = email or None
    for user_id, email in emails.items():
        store.set_email(user_id, email)
    return _saved()


@router.post("/settings/app")
def save_app_address(
    request: Request, store: StoreDep, me: Me, app_address: Annotated[str, Form()] = ""
) -> Response:
    address = app_address.strip()
    if address and not address.startswith(("http://", "https://")):
        return _settings_page(request, me, ["The app address must start with http:// or https://."])
    store.set_app_address(address or None)
    return _saved()


@router.post("/settings/tax")
def save_tax_rate(
    request: Request,
    store: StoreDep,
    me: Me,
    zip: Annotated[str, Form()],
    rate_percent: Annotated[str, Form()] = "",
) -> Response:
    zip_code = zip.strip()
    if not is_zip(zip_code):
        return _settings_page(request, me, [ZIP_ERROR])
    if not rate_percent.strip():
        store.set_tax_rate(zip_code, None)
        return _saved()
    try:
        percent = Decimal(rate_percent.strip().rstrip("%"))
    except InvalidOperation:
        percent = Decimal(-1)
    if not (percent.is_finite() and 0 <= percent < _MAX_TAX_PERCENT):
        return _settings_page(request, me, ["Tax rate must be a percentage, like 8.625."])
    store.set_tax_rate(zip_code, percent / 100)
    return _saved()
