"""Items: who's viewing, the item list, drafting from free text, and the item form."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import TypeAdapter, ValidationError

from buy_assist.claude import ParseError
from buy_assist.forms import ItemForm
from buy_assist.links import LinkError, first_url
from buy_assist.models import Edition, ItemFields, User, url_domain
from buy_assist.parser import draft_from_link
from buy_assist.web.pages import (
    ExistingItem,
    Me,
    ServicesDep,
    StoreDep,
    item_page,
    render,
    store_of,
)
from buy_assist.web.sales import record_sales_quietly

router = APIRouter()
_USER_COOKIE = "user_id"
_ONE_YEAR_SECONDS = 365 * 24 * 3600


async def _submitted_item_form(request: Request) -> ItemForm:
    data = await request.form()
    values = {name: str(data.get(name, "")) for name in ItemForm.text_fields()}
    return ItemForm(**values, watcher_ids=_ids(data.getlist("watcher_ids")))


SubmittedForm = Annotated[ItemForm, Depends(_submitted_item_form)]

_EDITIONS = TypeAdapter(tuple[Edition, ...])


async def _submitted_editions(request: Request) -> tuple[Edition, ...]:
    """The editions the draft form offered, carried along so a failed save can offer them
    again. It's display data from the person's own browser, so a bad value just drops it."""
    editions = (await request.form()).get("editions")
    if not isinstance(editions, str) or not editions:
        return ()
    try:
        return _EDITIONS.validate_json(editions)
    except ValidationError:
        return ()


SubmittedEditions = Annotated[tuple[Edition, ...], Depends(_submitted_editions)]


def _ids(values: Sequence[object]) -> frozenset[int]:
    """Parse submitted ids, dropping anything that isn't a number."""
    return frozenset(int(v) for v in values if isinstance(v, str) and v.isdecimal())


def _item_form_page(
    request: Request,
    me: User,
    form: ItemForm,
    *,
    item_id: int | None,
    errors: list[str] | None = None,
    messages: list[str] | None = None,
    editions: tuple[Edition, ...] = (),
) -> HTMLResponse:
    return render(
        request,
        "item_form.html",
        me,
        status_code=422 if errors else 200,
        form=form,
        item_id=item_id,
        errors=errors or [],
        messages=messages or [],
        editions=editions,
        editions_json=_EDITIONS.dump_json(editions).decode() if editions else "",
        home_zip=store_of(request).home_zip(),
    )


def _validated(
    request: Request, me: User, form: ItemForm, item_id: int | None, editions: tuple[Edition, ...]
) -> ItemFields | HTMLResponse:
    """The fields to save, or the form shown again with what's wrong."""
    result = form.validate({u.id for u in store_of(request).users()})
    if isinstance(result, list):
        return _item_form_page(request, me, form, item_id=item_id, errors=result, editions=editions)
    return result


@router.get("/whoami")
def whoami(request: Request) -> HTMLResponse:
    return render(request, "whoami.html", None)


@router.post("/whoami")
def pick_identity(store: StoreDep, user_id: Annotated[int, Form()]) -> Response:
    if user_id not in {u.id for u in store.users()}:
        raise HTTPException(400, "Unknown user")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        _USER_COOKIE, str(user_id), max_age=_ONE_YEAR_SECONDS, httponly=True, samesite="lax"
    )
    return response


@router.get("/")
def item_list(request: Request, store: StoreDep, me: Me, closed: bool = False) -> HTMLResponse:
    items = store.list_items(open_items=not closed)
    return render(request, "items.html", me, items=items, closed=closed)


@router.get("/items/new")
def new_item(request: Request, me: Me) -> HTMLResponse:
    return render(request, "new_item.html", me)


@router.post("/items/draft")
def draft_item(
    request: Request, services: ServicesDep, me: Me, text: Annotated[str, Form()]
) -> HTMLResponse:
    messages: list[str] = []
    link = None
    if url := first_url(text):
        try:
            link = services.links.read(url)
            messages.append(f"Read from {link.source}.")
        except LinkError as e:
            site = url_domain(url) or url
            messages.append(f"Couldn't read {site}: {e} Details come from your text.")
    try:
        draft = services.parser.parse(text, link)
    except ParseError:
        draft = draft_from_link(text, link)
        messages.append("Claude couldn't read your note, so fill in the rest by hand.")
    form = ItemForm.from_fields(replace(draft, watcher_ids=frozenset({me.id})))
    editions = link.editions if link else ()
    return _item_form_page(request, me, form, item_id=None, messages=messages, editions=editions)


@router.post("/items")
def create_item(
    request: Request,
    store: StoreDep,
    services: ServicesDep,
    me: Me,
    form: SubmittedForm,
    editions: SubmittedEditions,
    background: BackgroundTasks,
) -> Response:
    fields = _validated(request, me, form, None, editions)
    if isinstance(fields, HTMLResponse):
        return fields
    item = store.create_item(fields)
    if services.sold_prices.covers(fields):
        background.add_task(record_sales_quietly, store, services.sold_prices, item)
    return RedirectResponse(f"/items/{item.id}", status_code=303)


@router.get("/items/{item_id}")
def show_item(request: Request, me: Me, item: ExistingItem) -> HTMLResponse:
    return item_page(request, me, item)


@router.get("/items/{item_id}/edit")
def edit_item(request: Request, me: Me, item: ExistingItem) -> HTMLResponse:
    return _item_form_page(request, me, ItemForm.from_fields(item.fields), item_id=item.id)


@router.post("/items/{item_id}")
def update_item(
    request: Request, store: StoreDep, me: Me, item: ExistingItem, form: SubmittedForm
) -> Response:
    fields = _validated(request, me, form, item.id, ())
    if isinstance(fields, HTMLResponse):
        return fields
    store.update_item(item.id, fields)
    return RedirectResponse(f"/items/{item.id}", status_code=303)


@router.post("/items/{item_id}/close")
def close_item(store: StoreDep, me: Me, item: ExistingItem) -> Response:
    store.close_item(item.id)
    return RedirectResponse(f"/items/{item.id}", status_code=303)
