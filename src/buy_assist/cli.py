import argparse
import logging
from datetime import timedelta
from pathlib import Path

import httpx2
import uvicorn

from buy_assist.bgg import BggApi
from buy_assist.ebay import EbayApi
from buy_assist.judge import ClaudeListingJudge
from buy_assist.links import HttpLinkReader
from buy_assist.listings import EbayListings, GeekMarketListings, ListingSource
from buy_assist.mailer import Mailer, MailerError, SmtpMailer
from buy_assist.parser import ClaudeItemParser
from buy_assist.paste import ClaudeSalesParser
from buy_assist.pricing import UspsQuoter
from buy_assist.sold import BggSoldPrices
from buy_assist.store import Store
from buy_assist.web import Services, create_app

_log = logging.getLogger("buy_assist")
_WATCH_EVERY = timedelta(hours=1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Buy Assistant web app, and the hourly watch that fetches listings "
        "and emails alerts and the daily digest."
    )
    parser.add_argument("--db", type=Path, default=Path("data/buy.db"), help="SQLite file")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on")
    parser.add_argument("--port", type=int, default=6767)
    parser.add_argument(
        "--no-watch", action="store_true", help="serve the app only: no fetching, no email"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args.db.parent.mkdir(parents=True, exist_ok=True)
    client = httpx2.Client(timeout=20)
    bgg = BggApi(client)
    sources: list[ListingSource] = [GeekMarketListings(bgg)]
    if ebay := EbayApi.from_env(client):
        sources.append(EbayListings(ebay))
    else:
        _log.warning("No eBay search: EBAY_CLIENT_ID and EBAY_CLIENT_SECRET aren't set")
    services = Services(
        parser=ClaudeItemParser(),
        links=HttpLinkReader(client, bgg),
        sold_prices=BggSoldPrices(bgg),
        sales_parser=ClaudeSalesParser(),
        listing_sources=sources,
        judge=ClaudeListingJudge(),
        shipping=UspsQuoter(Store.open(args.db), client),
        mailer=_mailer(),
    )
    app = create_app(args.db, services, watch_every=None if args.no_watch else _WATCH_EVERY)
    uvicorn.run(app, host=args.host, port=args.port)


def _mailer() -> Mailer | None:
    try:
        return SmtpMailer.purelymail_from_env()
    except MailerError as e:
        _log.warning("No email will be sent: %s", e)
        return None
