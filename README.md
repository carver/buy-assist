# Buy Assistant

Finds listings for specific things our household wants to buy, often used, and helps us work out what a fair price is.

Shira is the main user. Jason also tracks a few items of his own. Each item has watchers, and only watchers get its alerts.

## What it does

- Keeps a list of open items. You add one with free text, a reference link, or both, and Claude fills in fields you can edit.
- Searches marketplaces for each item and records every listing it sees, with price over time and when the listing ended.
- Backfills sold prices when you add an item, from BoardGameGeek automatically or from eBay results you paste in, so a fair price is visible before the first new listing turns up.
- Works out the full price: item, plus shipping to the item's zip, plus tax at that zip's rate.
- Suggests a target price once it has about 5 data points. You accept or edit it.
- Emails watchers when something worth seeing shows up, and sends a daily digest.

## Items

An item has:

- name
- identifiers when known: UPC, eBay product ID (EPID), model number
- reference link, such as a BoardGameGeek page
- variant (size, color, edition) and acceptable condition (new, used, any)
- shipping zip, defaulting to home
- watchers: Jason, Shira, or both
- target price, optional, compared against the full price
- notes, which "not this" reasons get added to

### Reference links

Claude reads any reference link the same way and pulls out name, edition, UPC and an image. The app counts how many items use each link domain. Once a domain reaches 3 uses, Jason's digest and the settings page say it's worth writing a dedicated extractor for that site. BoardGameGeek is the likely first one. Its XML API lists editions, but they carry publisher product codes rather than UPCs, and it needs a registered app token (see `docs/research/apis.md`).

Test fixture: [EXIT: The Game Advent Calendar: The Hunt for the Golden Book](https://boardgamegeek.com/boardgame/343322/exit-the-game-advent-calendar-the-hunt-for-the-gol).

## Sources

eBay first, through the Browse API. It searches active listings by keyword, UPC (GTIN) or EPID and quotes shipping to a zip. Claude checks each candidate against the item and drops listings like "for parts", "box only" and accessories.

The Browse API doesn't return sold prices, and there's no free automated way to get them (see `docs/research/sold-prices.md`). Sold prices come from two places:

- BoardGameGeek GeekMarket price history, for items with a BGG link. It's an undocumented JSON endpoint, so we only make small targeted lookups, at most one request every 5 seconds, which BGG's API wiki gives as the safe gap.
- Pasting by hand. The item page links to eBay's sold search for the item. Shira signs in, copies the results and pastes them into the page, and Claude pulls out date, price and condition.

A paid eBay sold-data API is the upgrade if pasting gets tedious. Each item needs one backfill when it's added, and no refresh after that, since the daily search sees new listings as they appear.

Later candidates: BoardGameGeek's GeekMarket for games, Etsy.

## Price history

We keep our own record of every listing: asking price over time, and when it disappeared. A listing that disappears may have sold or expired, and the Browse API can't tell us which. Sold prices from the backfill go in a separate table.

Each new listing gets ranked against the history ("cheapest of 14 seen, median $62").

## Alerts

Email, from Jason's Purelymail account, to the item's watchers only. Every email links to the item's page.

Instant alerts:

- the first listing found for a new item, with a note that the rest will arrive in the digest
- any listing at or under the target price
- a new lowest price, once the item has about 5 data points

Daily digest at 11:30am Pacific, listing new matches per item. Skipped when nothing is new. Jason's digest also carries the reference-link extractor reminders.

## Web app

Each item has a page with current listings, price history, sold prices and the suggested target, so nobody has to wait for an email.

There's no login. You pick who you are and a cookie remembers it. The tailnet is the security boundary.

Feedback on a listing:

- "not this", with an optional reason that gets added to the item's notes
- "bought", which closes the item and records the final price

Settings: tax rate per zip, home zip, reference-link domains and their use counts.

## Running it

Python, FastAPI, HTMX and SQLite in one process. The process serves the pages and runs the scheduler. It runs in the dev sandbox for now, started at boot from the project's `install.py`, and Shira reaches it over Tailscale.

The scheduler checks every hour and runs a fetch if the last successful one is more than 24 hours old, so it catches up after the sandbox has been off. Claude calls go through `claude-json`.

Moving to a real server should mean copying the code and the SQLite file, then adding real auth.

Needs an eBay developer account for API keys.

Start it with `uv run buy-assist` (`-h` for options). It keeps its database in `data/buy.db` and listens on port 8000.

## Development

`uv run pytest` runs the tests. Tests marked `live` call real services such as `claude-json` and are skipped by default; run them with `uv run pytest -m live`. Run `uv run pre-commit install` once per clone, so commits run ruff, pyright and the tests.

## Build order

eBay keys don't exist yet, so the parts that don't need them come first. The eBay source sits behind an interface and is tested against recorded responses until the keys arrive.

1. Data model, item page, add-item flow.
2. Reference-link extraction, starting with the BoardGameGeek fixture.
3. Sold prices: BGG history and the paste flow.
4. Scheduler, alerts and digest.
5. eBay search, once keys exist.
6. Feedback.
