# deal-watch

Emails me when something on a couple of Argentine retail sites drops to 70% off
or more. Runs on GitHub Actions; no server.

## Sources

**electrooutlet.com.ar** — the full catalogue (~920 items). Crawled twice per
full run: unfiltered, then `filters=5,|41|,` ("Productos Outlet"), which is a
strict subset. Set membership tags each hit `Productos Outlet` or `Primera`.
Discount comes from each card's `badge-dto percent`, with a `tachado` vs
`precio-final` fallback. Items with no `tachado` have no offer and are skipped.

**fravega.com** — the `electrofans` collection (~1400 items). Never crawled in
full: the listing is sorted with `sorting=HIGHEST_DISCOUNT` and pages are walked
only until the discount falls below the threshold, normally 2 pages. Prices come
from the `__NEXT_DATA__` Apollo state, channel `fravega-ecommerce`. Product URLs
are recovered by matching each sku `code` against `/p/<slug>-<code>/` hrefs on
the same page.

## Two-tier schedule

| Tier | Cron | Work | Catches |
|---|---|---|---|
| quick | `*/5 * * * *` | 4 requests, ~8s | new listings, and every Fravega change |
| full | `23 */3 * * *` | 33 requests, ~90s | price drops on older electrooutlet stock |

Quick mode scans only electrooutlet's newest page, which is sound because the
listing is ordered `CustomDate` descending, so anything newly published is on
page 1. An item in the newest 50 overall that is an outlet item is necessarily
in the newest 50 outlet items, so the Outlet/Primera tag stays correct.

Fravega needs no full tier at all: sorting by discount means 2 pages already are
the complete set of items above the threshold.

GitHub delays scheduled workflows under load, "including the start of every
hour", so `*/5` realistically lands every 5-15 minutes.

## Alert conditions

An item alerts when it is at or above the threshold and one of:

- it was not in state before (newly listed)
- it was tracked below the threshold and crossed it (price drop)
- it was already alerted and the discount deepened

Once alerted at a given percentage it stays quiet until the discount deepens.
For Fravega, an item that drops below the threshold vanishes from the sorted
results, and that absence re-arms it.

## Caveat on Fravega discounts

Fravega's `listPrice` anchor is frequently inflated, far more so than
electrooutlet's. A moka pot listed as "was $68,894.97" is not really a $68k moka
pot. Treat a Fravega 70% as a lead, not a verdict. If it gets noisy: raise
`threshold_pct`, change `fravega_collection`, or set `sources.fravega` false.

## Configuration

Non-secret settings live in `config.json`. Anything personal comes from the
environment, so it stays out of this public repo:

| Env var | Repo secret | Meaning |
|---|---|---|
| `ELECTROOUTLET_SMTP_PASSWORD` | `SMTP_APP_PASSWORD` | Gmail app password |
| `DEAL_WATCH_SMTP_USER` | `SMTP_USER` | sending account |
| `DEAL_WATCH_EMAIL_TO` | `EMAIL_TO` | recipient |
| `DEAL_WATCH_STATE` | — | state file path; `.gz` writes gzipped |
| `DEAL_WATCH_THRESHOLD` | — | override the percent |
| `DEAL_WATCH_FRAVEGA_COLLECTION` | — | override the collection |

Running locally on macOS instead reads the password from the Keychain and posts
a desktop notification. Off macOS, notifications are skipped silently.

## Commands

    python watch.py                # full scan
    python watch.py --quick        # newest electrooutlet page only
    python watch.py --dry-run      # scan and print, send nothing, save nothing
    python watch.py --threshold 60
    python watch.py --bootstrap    # reset baseline to current stock, no alerts
    python watch.py --test-email

## State

`state.json.gz` holds one compact record per item keyed `<source>:<id>`:
percent, alerted percent, and first/last seen as dates. Dates rather than
timestamps keep the diff empty on runs where nothing changed, so the workflow
commits only when something actually moved. Gzip is written with a zeroed header
so identical content produces identical bytes.

A failed email deliberately does not mark hits as alerted, so a bad credential
re-alerts next run instead of swallowing the deal.

## Failure handling

A run aborts without touching state if a crawl is incomplete, returns too few
items, or fails to parse a discount on most cards. That is the guard against
silent breakage after a site redesign. One source throwing does not kill the
run; the other still reports and the failure is logged. Only both failing aborts.

## Site quirks worth knowing

electrooutlet:

- `recsPerPage` caps at 50 regardless of what you ask for.
- Out-of-range `page` values clamp to the last page instead of returning empty,
  so pagination must terminate on "no new ids", not on an empty page.
- ~18% of product hrefs contain a literal `&#160;`, which must be sent as
  `%C2%A0` or the link 404s. All URLs are percent-encoded.
- Sorting offers only name and price, no discount, which is why the full tier
  exists.

fravega:

- `sorting=HIGHEST_DISCOUNT` is the working URL param. `ordenar=`, `orderBy=`
  and `sort=` are silently ignored and fall back to sales ranking.
- The items query embeds a Buenos Aires postal code. Results from a US-based
  runner may differ from results in Argentina; worth checking if the hit list
  ever looks wrong.
