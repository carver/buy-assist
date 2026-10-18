# Buying Assistant

Finds listings for specific things our household wants to buy, often used, and helps us work out what a fair price is.

Multi-user system, with per-user settings. Each user can individually watch items to buy.

## What it does

- Keeps a list of open items. You add one with free text, a reference link, or both, and Claude fills in fields you can edit.
- Searches marketplaces for each item and records every listing it sees, with price over time and when the listing ended.
- Backfills sold prices when you add an item, from BoardGameGeek automatically or from eBay results you paste in, so a fair price is visible before the first new listing turns up.
- Works out the full price: item, plus shipping to the item's zip (estimated when the seller doesn't say), plus tax where the marketplace collects it.
- Suggests a target price once it has about 5 data points. You accept or edit it.
- Emails watchers when something worth seeing shows up, and sends a daily digest.

## Items

An item has:

- name
- identifiers when known: UPC, eBay product ID (EPID), model number
- reference link, such as a BoardGameGeek page
- search words: a short query for marketplace searches, drafted by Claude
- variant (size, color, edition) and acceptable condition (new, used, any)
- shipping zip, defaulting to home (94114); a note that names a ZIP sets it
- package weight and box size, from the BGG edition when there is one, for shipping estimates
- watchers: either or both
- target price, optional, compared against the full price
- notes, which "not this" reasons get added to

### Reference links

The first link in an item request is its reference link. The app reads the page behind it, then Claude reads the request with what the page said as context.

Most sites get the generic reader. It fetches the page (public hosts only), takes any JSON-LD product data (name, GTIN, MPN, image), and passes the visible text to Claude. If a site blocks the fetch, the draft comes from the typed text alone and the form says so.

BoardGameGeek has its own reader, since its pages sit behind a Cloudflare challenge. It uses the JSON API the BGG site itself calls (`api.geekdo.com`), at most one request every 5 seconds. BGG lists a game's editions, so the form offers them as a dropdown; picking one fills in that edition's title and publisher product code. BGG editions carry product codes, not UPCs. Once BGG approves our app, the reader can move to the official XML API (see `docs/research/apis.md`).

The settings page counts items per reference-link domain. A domain with 3 or more items and no dedicated reader gets flagged as worth writing one, and the same list goes at the foot of every email to the admin.

