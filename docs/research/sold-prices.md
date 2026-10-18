# Sold-price sources: spike results

Spike for build step 1. Tested 2026-10-17 from the dev sandbox. The sandbox egress IP belongs to Sonic Telecom, a residential ISP in San Jose. Every query used the test item, EXIT: The Game Advent Calendar: The Hunt for the Golden Book, BGG id 343322.

None of the 403s below came from the sandbox firewall. Each one carried the site's own block page (Akamai, Cloudflare, PerimeterX), not a "Blocked by network policy" body.

## Summary

| Source | Works from sandbox? | Sold data? | Cost | ToS |
|---|---|---|---|---|
| BGG GeekMarket price history JSON | Yes, 200, no auth | Yes, GeekMarket sales with date, price, condition | Free | Undocumented endpoint, and boardgamegeek.com's robots.txt disallows it |
| Third-party eBay sold-data API, e.g. OpenWeb Ninja | Host reachable, needs a key; not tested | Claimed: eBay sold, last 90 days | Free tier is 100 requests a month | The vendor scrapes eBay. We only call its API |
| Human-in-the-loop eBay sold search | Not applicable, runs in a signed-in browser | Yes, 90 days, or 3 years via Terapeak | Free | A person browsing normally |
| eBay Marketplace Insights API | Endpoint up, needs an approved token | Yes, 90 days | Free if approved | Approval-only, for eBay partners |
| eBay sold search page, scraped | No, Akamai 403 on every eBay page | Login wall since July 2026 | | Prohibited by the User Agreement |
| 130point.com | No, Cloudflare challenge | | | |
| Finding API findCompletedItems | No, decommissioned | | | |
| SerpApi eBay engine | | Dropped sold and complete filters | | |
| WorthPoint, Mercari | No, bot walls | | | |
| PriceCharting | Yes | No board games | | |

## 1. eBay Marketplace Insights API

The API is real and returns what we want. We won't get access.

