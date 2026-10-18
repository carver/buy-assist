# Estimating shipping for sales that don't record it

Researched 2026-10-17 from the dev sandbox. Question: when a sold price has no shipping attached (every BGG GeekMarket sale), how well can we estimate what a buyer at our ZIP would pay, so the item page can show a typical all-in price? Test item: EXIT: The Game Advent Calendar: The Hunt for the Golden Book, BGG 343322, English edition 595004. Ship-to ZIP 94110.

No request hit the sandbox firewall. Every call below returned 200, or 401 from the remote API.

## Summary

- **The rate table works, and it's the estimator to build first.** USPS Ground Advantage prices are public (Notice 123), zones from any origin to our 3-digit ZIP come from an unauthenticated USPS endpoint, and BGG gives the box size. For the test item the package bills at 3 lb with no dimensional weight. A buyer at 94110 pays about **$10 to $16 with a commercial label** (Pirate Ship, eBay, PayPal labels), or $14 to $23 if the seller pays post-office retail.
- **BGG tells us where each GeekMarket seller ships from.** The active-listings endpoint also returns sold listings, each with the seller's state and city and a `listdate` that matched all five price-history sales exactly. Seller notes sometimes give the ship-from ZIP outright ("Buyer pays shipping from 98036"). Knowing the origin narrows the estimate from a $7 spread to one zone's price.
- **GeekMarket carries no shipping cost field.** A product has `shiplocation` (US only, or a list of regions) and free-text notes. Sellers write "All prices are without sending fees" and "Buyer pays shipping".
- **eBay Browse gives real quotes to our ZIP**, but only for active eBay listings, and only once we have keys. It's a good cross-check for the rate table and a poor estimator on its own, because free-shipping listings report $0.
- **A median of pasted eBay shipping is the wrong tool.** Free-shipping sales report $0 with the cost folded into the price, and international sales add outliers. For eBay sales, add each sale's own shipping to its own price. Use the rate table only for sales that have no shipping.
- **UPS and FedEx Ground don't matter for this kind of item.** UPS bills dimensional weight on every package (divisor 139), adds a $6.10 residential surcharge, and lands at $20 to $30 or more. Sellers of a 2 to 3 lb game use USPS.
- Worked estimate for the test item: GeekMarket sales of $35 to $40 come to **about $50 to $56 all-in at 94110**, median $52.

## 1. eBay sold listings pasted by hand

The paste fixture (`tests/data/ebay_sold_paste.txt`, five rows) shows the usual mix: "+$5.85 delivery", "Free delivery" twice, "+$1.25" on a parts lot, and "+$14.00" from Germany. `paste.py` already stores `shipping_cents`, with 0 for free and null when not shown.

How a median of pasted shipping would behave:

- **Free shipping pulls it to zero.** Many eBay sellers price shipping into the item. Two of five fixture rows are free, so the median shipping is $1.25 or $5.85 depending on what gets filtered, which says nothing about the cost of moving a 3 lb box.
- **Seller-paid shipping is common even when it isn't free.** $5.85 for a US sale is below the cheapest commercial Ground Advantage price for a 3 lb parcel in any zone ($8.64 at zone 1, July 2026 prices). That seller either absorbed part of the postage or shipped it lighter than the box allows.
- **Outliers.** International rows ($14 from Germany) and parts lots ($1.25) need filtering first, and the paste flow already unchecks most of them.

What to do with pasted rows: use `price + shipping` for each sale as its all-in figure. Don't compute a median shipping and add it to some other price. Once we have pasted a few dozen US sales with calculated shipping, they are the best test set we'll have for the rate-table estimator in section 3.

## 2. eBay Browse API

