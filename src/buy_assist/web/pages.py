"""What every route shares: the services, who's viewing, the item in the URL, and rendering."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from buy_assist.ebay import sold_search_url
from buy_assist.forms import format_dollars
from buy_assist.links import LinkReader
from buy_assist.models import Condition, Item, User
from buy_assist.parser import ItemParser
from buy_assist.paste import SalesParser
from buy_assist.prices import summarize_sales
from buy_assist.sold import SoldPriceSource
from buy_assist.store import Store

PACKAGE_DIR = Path(__file__).parent.parent

_templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
_templates.env.filters["dollars"] = format_dollars  # pyright: ignore[reportUnknownMemberType]


@dataclass(frozen=True)
class Services:
    """The app's outside collaborators, swapped for fakes in tests."""

    parser: ItemParser
    links: LinkReader
    sold_prices: SoldPriceSource
    sales_parser: SalesParser


class NoIdentity(Exception):
    """The visitor hasn't picked who they are, or picked someone who no longer exists."""


def ask_identity(request: Request, exc: Exception) -> Response:
    return RedirectResponse("/whoami", status_code=303)


def store_of(request: Request) -> Store:
    return request.app.state.store


def _services(request: Request) -> Services:
    return request.app.state.services


StoreDep = Annotated[Store, Depends(store_of)]
ServicesDep = Annotated[Services, Depends(_services)]


def _current_user(store: StoreDep, user_id: Annotated[str | None, Cookie()] = None) -> User:
    users = {str(u.id): u for u in store.users()}
    if user_id is None or user_id not in users:
        raise NoIdentity
    return users[user_id]


Me = Annotated[User, Depends(_current_user)]


def _existing_item(store: StoreDep, item_id: int) -> Item:
    item = store.get_item(item_id)
    if item is None:
        raise HTTPException(404, "No such item")
    return item


ExistingItem = Annotated[Item, Depends(_existing_item)]


def render(
    request: Request, name: str, me: User | None, status_code: int = 200, **context: object
) -> HTMLResponse:
    return _templates.TemplateResponse(
        request,
        name,
        {"me": me, "users": store_of(request).users(), "conditions": list(Condition), **context},
        status_code=status_code,
    )


def item_page(
    request: Request, me: User, item: Item, messages: list[str] | None = None
) -> HTMLResponse:
    store = store_of(request)
    sales = store.sales(item.id)
    return render(
        request,
        "item.html",
        me,
        item=item,
        home_zip=store.home_zip(),
        sales=sales,
        summary=summarize_sales(r.sale for r in sales),
        ebay_sold_url=sold_search_url(item.fields),
        sold_source=_services(request).sold_prices,
        messages=messages or [],
    )