**What it returns.** `GET https://api.ebay.com/buy/marketplace_insights/v1_beta/item_sales/search` searches by `q`, `category_ids`, `epid` or `gtin`, with a `filter` that takes `lastSoldDate`, `price`, `conditionIds` and others. The method reference says it "retrieves the sales history of the items for the last 90 days". Each `itemSales[]` entry has `title`, `lastSoldPrice` {value, currency}, `lastSoldDate`, `totalSoldQuantity`, `condition`, `itemWebUrl`, images and seller info. It returns at most 10,000 items. It needs an application token with scope `https://api.ebay.com/oauth/api_scope/buy.marketplace.insights`. Source: method reference, [Wayback copy from 2025-05-19](https://web.archive.org/web/20250519205923/https://www.developer.ebay.com/api-docs/buy/marketplace-insights/resources/item_sales/methods/search).

**Access.** The same page says: "This is a (Limited Release) API available only to select developers approved by business units." eBay's [buying-app overview](https://developer.ebay.com/develop/get-started/get-started-on-a-buying-application) lists it as "Retrieve the sales history of items sold on eBay. (This is a Limited Release.)" The [Buy APIs Requirements](https://developer.ebay.com/api-docs/buy/static/buy-requirements.html) page sets out the process:

- "The use of eBay's Buy APIs in production is intended for eBay partners only. You must apply for production access through the eBay Partner Network."
- "Acceptance of applications is based on the proposed business model ... There is no guarantee that your application for production use of the APIs will be approved."
- The steps: sign up for EPN, submit the Buy API Application with UX mocks and data flows, wait up to 10 business days for EPN, open a DTS ticket titled "Buy API Production Access (eBay user ID)", pass a compliance review, then sign contracts. Apps must also "implement affiliate tracking details".

**Sandbox.** The requirements page says "Anyone with an eBay developer account can use the Buy APIs in the sandbox". The method page says the method "is supported in Sandbox environment". Sandbox data is synthetic, so it tells us nothing about real prices.

**Current status.** The public docs have moved behind a login. On 2026-10-17, both `developer.ebay.com/api-docs/buy/marketplace-insights/static/overview.html` and `.../item_sales/methods/search` redirected to `/signin?return_to=/api-docs/marketplace-insights-private/...`. The path now says "private".

**Live test.** I called the production and sandbox endpoints without a token. Both returned HTTP 400 `{"errorId":1002,"domain":"OAuth","message":"Missing access token"}`, so the endpoints are up.

**Odds for us.** In eBay's community forum, two users report applying and being told eBay "no longer provide[s] access besides for major partner" ([thread](https://community.ebay.com/t5/eBay-APIs-Talk-to-your-fellow/Marketplace-Insights-API-access/m-p/34838736)). Another poster gave up because "it was some long process" ([thread](https://community.ebay.com/t5/eBay-APIs-Talk-to-your-fellow/Marketplace-Insights-API-query-about-small-project/m-p/34874973)). A household tool has no EPN business model to show. Don't plan on this.

## 2. eBay sold-listings search page

Dead from here. It would be dead even if the bot block lifted.

**What I tried.** Three requests to `https://www.ebay.com/sch/i.html?_nkw=EXIT+The+Game+Advent+Calendar+Hunt+for+the+Golden+Book&LH_Sold=1&LH_Complete=1`:

- plain `curl`: HTTP 403, 1,832 bytes, `server: AkamaiGHost`, an `Error Page | eBay` body reading "SORRY Something went wrong on our end", and `bm_s`/`bm_so` cookies from Akamai Bot Manager.
- `curl` with a Chrome User-Agent and Accept headers: the same 403.
- `curl_cffi` impersonating Chrome's TLS fingerprint: the same 403.
- `web-shot`, headless Chromium with a 4 s wait: the same error page, plus a console error for the 403.

`https://www.ebay.com/` and `https://www.ebay.co.uk/` also return 403. `/robots.txt` returns 200. So Akamai blocks every eBay page from this sandbox, not just search. The proxy tunnels TLS with CONNECT and doesn't intercept it, since curl saw eBay's Sectigo certificate. The block comes from Akamai scoring the client or the IP. I never saw a results page, so I can't say whether the page embeds JSON.

**Login wall.** Two independent sources say eBay now shows sold and completed results only to signed-in users:

- OpenWeb Ninja, an API vendor: "On 22 July 2026 eBay quietly put them behind a login. Any search URL carrying the sold filter (LH_Sold=1) or the completed filter (LH_Complete=1) now redirects signed-out visitors to signin.ebay.com." They report the same behaviour on seven eBay sites ([blog, 2026-07-28](https://www.openwebninja.com/blog/how-to-get-ebay-sold-and-completed-listings)).
- QuickSync: "Since mid-2026, eBay only shows sold and completed listings to signed-in users ... We checked this on 12 October 2026" ([guide](https://quicksync.pro/blog/how-to-see-sold-items-on-ebay/)).
- SerpApi's eBay engine docs agree: "The Sold and Complete options are deprecated and no longer supported" ([docs](https://serpapi.com/ebay-search-api), `show_only` parameter).

Both blog sources sell something, but the SerpApi change backs them up. I couldn't check this myself because of the Akamai block.

**ToS.** The eBay User Agreement prohibits "any robot, spider, scraper, data mining tools, data gathering and extraction tools, or other automated means to access our Services for any purpose". The January 2026 revision added "buy-for-me agents, LLM-driven bots" ([The Register, 2026-01-22](https://www.theregister.com/2026/01/22/ebay_updates_legalese_to_ban/)). A scraper would now have to sign in to a real eBay account, which puts that account at risk.

## 3. Third-party aggregators and data APIs

**130point.com.** `GET https://130point.com/sales/` returned 403 with a Cloudflare "Just a moment..." page. `web-shot` with an 8 s wait stayed stuck on "Performing security verification". I also tried the form backend, `POST https://back.130point.com/sales/`, and got a Cloudflare "Attention Required!" 403. Its robots.txt has `Disallow: /api/`. There's no public API. 130point mainly covers trading cards anyway.

**OpenWeb Ninja, Real-Time eBay Data API.** The vendor documents sold and completed search with `show_only=sold_items` or `completed_items`, needing no eBay account, at 60 results a page with sold price, sale date, condition and shipping ([product page](https://www.openwebninja.com/api/real-time-ebay-data), [blog](https://www.openwebninja.com/blog/how-to-get-ebay-sold-and-completed-listings)). Pricing: Free is 100 requests a month, hard limit. Pro is $25 a month for 10,000. It's also on RapidAPI. A keyless call to `https://api.openwebninja.com/realtime-ebay-data/search` returned 403 `{"message":"Missing Authentication Token"}`. That's their API gateway, so the host is reachable. **Untested with real data: it needs a sign-up.**

**Apify actors.** Several eBay sold-listing scrapers are maintained. I read their metadata from Apify's public API:

- `cmfapps/ebay-sold-search`: 603 of 700 runs succeeded in the last 30 days, at $0.0015 per sold listing returned, with a 1 to 90 day window.
- `tnodes/ebay-sold-scraper`: 46 of 46 runs succeeded in 30 days. Its readme says "Residential-proxy backed ... Chrome 124 TLS fingerprint impersonation — bypasses eBay's anti-bot".

Those runs postdate the July login wall, so some route still works. Running them needs an Apify account and token, which we don't have. Untested.

**ToS for both.** The vendor does the scraping, and eBay's terms bind the vendor, not us. The data still comes from scraping eBay, and the vendor could break any day, as SerpApi's sold filter did.

**WorthPoint.** The sold-items search returned 403 "Access to this page has been denied", a PerimeterX page. It's a paid subscription with no public API.

**Terapeak.** eBay's Product Research in Seller Hub covers about 3 years of sold data, per QuickSync. It's free to eBay sellers but needs a signed-in seller account and has no API. It works for a person doing a backfill by hand.

## 4. eBay Finding API findCompletedItems

Decommissioned.

- The findCompletedItems call reference says: "This call is deprecated as of October 15, 2020, and access is restricted. All calls in the Finding API will be decommissioned on February 5, 2025. An alternative to findCompletedItems call is the Marketplace Insights API, but please note that this API is also restricted and requires eBay Business approval for access." ([Wayback copy, 2025-01-30](https://web.archive.org/web/20250130010052/https://developer.ebay.com/Devzone/finding/CallRef/findCompletedItems.html)). The live URL now 404s.
- The API Deprecation Status table lists "Finding API | All | 2025/02/04 | This API has been replaced by the Browse API" ([Wayback copy, 2026-06-14](https://web.archive.org/web/20260614162304/https://www.developer.ebay.com/develop/get-started/api-deprecation-status)). The live page is behind the same Akamai 403 from the sandbox, and from WebFetch too.
- Live test: `GET https://svcs.ebay.com/services/search/FindingService/v1?OPERATION-NAME=findCompletedItems&...` returned HTTP 418 with an empty body from `ebay-proxy-server`.

## 5. Board-game sources

### BGG GeekMarket price history: works

The BGG site's own price-history chart calls an unauthenticated JSON endpoint on `api.geekdo.com`, outside BGG's Cloudflare:

```
GET https://api.geekdo.com/api/market/products/pricehistory?ajax=1&condition=any&currency=USD&objectid=343322&objecttype=thing&pageid=1
```

HTTP 200, 4 KB. Leaving out `ajax/condition/currency` gives a 400. The response shape:

- `config`: `numitems`, `itemsperpage` of 100, and the list of supported `currencies`.
- `items[]`: `itemid`, `price`, `currency`, `condition`, `listdate`, `saledate`, `usaledate` in ISO UTC, and `notes`, the seller's free text.
- `pricedata[]`: the same sales as chart points.

The test item has five USD sales:

| Sold | Price | Condition |
|---|---|---|
| 2025-11-18 | $40.00 | New |
| 2023-11-29 | $40.00 | New |
| 2023-08-19 | $40.00 | New |
| 2023-05-04 | $40.00 | Like New |
| 2023-02-21 | $35.00 | Very Good |

`currency=EUR` returned 0 sales, even though there are active EUR listings. History is per currency, so for us USD is enough.

The companion endpoint `GET https://api.geekdo.com/api/market/products?objectid=343322&objecttype=thing&pageid=1` returns active GeekMarket listings, 9 for the test item. Each has `price`, `currency`, `condition`, `listdate`, `itemlocation` and `version`, the edition, e.g. "English edition", id 595004. That's a second listing source beyond eBay.

**Limits.** The data is thin: five sales in three years for this item. GeekMarket sales don't record shipping. Condition comes from BGG's own scale. Sales aren't tied to an edition, but listings are.

**ToS and robots.** The endpoint is undocumented. `boardgamegeek.com/robots.txt` lists, under "GeekMarket JSON endpoints", `Disallow: /api/market/products/pricehistory`. `api.geekdo.com/robots.txt` allows `*` with `Crawl-delay: 5` and blocks named AI crawlers, ClaudeBot among them. BGG's official XML API (`/xmlapi2/thing?id=343322&marketplace=1`) returned 401 "Unauthorized. See https://boardgamegeek.com/using_the_xml_api". Since mid-2025 it has needed a registered application and a bearer token, registered as commercial or non-commercial. BGG says approval can take a week or more. `marketplace=1` on the XML API returns listings, not sale history, as far as I know. I couldn't read BGG's docs pages from here, which are behind Cloudflare. BGG's robots.txt says plainly that it doesn't want bots on price history. At a few dozen requests a week it's polite, but it's still against stated wishes. Registering a non-commercial XML API app and asking BGG whether market history is OK is cheap.

### Others checked

- **PriceCharting** returned 200, but it covers video games, trading cards, comics, coins, Funko and LEGO, with no board-game category. A search for "EXIT advent calendar" returned only LEGO advent calendars.
- **Board Game Oracle** returned 200 and has search results. It tracks retail prices, not sales. That's useful as a "new in shrink" ceiling, not as sold data.
- **Mercari** search with `itemStatuses=2` (sold) returned a Cloudflare 403.

## Recommendation

Ranked for a household tool with a few dozen items:

1. **BGG GeekMarket price history, for board games.** It works today with no key and gives real sold prices with condition and date. Fetch it once when an item is added and weekly after that, with a 5 s gap between requests. Register a non-commercial BGG XML API app anyway, for the edition and UPC extraction the README already plans, and use the same contact to ask about market history. Expect sparse data. It's a floor of evidence, not a market.
2. **A paid eBay sold-data API: OpenWeb Ninja first, Apify as backup.** This is the only automated route to eBay sold prices. The admin should sign up for the free tier and run one query with the test item before we design around it. Sold prices don't need a daily fetch. eBay keeps 90 days, and the Browse API already tracks asking prices daily. One backfill when an item is added, then one refresh per item every two weeks, comes to about 60 to 80 requests a month for 30 items. That fits the free 100. Build it behind a `SoldPriceSource` interface: these vendors break whenever eBay changes something.
3. **A person does the eBay backfill.** The item page shows a link to eBay's sold search, and Terapeak for 3 years if the admin has a seller account. Either of us opens it signed in, copies the results text and pastes it into the item page. `claude-json` extracts the rows. With a few dozen items, that's a few minutes a week. It carries no ToS or account risk and survives eBay's changes. It also works as a fallback for option 2.
4. **Marketplace Insights.** Skip it. Approval is partner-only, and recent applicants report denials.

Dead ends: scraping eBay directly (Akamai blocks it, the login wall blocks it, and the User Agreement forbids it), 130point, WorthPoint, Mercari, the Finding API and SerpApi's sold filter.

### Questions for the admin

- The README promises "Backfills sold prices right after you add an item". With option 3, the backfill waits for a person. Is that acceptable, or is option 2 worth $0 to $25 a month?
- Untested idea: when a Browse API listing disappears, call `getItem` once. If eBay still returns the item, `estimatedAvailabilities` or the listing status might tell sold from expired. That would turn our own listing history into sold data without any new source. It's worth one test once we have Browse API keys.