What the docs say (Browse OpenAPI v1.20.4, <https://developer.ebay.com/api-docs/master/buy/browse/openapi/3/buy_browse_v1_oas3.json>, fetched 2026-10-17; more in `apis.md`):

- `item_summary/search` returns `itemSummaries[].shippingOptions[]` (`ShippingOptionSummary`) with `shippingCost` ("This is the estimated price to ship the item") and `shippingCostType`, "FIXED (flat-rate shipping) and CALCULATED (shipping cost calculated based on item and buyer location)".
- `getItem` returns `shippingOptions[]` (`ShippingOption`) with `shippingCost` ("The final shipping cost for all the items after all discounts are applied"), `shippingCarrierCode`, `shippingServiceCode`, `type` (STANDARD, ECONOMY, ...) and `shipToLocationUsedForEstimate`, which echoes the ZIP eBay used.
- The ZIP comes from the `X-EBAY-C-ENDUSERCTX: contextualLocation=country%3DUS%2Czip%3D94110` header. Without it, calculated shipping is missing or generic.
- `itemLocation` has `postalCode`, `city`, `stateOrProvince` and `country`, so each listing's origin is known too.
- No Browse schema has a package weight or dimension field. I searched the spec for `weight`, `dimension` and `package` and found none.

As an estimator for items we can't see shipping for:

- **For the same item**, the median `shippingCost` across active US listings, dropping $0 and `fulfilledThrough` international ones, is what eBay itself would charge us to 94110. It reflects real seller behaviour (handling fees, Priority upgrades) that the rate table can't.
- **For other items**, it's only as good as the listing count. The test item has thin eBay supply, and a board game of a different size isn't comparable.
- **Free-shipping listings have to be dropped, not counted as $0**, for the same reason as in section 1. That leaves fewer data points.
- No keys yet, so none of this is tested. Store `shippingCost`, `shippingCostType` and `itemLocation.postalCode` on every listing from day one. With the rate table we can then compare quote against estimate per listing, a free validation set that grows daily.

## 3. Package size and weight with USPS, UPS rate tables

### BGG dimensions: inches and pounds

The BGG version data (`tests/data/bgg_versions_343322.json`) gives the English, German, Danish and Dutch 2025 editions the same box: length `17.7559`, width `12.9134`, depth `2.12598`. Only the Dutch 2025 edition (767371) has a weight: `1.87393`. The English edition says `"weight": "0"`, meaning unknown.

The units are inches and pounds. Each number is a round metric figure converted: 17.7559 in = 45.10 cm, 12.9134 in = 32.80 cm, 2.12598 in = 5.40 cm, 1.87393 lb = 0.8500 kg. A contributor entered 45.1 × 32.8 × 5.4 cm and 850 g, and BGG stored them in imperial. A retailer listing for KOSMOS 681951 agrees: "17.7 x 13 x 2.2 in", 1.8 lb ([Pufferbellies](https://pufferbelliestoys.com/products/exit-advent-calendar-the-hunt-for-the-golden-book)).

Fallback when the chosen version has no weight: take it from a sibling version with identical dimensions, as here. Most of this item's versions have zeros, so expect gaps; when nothing has a weight, ask the person, or use a density from other BGG versions that carry both size and weight (we have only one such pair so far, 0.0038 lb/in³, and an advent calendar is mostly air, so don't trust that number for normal games).

### Packed size and billable weight

My packing assumption, not a sourced figure: a box 1 inch larger each way and about 0.6 to 1 lb of box and padding. For the test item, that's roughly 19 × 14 × 4 in and 2.5 to 2.9 lb.

USPS Ground Advantage rules (DMM 133, <https://pe.usps.com/text/dmm300/133.htm>):

- Weight: "Per pound from more than 1 pound through 70 pounds. Any fraction of a pound is considered a whole pound." So 2.5 to 2.9 lb bills as **3 lb**. Only a bare-box mailer under 2 lb total would bill at 2 lb.
- Dimensional weight applies only to parcels "exceeding 1 cubic foot (1,728 cubic inches)". Since July 12, 2026 the divisor is **139** ("divide the result by 139 and round up", marked `[7-12-26]`). 19 × 14 × 4 = 1,064 in³, and even 20 × 15 × 5 = 1,500 in³. **No dim weight for this item.**
- Nonstandard fees start at more than 22 inches long ($4.50) or more than 2 cubic feet ($21). The item is 17.8 in long and 19 or so packed, so no fee.

### USPS Ground Advantage prices

Source: Notice 123 price list, <https://pe.usps.com/text/dmm300/Notice123.htm>, "Effective October 04, 2026". I compared it with the Wayback capture from 2026-09-28 (<https://web.archive.org/web/20260928154835/https://pe.usps.com/text/dmm300/Notice123.htm>, "Effective July 12, 2026"). The October list is the July list plus the holiday surcharge USPS announced on 2026-08-25 ([USPS release](https://about.usps.com/newsroom/national-releases/2026/0825-usps-announces-temporary-price-change-for-2026-holiday-shipping-season.htm)), in force 2026-10-04 until 2027-01-17. For 0 to 3 lb that's +$0.50 retail in zones 1 to 4 and +$0.75 in zones 5 to 9; commercial is +$0.40 and +$0.55. The diff of the two lists confirms those amounts.

3 lb parcel, by zone (2 lb in parentheses for the lightest-packing case):

| Zone | Retail now | Retail from Jan 17 | Commercial now | Commercial from Jan 17 |
|---|---|---|---|---|
| 1 | 11.80 (11.30) | 11.30 | 9.04 (8.39) | 8.64 |
| 2 | 12.50 (12.00) | 12.00 | 9.06 (8.48) | 8.66 |
| 3 | 13.15 (12.70) | 12.65 | 9.54 (8.66) | 9.14 |
| 4 | 14.20 (13.50) | 13.70 | 10.07 (8.91) | 9.67 |
| 5 | 15.70 (14.85) | 14.95 | 12.12 (10.50) | 11.57 |
| 6 | 17.20 (15.85) | 16.45 | 14.14 (12.13) | 13.59 |
| 7 | 19.70 (17.20) | 18.95 | 14.91 (12.55) | 14.36 |
| 8 | 23.15 (19.80) | 22.40 | 16.30 (13.42) | 15.75 |

Retail is what the post office counter charges. Commercial is what a seller pays for a label bought online (eBay labels, Pirate Ship, PayPal), and most hobby sellers who ship regularly buy those. Retail runs $4 to $7 higher at 3 lb. Commercial cubic pricing doesn't help here: a 0.6 to 0.7 ft³ box costs more by cube than by weight in every zone (zone 8: $21.41 to $22.62 against $16.30).

Flat-rate Priority boxes are out: the box is too long for them.

### Zones to 94110

`GET https://postcalc.usps.com/DomesticZoneChart/GetZoneChart?zipCode3Digit=941&shippingDate=10%2F17%2F2026` returns the zone chart for 941, no auth, `"EffectiveDate":"October 1, 2026"`. Zones are symmetric between 3-digit pairs, so this one chart gives the zone from any seller to us.

| Seller origin | Zone to 941 |
|---|---|
| Sacramento (958) | 2 |
| Los Angeles (900), Portland (972) | 4 |
| Lynnwood WA (980), Phoenix (850), Denver (802) | 5 |
| Austin (787) | 7 |
| Chicago, New York, Boston, Miami, Nashville, Mississippi, Louisiana | 8 |

By count of 3-digit prefixes, 56% are zone 8 and 19% zone 7; zones 1 to 4 are 10%. That counts ZIP areas, not people, so it overstates zone 8, but the conclusion holds: a seller picked at random is usually zone 7 or 8 from San Francisco. Across that mix a 3 lb commercial label averages $14.89 and the median is $16.30 (zone 8). Retail averages $20.50.

### UPS Ground

UPS's Rate and Service Guide (<https://www.ups.com/assets/resources/webcontent/en_US/daily_rates.pdf>; the copy at that URL is the 2025 edition, effective 2025-03-31, so 2026 prices are higher):

- "Divide the cubic size in inches by 139 to determine the dimensional weight", with no 1 ft³ threshold. 19 × 14 × 4 / 139 = 7.7, so **8 lb billable**.
- UPS Ground daily rate at 8 lb, zones 2 to 8: $14.66 to $22.19. Residential surcharge for Ground: $6.10. Fuel surcharge on top.
- So $21 to $28 before fuel, against $10 to $16 commercial USPS. A household seller won't choose UPS for this box, and the estimator can ignore UPS and FedEx for games under about 10 lb. I didn't check FedEx.

## 4. BGG GeekMarket

Four requests to `api.geekdo.com`, 5 s apart, User-Agent `buy-assist/0.1 (household price tracker; ...)`. Raw responses are in the session scratchpad, not the repo.

`GET /api/market/products?objectid=343322&objecttype=thing&pageid=1` returned 9 products. Shipping-related fields per product:

- `itemlocation` / `itemlocation_code`: country only ("United States", "Estonia"). Two of nine are blank.
- `linkeduser`: the seller's `city`, `state` and `country` from their BGG profile, e.g. Mandeville, Louisiana; Lynnwood, Washington; Los Angeles, California. Some leave city or state blank.
- `linkeduserGeekMarket.itemsig`: the seller's standing signature. The Estonian seller's: "All prices are without sending fees. Write to me to find out how much it costs."
- No shipping cost field. `grep` for `ship`, `postage`, `deliver`, `zip` and `postal` in the list response found nothing.

`GET /api/market/products/{productid}` (tried 3851948 and 4115629) adds:

- `shiplocationraw`: `"usonly"` ("Will ship to the United States only.") or `"usandothers"` with `shipareas` (`["Europe", "USA", "European Union", "United Kingdom"]`).
- `notes`, `paymentmethod` (PayPal), `available`, `quantity`.
- Still no cost.

**The products list includes sold listings.** All nine have `productstate: "active"`, but 3851948 has `quantity: "0"` and `available: false`. Each of the five USD sales in `pricehistory` has a `listdate` equal to one product's `listdate`, which gives each sale's seller location:

| Sale | Price | Seller (from product) | Notes in price history | Zone to 941 |
|---|---|---|---|---|
| 2025-11-18 | $40 New | Mandeville, LA | | 8 |
| 2023-11-29 | $40 New | Lynnwood, WA | "Buyer pays shipping from 98036" | 5 |
| 2023-08-19 | $40 New | Los Angeles, CA | | 4 |
| 2023-05-04 | $40 Like New | Tennessee | | 8 |
| 2023-02-21 | $35 Very Good | Mississippi | "all shipping is done from 38821 ... For US buyers, most board games will ship for between $8-$15" | 8 |

The listdate join worked for 5 of 5 here. Check it on a second item before relying on it; two sellers listing the same second would break it.

Typical GeekMarket shipping, from what the data shows: the buyer pays actual postage on top of the price, quoted by the seller. One seller's 2023 note puts "most board games" at $8 to $15 in the US, which matches commercial Ground Advantage for 2 to 5 lb across zones.

## 5. Other cheap signals

- **USPS's own APIs.** The zone chart endpoint above needs no key. The USPS Domestic Prices 3.0 API (`POST https://apis.usps.com/prices/v3/base-rates/search`) quotes exact prices but returned 401 "Missing or malformed access token" without OAuth; it needs a free developer account at developers.usps.com. Not needed: Notice 123 changes two or three times a year and fits in a checked-in JSON file.
- **Pirate Ship / Shippo / EasyPost.** I found no documented public rate API for Pirate Ship. Shippo and EasyPost quote rates but need an account and key. They would return the same commercial USPS prices as the table above, so they add nothing for an estimate.
- **Seller notes.** Price-history `notes` and product `notes` sometimes carry a ship-from ZIP or a quoted shipping range. A 5-digit regex catches the ZIPs; `claude-json` could pull "$8-$15" style quotes, though they're rare.
- **Ship-to restrictions.** `shiplocationraw` tells us whether a listing ships to the US at all. Useful for filtering live GeekMarket listings, not for estimating cost.

## Worked estimate: test item to 94110

Inputs: box 17.76 × 12.91 × 2.13 in, 1.87 lb (Dutch 2025 edition, same box as English). Packed about 19 × 14 × 4 in, 2.5 to 2.9 lb, billed as 3 lb USPS Ground Advantage. No dimensional weight, no nonstandard fee.

Per GeekMarket sale, using the seller's state and today's commercial price:

| Sale | Item | Zone | Shipping est. | All-in est. | If retail |
|---|---|---|---|---|---|
| 2025-11-18 New | $40 | 8 | $16.30 | $56.30 | $63.15 |
| 2023-11-29 New | $40 | 5 | $12.12 | $52.12 | $55.70 |
| 2023-08-19 New | $40 | 4 | $10.07 | $50.07 | $54.20 |
| 2023-05-04 Like New | $40 | 8 | $16.30 | $56.30 | $63.15 |
| 2023-02-21 Very Good | $35 | 8 | $16.30 | $51.30 | $58.15 |

- Shipping: median $16.30, range $10.07 to $16.30 commercial.
- All-in, new: median $52.12 (3 sales). All-in, used: $51.30 and $56.30.
- With an unknown seller location: $14.89 expected, $16.30 median, commercial.
- After 2027-01-17 every commercial figure drops $0.40 to $0.55.

## Expected error

Not validated against real buyer-paid shipping yet. From the tables, the sources of error at 3 lb:

| Source | Size | Removable? |
|---|---|---|
| Zone unknown | $9 to $16 (zones 1 to 8) | Mostly, with seller state from GeekMarket or `itemLocation` from eBay |
| Commercial vs retail postage | +$4 to $7 | No. Default to commercial; mention retail in the tooltip |
| Weight tier (packing) | ±$0.50 to $3 per pound tier | Partly, with a real item weight |
| Seller absorbs postage, offers free shipping, or adds handling | Unknown; the eBay fixture's $5.85 is about $3 under any 3 lb commercial price | No |
| Price list changes | $0.40 to $0.75 this season | Yes, date the tables |

With the seller's location known, expect the commercial estimate within about $2 of what was charged, unless the seller paid retail ($4 to $7 more) or absorbed postage. Without location, about ±$4. On a $40 game that's ±5% to ±10% of the all-in price, small next to the spread between sold prices.

## Recommendation

Build first:

1. **A USPS Ground Advantage rate-table estimator.** Check Notice 123 commercial and retail prices into the repo as JSON, keyed by effective date, with weights 1 to 20 lb and zones 1 to 9 (more if a heavy item ever needs them). Fetch the zone chart for the home ZIP's 3-digit prefix from `postcalc.usps.com` once per price change and store it. Inputs: packed weight (item weight + 0.75 lb, rounded up; dim weight by the DMM 133 rule if packed volume tops 1,728 in³) and origin 3-digit ZIP or state. With no origin, use the zone mix for the home ZIP.
2. **Seller location for GeekMarket sales.** Save `linkeduser` city/state and `itemlocation` from the products endpoint, join to price-history sales on `listdate`, and pull 5-digit ZIPs from notes. A state is coarse for big western states (California spans zones 1 to 4 from 94110, Texas 5 to 7) but fine east of the Mississippi, where almost every prefix is zone 8 (387, in Mississippi, is zone 7).
3. **Item weight.** Take it from the chosen BGG version, then a sibling version with the same dimensions, then the reference page, then ask on the item form. Show "weight unknown, assumed 3 lb" when it's a guess.

Later, once eBay keys exist: store every Browse listing's `shippingCost` to the item's ZIP and its `itemLocation.postalCode`, and compare against the estimator. That's the validation set this research couldn't build. Pasted eBay sales with US calculated shipping are a second one.

Don't build: a median of pasted eBay shipping as an estimator, or anything for UPS or FedEx.

What to show on the item page:

- Per BGG sale: "$40 + ~$16 ship (est.)", with a tooltip: "USPS Ground Advantage, 3 lb, from Louisiana to 94110, online label price. Post office counter: ~$23."
- In the sold-price summary: "Typical all-in ~$52 (shipping ~$10-16, estimated)", and a separate marker when the item's weight was assumed.
- For eBay sales, the real shipping with no marker, since it's what the buyer paid.

Open questions for the admin:

- Commercial or retail as the default? I picked commercial because sellers who list online mostly buy labels online. Retail is the safer "won't be surprised" number.
- Is a 0.75 lb packing allowance right for the things the household buys? It's my guess and moves a few items across a pound tier.