Test fixture: [EXIT: The Game Advent Calendar: The Hunt for the Golden Book](https://boardgamegeek.com/boardgame/343322/exit-the-game-advent-calendar-the-hunt-for-the-gol).

## Sources

Every listing source sits behind one interface (`listings.py`).

- **BoardGameGeek GeekMarket:** active listings for items with a BGG link, in dollars and located in the US, in the item's edition when the listing names one.
- **eBay**, through the Browse API (`ebay.py`): one search per item per day, by UPC when the item has one, else EPID, else its search words. It takes Buy It Now listings and auctions located in the US that deliver to the item's ZIP, in the item's acceptable condition, and eBay quotes shipping to that ZIP. When eBay gives no cost, the USPS estimate uses the seller's ZIP prefix. An auction with a Buy It Now price counts as Buy It Now. Pure auctions show their bids and end time on the item page and in the digest. Their bids don't count toward a new low or a rank, and they only alert when ending soon (see Alerts).

Claude checks each eBay listing against the item the first time it's seen (`judge.py`) and rejects ones like "for parts", "box only", accessories, lots and other editions, along with anything like a listing the household turned down (see Feedback). Rejected listings never alert or reach the digest; the item page lists them under "Filtered out" with Claude's reason, so a wrong call is easy to spot and put back. GeekMarket listings skip the check, since they're already tied to the game and edition.

The Browse API doesn't return sold prices, and there's no free automated way to get them (see `docs/research/sold-prices.md`). Sold prices come from two places:

- BoardGameGeek GeekMarket price history, for items with a BGG link, fetched right after the item is saved and on demand from the item page. It's an undocumented JSON endpoint, so we only make small targeted lookups; every BGG call shares one throttle of one request every 5 seconds, which BGG's API wiki gives as the safe gap.
- Pasting by hand. The item page links to eBay's sold search for the item's search words. User signs in, copies the results and pastes them into the page. Claude pulls out each sale and unchecks ones that don't look like the item (parts, other editions, lots); she reviews the list and saves.

A paid eBay sold-data API is the upgrade if pasting gets tedious. Each item needs one backfill when it's added, and no refresh after that, since the daily search sees new listings as they appear.

Later candidates: Etsy.

## Price history

We keep our own record of every listing: asking price over time, and when it disappeared. A listing that disappears may have sold or expired, and the Browse API can't tell us which. Sold prices from the backfill go in a separate table.

Each new listing gets ranked against the history ("cheapest of 14 seen, median $62").

The item page summarises sold prices in two rows, new and used, so the premium for new is visible. Each row also gives a median all-in price.

## Full prices

Full price = item price + shipping + tax.

- **Shipping:** the seller's own figure when the listing gives one. Otherwise a USPS Ground Advantage estimate (`usps.py`): the item's weight plus 12 oz of packing, rounded up the way USPS bills, with dimensional weight for big boxes, at Notice 123 commercial (online label) prices, for the zone from the seller to our ZIP. A seller's state picks the zone; with no origin, the estimate uses the typical zone for our ZIP. USPS's zone chart comes from postcalc.usps.com and is kept for a month. When USPS changes prices, regenerate `src/buy_assist/rates/usps_ground_advantage.json` with `scripts/usps_rates.py` (its `-h` shows how). See `docs/research/shipping.md` for how close this gets.
- **Tax:** depends on where you buy, not on condition. eBay collects California sales tax as a marketplace facilitator, used items included; GeekMarket buyers pay sellers directly, untaxed. Tax applies to the item price at the ship-to ZIP's rate (94114: 8.625%, from CDTFA), set on the settings page.

## Alerts

Email, from Purelymail account, to the item's watchers only, at the addresses set on the settings page. Once the app address is set there, every email links to the item's page.

Instant alerts:

- the first listing found for a new item, with a note that the rest will arrive in the digest
- any listing at or under the target price
- a new lowest full price, once the item has at least 5 earlier data points (listings and sales)
- an auction that would end before the person's next digest could mention it. The digest goes out at the first hourly tick from 11:30, so "before" means by 12:30.

A listing that triggers more than one alert appears once in the email, with each reason.

Daily digest at 11:30am Pacific, listing new matches per item, each ranked against everything seen for that item. Above them, under "Ending soon", it reminds of every auction that ends before the next day's digest, even ones already sent; an auction that's already over is left out entirely. Skipped when nothing is new or ending. If the app is down at 11:30, the digest goes out when it's back, the same day only.

eBay bids by proxy: you enter the most you'll pay, any time before the end, and eBay bids for you up to it. So an auction reminder, by digest or alert, gives the bid that keeps the full price (shipping, plus tax on the bid) within the target, or with no target, under the cheapest Buy It Now listing. It's skipped when the current bid is already past that, and sent without a bid when there's nothing to compare. The bid is from that day's search, so the reminder says when it was checked. Each person gets one reminder per auction.

Every alert and digest to the admin ends with the reference-link sites worth a reader. That footer never sends an email on its own.

Alerts are worked out from what's stored, and a log of who heard about which listing stops repeats, so an email that fails to send goes out on the next hourly tick.

## Web app

Each item has a page with current listings, price history, sold prices and the suggested target, so nobody has to wait for an email.

There's no login. You pick who you are and a cookie remembers it. The tailnet is the security boundary.

Pages show times in the browser's time zone, which a cookie passes to the server (Pacific until it's set). Emails can't know where they'll be read, so their times are Pacific and say "PT".

Feedback on a listing, from the item page (`web/feedback.py`):

- "not this", with an optional reason that gets added to the item's notes. The listing joins "Filtered out" with the reason and who said it, so it never alerts, reaches the digest or counts as a data point. With a reason, Claude checks the item's other current eBay listings again against the new notes, so one "German edition" clears out the rest.
- "put back", on any filtered listing, Claude's or a person's. Claude never filters a put-back listing again.
- "bought", on a current listing or one in price history (a won auction), or "bought elsewhere" on the item. It records the all-in price paid, prefilled from the listing and editable, closes the item, and emails its other watchers so nobody buys a second.

