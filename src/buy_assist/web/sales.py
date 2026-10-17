"""Sold prices on the item page: refresh from a source, paste eBay results, remove mistakes."""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import TypeAdapter, ValidationError

from buy_assist.claude import ParseError
from buy_assist.models import Item, Sale, SaleSource, checked_sale
from buy_assist.paste import PastedSale
from buy_assist.sold import SoldPriceError, SoldPriceSource
from buy_assist.store import Store
from buy_assist.web.pages import ExistingItem, Me, ServicesDep, StoreDep, item_page, render

router = APIRouter()
_log = logging.getLogger(__name__)
_SALES = TypeAdapter(list[Sale])


def record_sales(store: Store, source: SoldPriceSource, item: Item) -> int:
    """Fetch and store the item's sales. Returns how many were new. Raises SoldPriceError."""
    return store.add_sales(item.id, source.sales_for(item.fields))


def record_sales_quietly(store: Store, source: SoldPriceSource, item: Item) -> None:
    """record_sales for a background task, where there's nobody to show an error to."""
    try:
        record_sales(store, source, item)
    except SoldPriceError as e:
        _log.warning("Sold prices for item %d: %s", item.id, e)


@router.post("/items/{item_id}/sales/refresh")
def refresh_sales(
    request: Request, store: StoreDep, services: ServicesDep, me: Me, item: ExistingItem
) -> HTMLResponse:
    source = services.sold_prices
    try:
        added = record_sales(store, source, item)
        if added:
            message = f"Added {added} new sales from {source.label}."
        else:
            message = f"No new sales on {source.label}."
    except SoldPriceError as e:
        message = f"Couldn't get sold prices: {e}"
    return item_page(request, me, item, [message])


@router.post("/items/{item_id}/sales/paste")
def paste_sales(
    request: Request,
    services: ServicesDep,
    me: Me,
    item: ExistingItem,
    text: Annotated[str, Form()],
) -> HTMLResponse:
    try:
        rows = services.sales_parser.parse(text, item.fields)
    except ParseError:
        return item_page(request, me, item, ["Claude couldn't read that paste. Try copying again."])
    if not rows:
        return item_page(request, me, item, ["No sold listings found in that paste."])
    return _preview(request, me, item, rows)


def _preview(request: Request, me: Me, item: Item, rows: list[PastedSale]) -> HTMLResponse:
    rows_json = _SALES.dump_json([r.sale for r in rows]).decode()
    return render(request, "sales_preview.html", me, item=item, rows=rows, rows_json=rows_json)


@router.post("/items/{item_id}/sales/save")
async def save_pasted_sales(
    request: Request, store: StoreDep, me: Me, item: ExistingItem
) -> Response:
    form = await request.form()
    try:
        rows = _SALES.validate_json(str(form.get("rows", "")))
    except ValidationError as e:
        raise HTTPException(400, "The pasted rows didn't come back intact.") from e
    chosen = {int(v) for v in form.getlist("selected") if isinstance(v, str) and v.isdecimal()}
    pasted = (
        Sale(
            source=SaleSource.EBAY,
            sold_on=sale.sold_on,
            price_cents=sale.price_cents,
            shipping_cents=sale.shipping_cents,
            is_new=sale.is_new,
            condition=sale.condition,
            title=sale.title,
        )
        for index, sale in enumerate(rows)
        if index in chosen
    )
    today = date.today()
    sales = [checked for sale in pasted if (checked := checked_sale(sale, today))]
    store.add_sales(item.id, sales)
    return RedirectResponse(f"/items/{item.id}", status_code=303)


@router.post("/items/{item_id}/sales/{sale_id}/remove")
def remove_sale(store: StoreDep, me: Me, item: ExistingItem, sale_id: int) -> Response:
    store.remove_sale(item.id, sale_id)
    return RedirectResponse(f"/items/{item.id}", status_code=303)
