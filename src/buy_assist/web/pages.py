"""What every route shares: the services, who's viewing, the item in the URL, and rendering."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from buy_assist.ebay import sold_search_url
from buy_assist.forms import describe_box, format_dollars, format_pounds
from buy_assist.judge import ListingJudge
from buy_assist.links import LinkReader
from buy_assist.listings import ListingSource
from buy_assist.mailer import Mailer
from buy_assist.models import Condition, Item, User
from buy_assist.parser import ItemParser
from buy_assist.paste import SalesParser
from buy_assist.prices import summarize_sales
from buy_assist.pricing import Pricer, ShippingQuoter
from buy_assist.sold import SoldPriceSource
from buy_assist.store import Store
from buy_assist.times import LocalTime

PACKAGE_DIR = Path(__file__).parent.parent

_templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
_templates.env.filters["dollars"] = format_dollars  # pyright: ignore[reportUnknownMemberType]
_templates.env.filters["box"] = describe_box  # pyright: ignore[reportUnknownMemberType]
_templates.env.filters["pounds"] = format_pounds  # pyright: ignore[reportUnknownMemberType]


@dataclass(frozen=True)
class Services:
    """The app's outside collaborators, swapped for fakes in tests."""

    parser: ItemParser
    links: LinkReader
    sold_prices: SoldPriceSource
    sales_parser: SalesParser
    listing_sources: Sequence[ListingSource]
    judge: ListingJudge
    shipping: ShippingQuoter
    mailer: Mailer | None


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
        {
            "me": me,
            "users": store_of(request).users(),
            "conditions": list(Condition),
            "local": LocalTime.named(request.cookies.get("tz")),
            **context,
        },
        status_code=status_code,
    )


def item_page(
    request: Request,
    me: User,
    item: Item,
    messages: list[str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    store = store_of(request)
    pricer = Pricer(store, _services(request).shipping)
    sales = [(r, pricer.sale(item.fields, r.sale)) for r in store.sales(item.id)]
    recorded = store.listings(item.id, include_ended=True)
    listings = [
        (r, pricer.listing(item.fields, r.listing)) for r in recorded if r.rejection is None
    ]
    return render(
        request,
        "item.html",
        me,
        status_code,
        item=item,
        home_zip=store.home_zip(),
        names={u.id: u.name for u in store.users()},
        sales=sales,
        listings=sorted(
            ((r, p) for r, p in listings if r.ended_at is None), key=lambda rp: rp[1].total_cents
        ),
        gone=sorted(
            ((r, p) for r, p in listings if r.ended_at is not None),
            key=lambda rp: rp[0].ended_at or rp[0].last_seen,
            reverse=True,
        ),
        filtered_out=[r for r in recorded if r.rejection is not None],
        bought_from=next(
            (r for r in recorded if item.purchase and r.id == item.purchase.listing_id), None
        ),
        summary=summarize_sales(
            (r.sale for r, _ in sales), lambda sale: pricer.sale(item.fields, sale).total_cents
        ),
        ebay_sold_url=sold_search_url(item.fields),
        sold_source=_services(request).sold_prices,
        messages=messages or [],
    )