Settings: home zip, people (name, email address, and whether they're the admin), the app address for links, tax rate per zip, reference-link domains and their use counts.

## Running it

Python, FastAPI, HTMX and SQLite in one process. The process serves the pages and runs the scheduler. It runs in the dev sandbox for now, started at boot from the project's `install.py`, and mobile user reaches it over Tailscale.

The scheduler (`watch.py`) ticks every hour. It fetches listings for each open item whose last successful fetch is over 24 hours old (and right after an item is saved), refreshes BGG sold prices weekly, sends instant alerts, and sends the digest when it's due. Each job remembers its last success, so it catches up after the sandbox has been off. Claude calls go through `claude-json`.

Moving to a real server should mean copying the code and the SQLite file, then adding real auth.

eBay search needs a production keyset from the eBay developer program: `EBAY_CLIENT_ID` (App ID) and `EBAY_CLIENT_SECRET` (Cert ID) in `/etc/sandbox-secrets.sh`. eBay won't activate production keys until the app either takes marketplace account deletion notices or claims the exemption; we store no eBay user data, so claim the exemption. Without the keys the app runs with GeekMarket alone and says so in its log.

When the eBay keys arrive:

1. Add them to `/etc/sandbox-secrets.sh` as `export EBAY_CLIENT_ID=...` and `export EBAY_CLIENT_SECRET=...`.
2. In the eBay developer portal, claim the marketplace account deletion exemption, so eBay activates the keyset.
3. Run `./restart.sh`, so the app starts again with the keys. Its log (`data/server.log`) stops warning that eBay search is off.
4. Run `uv run pytest -m live tests/test_ebay_listings.py`. The tests' eBay response was built from eBay's published field names, not captured, so expect small fixes to `ebay.py` and to `tests/data/ebay_search_exit.json`.
5. Settle the open questions at the end of `docs/research/apis.md`.

Start it with `uv run buy-assist` (`-h` for options). It keeps its database in `data/buy.db` and listens on port 6767. `--no-watch` serves the pages without fetching or sending email. Email needs `PURELYMAIL_ADDRESS` and `PURELYMAIL_PASSWORD` in the environment; without them the app runs and sends nothing. eBay search needs `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` likewise.

To keep it running, `python3 install.py` installs dependencies and adds crontab entries that run `scripts/serve.sh` every 5 minutes and at boot. That script starts the app, with the Purelymail and eBay credentials from `/etc/sandbox-secrets.sh`, whenever it isn't answering, and logs to `data/server.log`. `python3 install.py remove` takes the entries out.

After pulling new code or changing credentials, `./restart.sh` stops the running app, syncs dependencies and starts it again. Migrations run as it starts.

## Development

`uv run pytest` runs the tests. Tests marked `live` call real services such as `claude-json` and are skipped by default; run them with `uv run pytest -m live`. Link the git hook in once per clone with `ln -sf ../../scripts/git-pre-commit .git/hooks/pre-commit`, so commits run ruff. The hook runs pre-commit through `uvx` rather than `.venv`, so it also works from the host, which shares this checkout but can't run the sandbox's `.venv`. Pyright and the tests are too slow for that hook, so they live in `scripts/agent-pre-commit`. To have agents' commits run it, link it in with `ln -s ../../scripts/agent-pre-commit .git/hooks/agent-pre-commit` and run `agent-commit-gate --install .`. Run it by hand before your own commits. CI runs the same checks on pushes to main and on pull requests.

## Build order

eBay keys didn't exist at first, so the parts that don't need them came first. The eBay source is tested against a response shaped from eBay's published field names; its live test runs once the keys arrive.

1. Data model, item page, add-item flow.
2. Reference-link extraction, starting with the BoardGameGeek fixture.
3. Sold prices: BGG history and the paste flow.
4. Scheduler, alerts and digest.
5. eBay search, once keys exist.
6. Feedback.
