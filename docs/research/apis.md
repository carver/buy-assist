# API research: BoardGameGeek and eBay

Gathered 2026-10-17 from the sandbox. Test fixture: BGG thing 343322, [EXIT: The Game – Advent Calendar: The Hunt for the Golden Book](https://boardgamegeek.com/boardgame/343322/exit-the-game-advent-calendar-the-hunt-for-the-gol).

No request in this session hit the sandbox firewall. Every block below came from the remote site (Cloudflare on boardgamegeek.com, an "Error Page | eBay" 403 on developer.ebay.com HTML docs), not from a "Blocked by network policy" 403.

## Summary

- The BGG XML API now requires a registered application and a Bearer token. Unauthenticated calls return `401 Unauthorized. See https://boardgamegeek.com/using_the_xml_api`. Registration is manual and BGG says approval can take a week or more.
- BGG version data for 343322 has no UPC, EAN or GTIN anywhere. Versions carry a `productcode` field, which holds the publisher's catalog number (KOSMOS `681951`, 999 Games `999-EXI22`) when filled at all. The README's "some carry UPCs" doesn't hold for the fixture. GameUPC (a separate service) maps UPC to BGG id, the opposite direction.
- The BGG HTML page is behind a Cloudflare challenge for both curl and headless Chromium. BGG's internal JSON API at `api.geekdo.com/api/...` answered without auth and has everything we want, but BGG's terms grant no license for it.
- eBay Browse needs only an application token from the client credentials grant, scope `https://api.ebay.com/oauth/api_scope`. Default limit is 5,000 calls/day; token minting is capped at 1,000/day and a token lasts 2 hours.
- Browse search supports `gtin` and `epid`. Neither can be combined with `q`. Search summaries do include `shippingOptions[].shippingCost`, and the `X-EBAY-C-ENDUSERCTX: contextualLocation=country=US,zip=NNNNN` header makes it zip-specific.
- Browse gives no sales tax amount. getItem has a `taxes` array (jurisdiction, percentage, flags), not a dollar figure.
- The Catalog API maps UPC or keywords to ePID, but it needs a **user** token (authorization code grant), not an application token.
- Open risk: eBay's docs mark the Buy APIs, Browse included, with "require an additional license", and the Buy requirements page says production use is "intended for eBay partners only" via the eBay Partner Network. Check this as soon as keys exist.

## A. BoardGameGeek

### Registration and tokens

BGG's "Using the XML API" page (version date 2025-07-02) says:

> Registration and authorization is required for use of the XML API. To register your application, go to https://boardgamegeek.com/applications , and click the button to create an application. Please be patient regarding a response--it may be a week or more before we get back to you.

> Along with registration, we are introducing Authorization tokens, to enforce registration. Once you have an approved application, you can create Tokens for it by going to https://boardgamegeek.com/applications and clicking "Tokens" by your application. To use a token, you must send your HTTPS request to BGG with an Authorization header, as follows: `Authorization: Bearer e3f8c3ff-...` ... If your token does not seem to be working, please ensure that you are making your requests to the correct domain (boardgamegeek.com, WITHOUT a leading www)

Other points from the same page:

- Two license types, commercial and non-commercial. A purely non-commercial app "may be eligible for a non-commercial license ... generally provided at no cost". Buy Assistant is a private household tool, so non-commercial fits.
- BGG may refuse apps that compete with "any part of BGG's business". Our later plan to watch GeekMarket listings is worth describing carefully in the application.
- Public-facing apps must show the "Powered by BGG" logo. Ours sits on a tailnet, so this likely doesn't apply, but the app page could show it anyway.
- "Using other parts of our API": BGG has "several other private APIs used by our website. Unless otherwise noted or authorized, we are granting no license for use of those endpoints." This covers `api.geekdo.com/api/...` below.

Source: <https://boardgamegeek.com/using_the_xml_api>. The page returns a Cloudflare challenge to curl, WebFetch and headless Chromium from this sandbox, so I read the Wayback Machine copy captured 2026-09-24: <https://web.archive.org/web/20260924202844/https://boardgamegeek.com/using_the_xml_api>.

### Rate limits

- The usage page says "We are still determining exact usage limits for the XML API". It asks for server-side requests with caching, and shows per-app usage under "Usage" at boardgamegeek.com/applications. Same source as above.
- The BGG_XML_API2 wiki says: "if you send requests too frequently, the server will give you 500 or 503 return codes ... Currently, a 5-second wait between requests seems to suffice." Source: <https://boardgamegeek.com/wiki/page/BGG_XML_API2>, read via Wayback capture 2026-09-08: <https://web.archive.org/web/20260908020651/https://boardgamegeek.com/wiki/page/BGG_XML_API2>.
- The same wiki documents `thing?id=...&versions=1` as "Returns version info for the item", with up to 20 ids per call.

For our volume (a handful of items, fetched once on add) a 5-second gap is plenty.

### Live call: XML API without a token

```
$ curl -sS 'https://boardgamegeek.com/xmlapi2/thing?id=343322&versions=1'
HTTP/2 401
content-type: text/plain
server: cloudflare

Unauthorized. See https://boardgamegeek.com/using_the_xml_api
```

`https://api.geekdo.com/xmlapi2/thing?id=343322&versions=1` also returns 401 with an empty body. I stopped live XML API calls there.

What a version looks like in XML, from a third-party test fixture (tnaskali/bgg-api, `src/test/resources/responses/bgg/xmlapi/v2/thing/200_OK.xml`, not captured by me):

```xml
<versions>
  <item type="boardgameversion" id="111618">
    <thumbnail>https://cf.geekdo-images.com/...pic98927.jpg</thumbnail>
    <image>https://cf.geekdo-images.com/...pic98927.jpg</image>
    <link type="boardgameversion" id="1001" value="Vendetta" inbound="true"/>
    <name type="primary" sortindex="1" value="Borras Spanish edition" />
    <link type="boardgamepublisher" id="4680" value="Borras Plana S.A." />
    <yearpublished value="0" />
    <productcode value="" />
    <width value="0" /><length value="0" /><depth value="0" /><weight value="0" />
    <link type="language" id="2203" value="Spanish" />
  </item>
</versions>
```

There is no UPC/GTIN element; `productcode` is the only identifier.

### Live call: BGG's internal JSON API (works, unlicensed)

The BGG site's own frontend reads `api.geekdo.com/api/...`. Three requests, no auth, all 200:

1. `GET https://api.geekdo.com/api/geekitems?objectid=343322&objecttype=thing&nosession=1`
2. `GET https://api.geekdo.com/api/geekitems?objectid=595004&objecttype=version&nosession=1`
3. `GET https://api.geekdo.com/api/geekitem/linkeditems?ajax=1&linkdata_index=boardgameversion&nosession=1&objectid=343322&objecttype=thing&pageid=1&showcount=25&sort=yearpublished&subtype=boardgameversion`

Thing 343322 (call 1, trimmed):

```json
{"item": {
  "objectid": 343322,
  "name": "EXIT: The Game – Advent Calendar: The Hunt for the Golden Book",
  "yearpublished": "2021",
  "minplayers": "1", "maxplayers": "99",
  "imageurl": "https://cf.geekdo-images.com/nNVkEVfayrcrfOzFs_14Bw__itemrep/img/.../pic6632318.jpg",
  "links": {
    "boardgamepublisher": [{"name": "KOSMOS", "objectid": "37"}, {"name": "999 Games", "objectid": "267"}, "..."],
    "boardgameversion":   [{"name": "Danish edition", "objectid": "736790"}, "..."]
  }}}
```

Other top-level keys: `description`, `short_description`, `images` (thumb/micro/square/etc.), `alternatenames`, `walmart_id` (null here), `bggstore_product` (null), `targetco_url` (empty), `website`, `canonical_link`, and a `links` map with designers, artists, families, categories, mechanics, expansions, honors.

Publishers: KOSMOS, 999 Games, Brädspel.se, IELLO, Spilbræt.dk. Designers: Inka and Markus Brand.

All seven versions (call 3):

| version id | name | year | publisher | productcode |
|---|---|---|---|---|
| 572700 | German edition | 2021 | KOSMOS | (empty) |
| 595004 | English edition | 2022 | KOSMOS | `681951` |
| 736790 | Danish edition | 2022 | Spilbræt.dk | (empty) |
| 670875 | Swedish edition | 2023 | Brädspel.se | (empty) |
| 666243 | French edition | 2023 | IELLO, KOSMOS | (empty) |
| 682152 | Dutch edition | 2023 | 999 Games | `999-EXI22` |
| 767371 | Dutch edition | 2025 | 999 Games | `999-EXI22` |

English edition (call 2, trimmed):

```json
{"item": {
  "objectid": 595004, "objecttype": "version",
  "name": "EXIT: The Game – Advent Calendar: The Hunt for the Golden Book (English edition)",
  "versionname": "English edition",
  "yearpublished": "2022", "releasedate": "2022-10-00", "releasestatus": "released",
  "productcode": "681951",
  "length": "17.7559", "width": "12.9134", "depth": "2.12598", "weight": "0",
  "links": {"boardgamepublisher": ["KOSMOS"], "languages": ["English"]}}}
```

A grep of all three responses for `upc`, `gtin`, `ean`, `barcode` and `isbn` found nothing. `681951` is KOSMOS's six-digit article number, not a 12-digit UPC. It could still help as an eBay keyword (`q=681951`) or an MPN.

Caveat: these endpoints are undocumented and BGG's terms grant no license for them. Fine for a one-off probe; for the extractor, use the XML API with a token, or ask BGG in the application whether this JSON is allowed.

### UPC lookup: GameUPC

GameUPC (<https://gameupc.com>, OpenAPI spec at <https://gameupc.com/gameupc-oas.yaml>) is a crowd-verified UPC-to-BGG map. `GET https://api.gameupc.com/test/upc/{barcode}` returns `bgg_info[]` with BGG `id`, name, images and a `confidence` score, plus `bgg_info_status` (`verified` or `choose_from_bgg_info_or_search`). There is also `/upc/{upc}/bgg_id/{bgg_id}/version/{bgg_version}`. Auth is an `x-api-key` header; `test_test_test_test_test` works on the `/test` server, which "will be wiped periodically", and production `/v1` needs a key by email. It goes UPC to BGG, not BGG to UPC, so it can verify a UPC we already have but can't supply one. I read the spec only, with no lookups.

### The HTML page

- curl with a browser User-Agent: `HTTP/2 403`, `cf-mitigated: challenge`, body "Just a moment... Enable JavaScript and cookies to continue". That is Cloudflare's managed challenge, not the sandbox firewall.
- `web-shot` (headless Chromium), 8 s wait: "Sorry, you have been blocked ... This website is using a security service to protect itself from online attacks", Cloudflare Ray ID a4c186a54b682320. The page never loaded, so I couldn't check for `GEEK.geekitemPreload`.
- Conclusion: scraping BGG HTML from this sandbox doesn't work. The data that preload would hold matches the `api.geekdo.com/api/geekitems` response above, which is what the page's own JavaScript fetches.

## B. eBay Browse API

Primary sources: eBay's published OpenAPI contract for Browse v1.20.4 (<https://developer.ebay.com/api-docs/master/buy/browse/openapi/3/buy_browse_v1_oas3.json>) and Catalog v1_beta.5.3 (<https://developer.ebay.com/api-docs/master/commerce/catalog/openapi/3/commerce_catalog_v1_beta_oas3.json>), both fetched with curl. The HTML doc pages return 403 bot pages to curl and WebFetch, so I rendered them with `web-shot`. Quotes below come from those two sources.

### Auth: client credentials

- "All methods in the Browse API require an Application access token, which is obtained using the client credentials grant flow." (<https://developer.ebay.com/api-docs/buy/browse/overview.html>)
- Scope for search, getItem and getItemByLegacyId: `https://api.ebay.com/oauth/api_scope`. getItems (batch) needs `https://api.ebay.com/oauth/api_scope/buy.item.bulk` and is "(Limited Release) available only to select Partners". (OpenAPI `security` per operation; getItems description.)
- Token request (<https://developer.ebay.com/api-docs/static/oauth-client-credentials-grant.html>):

  ```
  POST https://api.ebay.com/identity/v1/oauth2/token
  Content-Type: application/x-www-form-urlencoded
  Authorization: Basic <base64(client_id:client_secret)>

  grant_type=client_credentials&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope
  ```

  Response: `{"access_token": "v^1.1#...", "expires_in": 7200, "token_type": "Application Access Token"}`. The page tells apps to "store this token in a static variable and re-use the token while it is valid."
- Token endpoint limit: client_credentials grant, 1,000 requests/day (same page, "Access token rate limits"). At one token per 2 hours we'd use 12.

### Call limits and access

- Buy API call limits page (<https://developer.ebay.com/develop/api/buy/api_call_limits>): "Browse API* All methods except getItems: 5000 API calls per day; getItems: 5000 API calls per day". Catalog: 10,000/day. The footnote reads "* Buy APIs require an additional license. See the API documentation for details."
- Buy APIs requirements (<https://developer.ebay.com/api-docs/buy/static/buy-requirements.html>): "Many of the Buy APIs are a (Limited Release). The use of eBay's Buy APIs in production is intended for eBay partners only. You must apply for production access through the eBay Partner Network." It also says "Anyone with an eBay developer account can use the Buy APIs in the sandbox".
- The Buy APIs overview (<https://developer.ebay.com/api-docs/buy/static/buy-overview.html>) tags Deal, Feed beta, Offer and Order as "(Limited Release)". It doesn't tag Browse.

These pages disagree with each other. My reading is that Browse search and getItem work in production with a plain keyset, and the partner process covers the Limited Release APIs and checkout. That's an inference. Confirm it on the first production call with new keys. If Browse turns out to be gated, the EPN application takes "within 10 business days" plus a support ticket and contracts (same requirements page).

5,000 calls/day is far more than we need: about 20 items × 1 search/day, plus getItem for shipping on new candidates.

### search: `GET /buy/browse/v1/item_summary/search`

From the OpenAPI parameter descriptions:

- `q`: keywords, max 100 chars. Space means AND. `(a, b)` means OR. No `*` wildcard. "Do not include an epid or gtin parameter value as neither can be used in conjunction with a keyword search."
- `gtin`: "Supported GTIN types are UPC, EAN, and ISBN", e.g. `gtin=099482432621`. It "may be combined with an epid value, but neither an epid or gtin value should be combined with a keywords search through the q parameter."
- `epid`: "limits the results to only items in the specified ePID ... Use the product_summary/search method in the Catalog API to search for the ePID". Can't be used with `q`.
- `category_ids`: one ID per request for now. It can be combined with either epid/gtin or q. A top-level (L1) category requires `q`. Look up the board games category ID through the Taxonomy API or `fieldgroups=ASPECT_REFINEMENTS`, which returns `dominantCategoryId`.
- `limit` 1 to 200 (default 50), `offset` must be a multiple of `limit`, and a search returns at most 10,000 items.
- `sort`: `price` (ascending by price **plus shipping**; eBay "highly recommended" sending the contextualLocation header with it), `-price`, `distance`, `newlyListed`, `endingSoonest`. Default is Best Match.
- `fieldgroups`: `MATCHING_ITEMS` (default), `EXTENDED` (adds `shortDescription`, `itemLocation.city`), `ASPECT_REFINEMENTS`, `BUYING_OPTION_REFINEMENTS`, `CATEGORY_REFINEMENTS`, `CONDITION_REFINEMENTS`, `FULL`.
- Default results include only listings with `FIXED_PRICE` as a buying option. Auctions need `filter=buyingOptions:{AUCTION|FIXED_PRICE}`. A Buy It Now auction drops `FIXED_PRICE` once it gets a bid, so without that filter it disappears from results.

Filters, from <https://developer.ebay.com/api-docs/buy/static/ref-buy-browse-filters.html>. Combine them with commas: `filter=price:[10..50],priceCurrency:USD,conditions:{USED}`.

| filter | syntax | notes |
|---|---|---|
| conditions | `conditions:{NEW\|USED}` | values NEW, USED, UNSPECIFIED |
| conditionIds | `conditionIds:{1000\|1500}` | finer grades, e.g. 1000 New, 5000 Good |
| price | `price:[10..50]`, `price:[..50]` | must be paired with `priceCurrency:USD` |
| buyingOptions | `buyingOptions:{FIXED_PRICE\|BEST_OFFER\|AUCTION}` | "defined at the leaf category level"; with a top-level category it is ignored |
| deliveryCountry | `deliveryCountry:US` | only items that ship there |
| deliveryPostalCode | `deliveryPostalCode:95125` | requires `deliveryCountry` |
| maxDeliveryCost | `maxDeliveryCost:0` | free shipping only; 0 is the only value |
| itemLocationCountry | `itemLocationCountry:US` | |
| itemEndDate | `itemEndDate:[..2026-11-01T00:00:00Z]` | |
| sellers / excludeSellers | `excludeSellers:{name1\|name2}` | |

The same filters page notes: "The Marketplace Insights API is restricted and not open to new users at this time." That settles the README's sold-price question for Insights.

### Shipping cost to a zip

Header format, from the Browse guide's "Use request headers" section (<https://developer.ebay.com/api-docs/buy/static/api-browse.html>):

> Although the X-EBAY-C-ENDUSERCTX header containing contextualLocation is optional, it is strongly recommended ... This header increases the accuracy of the estimated delivery window information and is needed for the calculated shipping information. When using this header, you must always include the country code and the zip code if zip codes are used in that country.

```
X-EBAY-C-ENDUSERCTX: contextualLocation=country%3DUS%2Czip%3D19406
```

The inner `=` and `,` must be URL-encoded. A malformed header gives error 11020.

Where the cost appears:

- **search** returns `itemSummaries[].shippingOptions[]` (type `ShippingOptionSummary`) with `shippingCost {value, currency}`, `shippingCostType` (`FIXED` or `CALCULATED`), and `minEstimatedDeliveryDate`/`maxEstimatedDeliveryDate`. So search does return shipping, and with the header the cost is for that zip. `sort=price` also uses item plus shipping.
- **getItem** returns `shippingOptions[]` (type `ShippingOption`) with more detail: `shippingCost` ("The final shipping cost for all the items after all discounts are applied"), `shippingCostType`, `shippingServiceCode`, `shippingCarrierCode`, `type` (STANDARD, EXPEDITED, ...), `additionalShippingCostPerUnit`, `importCharges`, and `shipToLocationUsedForEstimate {country, postalCode}`, which echoes the zip eBay used. "For items with calculated shipping, this array is only returned if the X-EBAY-C-ENDUSERCTX header is supplied." getItem also takes `quantity_for_shipping_estimate`. Warning 11510 means "There was a problem calculating the shipping cost."

Plan: send the header on every search with the item's zip. Store the summary's `shippingCost`. Call getItem only for a candidate whose summary lacks a cost, or before an alert, to confirm.

### Sales tax

- No Browse response carries a tax **amount**. `ItemSummary` has no tax field at all.
- getItem's `taxes[]` (type `Taxes`) has `taxJurisdiction {region, taxJurisdictionId}`, `taxType`, `taxPercentage`, `includedInPrice`, `shippingAndHandlingTaxed`, and `ebayCollectAndRemitTax` ("eBay will collect tax ... and remit the tax to the taxing authority of the buyer's residence"). The OpenAPI schema says nothing about whether this is tied to the contextualLocation zip.
- `price` and `shippingCost` include VAT only for VAT marketplaces such as EBAY_GB. That doesn't apply to US sales tax.

The README's plan (our own tax rate per zip in settings) stays correct. Whether to tax shipping too is a per-state question the docs don't answer; `shippingAndHandlingTaxed` may help once we see real responses.

### Ended listings

- `Item.itemEndDate`: "A timestamp that indicates the date and time an auction listing will end. If a fixed-price listing has ended, this field indicates the date and time the listing ended."
- `EstimatedAvailability.estimatedAvailableQuantity`: "If the item has an EndDate in the past, or the estimatedAvailabilityStatus is OUT_OF_STOCK, the item is unavailable for purchase." `estimatedAvailabilityStatus` is `IN_STOCK`, `LIMITED_STOCK` or `OUT_OF_STOCK`. It also has `estimatedSoldQuantity` and `estimatedRemainingQuantity`.
- getItem errors (rendered method page, <https://developer.ebay.com/api-docs/buy/browse/resources/item/methods/getItem>): 11001 "The specified item ID was not found" (400/404), 11004 "The item is not available for purchase ... such as when the listing is being updated by the seller" (400/409).

These fields suggest getItem can return a recently ended fixed-price listing with a past `itemEndDate` and `OUT_OF_STOCK`. The docs never say how long that lasts, or whether an older ended listing gives 11001 instead. Check this live. Either way, nothing in Browse says "sold" versus "expired". `estimatedSoldQuantity` on a single-quantity listing might be the only clue, also unverified.

For refresh polling, use `getItem?fieldgroups=COMPACT`, meant to "quickly check if the availability or price of the item has changed". It returns itemId, legacyItemId, price, bid fields, estimatedAvailabilities, gtin, itemCreationDate, itemWebUrl, sellerItemRevision, shippingOptions, taxes and a few flags. It does not list `itemEndDate`, so ended-ness has to come from `estimatedAvailabilities` or an error.

### Item fields worth storing

| field | in search | in getItem | notes |
|---|---|---|---|
| itemId | yes | yes | RESTful id `v1\|<legacy>\|0`, or `v1\|<legacy>\|<variation>` |
| legacyItemId | yes | yes | the number in ebay.com/itm/ URLs; `getItemByLegacyId` converts it back |
| title | yes | yes | max 80 chars |
| price {value, currency} | yes | yes | |
| currentBidPrice, bidCount | auctions | auctions | |
| buyingOptions | yes | yes | FIXED_PRICE, AUCTION, BEST_OFFER, CLASSIFIED_AD |
| condition, conditionId | yes | yes | getItem adds conditionDescription |
| itemCreationDate, itemOriginDate | yes | creation only | origin survives relists |
| itemEndDate | yes | yes | |
| seller {username, feedbackScore, feedbackPercentage, sellerAccountType} | yes | yes | from 2025-09-26 "select developers" get an immutable user id in place of the US username |
| image.imageUrl, thumbnailImages, additionalImages | yes | yes | |
| itemWebUrl | yes | yes | itemAffiliateWebUrl only for EPN affiliates |
| epid | yes | yes | only when the listing is tied to a catalog product |
| gtin, mpn, brand | no | yes | `fieldgroups=PRODUCT` adds a `product` block with `gtins[]`, `mpns[]` |
| shippingOptions | yes (summary) | yes (full) | see above |
| itemLocation | yes | yes | postal code, country |
| estimatedAvailabilities | no | yes | |
| taxes | no | yes | |
| lotSize | no | yes | flags "lot of 3" listings |

Shape of a search result, assembled from the OpenAPI schema field names (not a captured response, since we have no keys):

```json
{"total": 12, "limit": 50, "offset": 0,
 "itemSummaries": [{
   "itemId": "v1|1234567890|0", "legacyItemId": "1234567890",
   "title": "EXIT The Game Advent Calendar Hunt for the Golden Book KOSMOS",
   "price": {"value": "19.99", "currency": "USD"},
   "condition": "New", "conditionId": "1000",
   "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"],
   "shippingOptions": [{"shippingCostType": "CALCULATED",
                        "shippingCost": {"value": "6.45", "currency": "USD"}}],
   "seller": {"username": "...", "feedbackScore": 512, "feedbackPercentage": "99.8"},
   "image": {"imageUrl": "https://i.ebayimg.com/..."},
   "itemWebUrl": "https://www.ebay.com/itm/1234567890",
   "epid": "...", "itemEndDate": "2026-11-01T18:00:00.000Z",
   "itemLocation": {"postalCode": "940**", "country": "US"}}]}
```

### Catalog API: UPC or name to ePID

From the Catalog OpenAPI spec and overview (<https://developer.ebay.com/api-docs/commerce/catalog/overview.html>):

- `GET /commerce/catalog/v1_beta/product_summary/search` takes `q`, `gtin` (comma-separated UPC/EAN/ISBN), `mpn`, `category_ids` and `aspect_filter`. "must include at least the q parameter, or the category_ids, gtin, or mpn parameter". `gtin` can't be combined with `q` or `aspect_filter`. `limit` max 200.
- Each `productSummaries[]` entry has `epid`, `title`, `brand`, `gtin`, `upc`, `ean`, `isbn`, `mpn`, `image`, `aspects` and `productWebUrl`. `GET /product/{epid}` returns full details.
- **Access restriction**: the only security flow in the spec is `authorizationCode` (a user token), with scope `commerce.catalog.readonly` or `sell.inventory`. An application token from client credentials won't work. Jason would have to sign in to eBay once through the consent flow, and the app would keep a refresh token (refresh grant limit 50,000/day, per the client credentials page's rate-limit table).
- Limits: 10,000 calls/day per app, 100,000/day per user. Marketplaces: AU, CA (en), DE, ES, FR, GB, IT, US.
- The API is built for sellers matching inventory, and it stayed v1_beta.

A cheaper route that skips Catalog: run Browse search with `gtin=<UPC>` or a keyword query, then read `epid` off the matching summaries. That needs no user token.

## Open questions to settle with real keys

1. Does a plain production keyset get Browse search and getItem, or does eBay ask for EPN approval first?
2. What does getItem return for a fixed-price listing that ended a day ago, a week ago, a month ago: 200 with a past `itemEndDate`, or 11001?
3. Does `taxes[]` change with the contextualLocation zip?
4. For the fixture, does `gtin` search find anything? We don't have its UPC yet. A keyword search for the title or `681951` would show it from listing data.
5. Will BGG approve a non-commercial app for this use, and how long does it take? Apply early, since it's on the path for the reference-link extractor.
