"""The household web app: pick who you are, add items, view and edit them."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import TypeAdapter, ValidationError

from buy_assist.forms import ZIP_ERROR, ItemForm, format_dollars
from buy_assist.links import DEDICATED_DOMAINS, Edition, LinkError, LinkReader, first_url
from buy_assist.models import Condition, Item, User, is_zip, url_domain
from buy_assist.parser import ItemParser, ParseError, draft_from_link
from buy_assist.store import Store

_PACKAGE_DIR = Path(__file__).parent
_USER_COOKIE = "user_id"
_ONE_YEAR_SECONDS = 365 * 24 * 3600

_templates = Jinja2Templates(directory=_PACKAGE_DIR / "templates")
_templates.env.filters["dollars"] = format_dollars  # pyright: ignore[reportUnknownMemberType]

router = APIRouter()


def create_app(db_path: Path, parser: ItemParser, links: LinkReader) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = Store.open(db_path)
    app.state.parser = parser
    app.state.links = links
    app.mount("/static", StaticFiles(directory=_PACKAGE_DIR / "static"), name="static")
    app.add_exception_handler(_NoIdentity, _ask_identity)
    app.include_router(router)
    return app


@dataclass(frozen=True)
class ReferenceDomain:
    domain: str
    item_count: int
    reader: str | None

    @property
    def wants_reader(self) -> bool:
        """Enough items link here that a dedicated reader would pay off."""
        return self.reader is None and self.item_count >= 3


class _NoIdentity(Exception):
    """The visitor hasn't picked who they are, or picked someone who no longer exists."""


def _ask_identity(request: Request, exc: Exception) -> Response:
    return RedirectResponse("/whoami", status_code=303)


def _store(request: Request) -> Store:
    return request.app.state.store


def _parser(request: Request) -> ItemParser:
    return request.app.state.parser


def _links(request: Request) -> LinkReader:
    return request.app.state.links


StoreDep = Annotated[Store, Depends(_store)]
ParserDep = Annotated[ItemParser, Depends(_parser)]
LinksDep = Annotated[LinkReader, Depends(_links)]


def _current_user(store: StoreDep, user_id: Annotated[str | None, Cookie()] = None) -> User:
    users = {str(u.id): u for u in store.users()}
    if user_id is None or user_id not in users:
        raise _NoIdentity
    return users[user_id]


Me = Annotated[User, Depends(_current_user)]


def _existing_item(store: StoreDep, item_id: int) -> Item:
    item = store.get_item(item_id)
    if item is None:
        raise HTTPException(404, "No such item")
    return item


ExistingItem = Annotated[Item, Depends(_existing_item)]


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


def _page(
    request: Request, name: str, me: User | None, status_code: int = 200, **context: object
) -> HTMLResponse:
    store = _store(request)
    return _templates.TemplateResponse(
        request,
        name,
        {"me": me, "users": store.users(), "conditions": list(Condition), **context},
        status_code=status_code,
    )


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
    return _page(
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
        home_zip=_store(request).home_zip(),
    )


def _save(
    request: Request,
    store: Store,
    me: User,
    form: ItemForm,
    item_id: int | None,
    editions: tuple[Edition, ...] = (),
) -> Response:
    result = form.validate({u.id for u in store.users()})
    if isinstance(result, list):
        return _item_form_page(request, me, form, item_id=item_id, errors=result, editions=editions)
    item = store.create_item(result) if item_id is None else store.update_item(item_id, result)
    return RedirectResponse(f"/items/{item.id}", status_code=303)


@router.get("/whoami")
def whoami(request: Request) -> HTMLResponse:
    return _page(request, "whoami.html", None)


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
    return _page(request, "items.html", me, items=items, closed=closed)


@router.get("/items/new")
def new_item(request: Request, me: Me) -> HTMLResponse:
    return _page(request, "new_item.html", me)


@router.post("/items/draft")
def draft_item(
    request: Request, parser: ParserDep, links: LinksDep, me: Me, text: Annotated[str, Form()]
) -> HTMLResponse:
    messages: list[str] = []
    link = None
    if url := first_url(text):
        try:
            link = links.read(url)
            messages.append(f"Read from {link.source}.")
        except LinkError as e:
            site = url_domain(url) or url
            messages.append(f"Couldn't read {site}: {e} Details come from your text.")
    try:
        draft = parser.parse(text, link)
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
    me: Me,
    form: SubmittedForm,
    editions: SubmittedEditions,
) -> Response:
    return _save(request, store, me, form, None, editions)


@router.get("/items/{item_id}")
def show_item(request: Request, store: StoreDep, me: Me, item: ExistingItem) -> HTMLResponse:
    return _page(request, "item.html", me, item=item, home_zip=store.home_zip())


@router.get("/items/{item_id}/edit")
def edit_item(request: Request, me: Me, item: ExistingItem) -> HTMLResponse:
    return _item_form_page(request, me, ItemForm.from_fields(item.fields), item_id=item.id)


@router.post("/items/{item_id}")
def update_item(
    request: Request, store: StoreDep, me: Me, item: ExistingItem, form: SubmittedForm
) -> Response:
    return _save(request, store, me, form, item.id)


@router.post("/items/{item_id}/close")
def close_item(store: StoreDep, me: Me, item: ExistingItem) -> Response:
    store.close_item(item.id)
    return RedirectResponse(f"/items/{item.id}", status_code=303)


def _settings_page(
    request: Request, me: User, home_zip: str, errors: list[str], status_code: int = 200
) -> HTMLResponse:
    counts = _store(request).reference_domain_counts()
    domains = [
        ReferenceDomain(domain, count, DEDICATED_DOMAINS.get(domain))
        for domain, count in sorted(counts.items(), key=lambda dc: (-dc[1], dc[0]))
    ]
    return _page(
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
