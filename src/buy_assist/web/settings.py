"""Household settings, and which reference-link sites have their own reader."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from buy_assist.domains import reference_domains
from buy_assist.forms import ZIP_ERROR
from buy_assist.models import User, is_zip
from buy_assist.web.pages import Me, StoreDep, render, store_of

router = APIRouter()


def _settings_page(
    request: Request, me: User, home_zip: str, errors: list[str], status_code: int = 200
) -> HTMLResponse:
    domains = reference_domains(store_of(request))
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
