"""Links into eBay's own site."""

from urllib.parse import urlencode

from buy_assist.models import Condition, ItemFields

_CONDITION_FILTER = {Condition.NEW: "1000", Condition.USED: "3000"}


def sold_search_url(item: ItemFields) -> str:
    """eBay's search for completed, sold listings of the item, for a person to open."""
    params = {
        "_nkw": item.search_query or item.name,
        "LH_Sold": "1",
        "LH_Complete": "1",
    }
    if condition := _CONDITION_FILTER.get(item.condition):
        params["LH_ItemCondition"] = condition
    return "https://www.ebay.com/sch/i.html?" + urlencode(params)
