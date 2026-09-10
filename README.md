# deal-watch

Emails me when something on a couple of Argentine retail sites drops to 70% off
or more.

## It cannot run on GitHub Actions

Both sites reject GitHub's runners outright. Measured with `tools/probe.py` on
2026-09-08, from a runner at 64.236.193.26 (Chicago, AS8075 Microsoft):

| headers | electrooutlet | fravega |
|---|---|---|
| bare urllib UA | 403 (cloudflare) | 403 (CloudFront) |
| minimal | 403 | 403 |
| full Chrome set, sec-ch-ua and all | 403 | 403 |

Twelve of twelve blocked. The same probe from a Buenos Aires residential
connection (AS16814) returns 200 on all twelve, including the bare
`python-urllib/3` User-Agent. So this is IP reputation or geo, not header
fingerprinting, and no amount of header spoofing fixes it.

A follow-up test (`tools/probe_browser.py`) ruled out the remaining hope, that
electrooutlet's Cloudflare response is a *solvable* challenge rather than a ban:

- plain urllib, `/robots.txt`: 403 `cf-mitigated: challenge` (even robots.txt)
- plain urllib, listing: 403 `cf-mitigated: challenge`
- headless Chromium via Playwright: challenge page loads and runs its JS
  (6.8 KB grows to 29 KB) but never clears over 30 seconds

So a real browser engine on a GitHub runner does not get through either. The
block is blanket and path-agnostic. Note that electrooutlet's robots.txt permits
all crawling, so this is generic IP-reputation bot protection rather than a
site policy against automated access.

`.github/workflows/watch.yml` is kept for reference but is **disabled**. Re-enable
it only from a runner with an Argentine, non-datacenter address.

The watcher therefore runs on a Mac in Argentina under launchd, on the two-tier
schedule below.

## Sources

**electrooutlet.com.ar** — the full catalogue (~920 items). Crawled twice per
full run: unfiltered, then `filters=5,|41|,` ("Productos Outlet"), which is a
strict subset. Set membership tags each hit `Productos Outlet` or `Primera`.
Discount comes from each card's `badge-dto percent`, with a `tachado` vs
`precio-final` fallback. Items with no `tachado` have no offer and are skipped.

**fravega.com** — the `electrofans` collection (~1400 items). Never crawled in
full: the listing carries a server-side discount facet, `descuento=desde-N-off`,
so the query returns only items already at or above the threshold. Two requests
covers all 18 current hits. Prices come from the `__NEXT_DATA__` Apollo state,
channel `fravega-ecommerce`. Product URLs are recovered by matching each sku
`code` against `/p/<slug>-<code>/` hrefs on the same page.

The filter buckets are fixed at 10..90 in steps of 10, so the code picks the
largest bucket at or below `threshold_pct` and filters the remainder client-side.
It refuses to run rather than pick a bucket above the threshold, which would
silently miss items.

Pagination uses the `total` the first page reports, because requesting a page
beyond the filtered result set makes fravega **silently drop the filter** and
return the unfiltered catalogue (`total` jumps to 10000). A page whose `total`
disagrees with the first page's is treated as filter loss and stops the walk.

## Two-tier schedule

| Tier | Every | Work | Catches |
|---|---|---|---|
| quick | 5 min | 4 requests, ~8s | new listings, and every Fravega change |
| full | 3 hours | 33 requests, ~90s | price drops on older electrooutlet stock |

Two launchd agents run these. A `flock` on `.watch.lock` keeps them from racing
on state: if the full tier is mid-run, the quick tick logs and exits.

Quick mode scans only electrooutlet's newest page, which is sound because the
listing is ordered `CustomDate` descending, so anything newly published is on
page 1. An item in the newest 50 overall that is an outlet item is necessarily
in the newest 50 outlet items, so the Outlet/Primera tag stays correct.

Fravega needs no full tier at all: sorting by discount means 2 pages already are
the complete set of items above the threshold.


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

- `descuento=desde-N-off` is the discount facet and is permitted by robots.txt.
  `sorting=HIGHEST_DISCOUNT` also works and returns the same items, but
  robots.txt disallows `/*sorting=`, so the facet is the right door.
- Careful: Python's `urllib.robotparser` does NOT implement `*` wildcards. It
  reports `/*sorting=` URLs as allowed, which is wrong. Match with a real
  wildcard-aware check.
- Requesting a page past the end of a filtered result set drops the filter
  instead of returning empty. Always paginate against the reported `total`.
- The items query embeds a Buenos Aires postal code. Results from a US-based
  runner may differ from results in Argentina; worth checking if the hit list
  ever looks wrong.
