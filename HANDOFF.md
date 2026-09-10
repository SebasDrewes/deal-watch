# Where we left off

Last updated 2026-09-10. Read this first when picking the project back up.

## Current status: working, on the Mac

Two launchd agents in `~/electrooutlet-watch/` (the live copy; this repo is the
canonical source):

| Agent | Every | Scope |
|---|---|---|
| `com.sebasdrewes.electrooutlet-watch-quick` | 5 min | `--quick`: newest electrooutlet page + fravega |
| `com.sebasdrewes.electrooutlet-watch` | 3 hours | full catalogue crawl |

Serialized by a flock on `.watch.lock`. Emails sebas.drewes@gmail.com plus a
macOS notification. Gmail app password in Keychain service
`electrooutlet-watch-smtp`.

Track record: 406+ quick ticks, zero electrooutlet failures, real deals caught
and emailed unattended (a 77% Philco heater, a 78% Philco blender, an 8-item
batch). It works.

## Settled. Do not re-test these.

**Both sites block datacenter IPs. There is no hosted option.** Evidence in
README.md: 12/12 requests 403 from a GitHub runner across three header profiles,
plus headless Chromium failing to clear electrooutlet's Cloudflare challenge in
30 seconds, plus even `/robots.txt` returning 403. The same probes from the
Buenos Aires residential line return 200 on everything. Reproduce with
`tools/probe.py` and `tools/probe_browser.py` if ever needed.

Corollaries already worked through:

- A **VPN makes it worse**. AWS WAF's `AWSManagedRulesAnonymousIpList` targets
  "VPNs, proxies, Tor nodes, and web hosting providers" by name.
- The **AWS exclusion** (`HostingProviderIPList` "does not include AWS IP
  addresses") only ever helped fravega, which is on CloudFront. Electrooutlet is
  behind Cloudflare, with its own independent IP reputation. Untested, and moot
  if fravega is dropped.
- **electrooutlet has no sitemap or feed.** The listing pages are the only source.
- **Root is not needed** on Android. Termux runs this unprivileged; the script is
  pure stdlib and already reads the password from `ELECTROOUTLET_SMTP_PASSWORD`.

## Decisions taken

- Host on hardware at home, on the Argentine residential connection. That IP is
  the one asset that makes any of this work.
- Target platform: **an Android tablet via Termux**, unrooted. Free and already
  in-country. A $15 Pi Zero 2 W is the fallback if the tablet proves unreliable.
- Prefer `termux-job-scheduler` over a persistent loop, to cooperate with Doze
  instead of fighting it. Costs a **15-minute floor** (Android JobScheduler's
  minimum periodic interval) instead of 5 minutes. Acceptable.

## Pending work

1. **Port to the tablet.** Termux from F-Droid or GitHub, never the Play Store
   (separate codebases). `pkg install python`. Needs: `termux-job-scheduler`
   registration, `termux-notification` support in `notify_macos`, Termux:Boot for
   reboot survival. Android version determines the phantom-process fix: a
   Developer Options toggle ("Disable child process restrictions") on 14+, an ADB
   command on 12/13. See README and memory for the command.
2. **Decide fravega's fate.** Its robots.txt disallows `/*sorting=`, the exact
   parameter our efficient 2-page scan uses, and we currently send ~590 requests
   a day at it. Its `listPrice` anchors are also inflated, so its "70% off" is
   much weaker evidence than electrooutlet's. Options: switch to the allowed
   unsorted listing swept once daily, or drop the source. Electrooutlet's
   robots.txt permits everything and is where every real deal came from.
   Beware: Python's `urllib.robotparser` does NOT implement wildcards and wrongly
   reports that URL as allowed.
3. **Drop the quick cadence to 15 minutes.** Currently ~1,400 requests/day total,
   which is a lot to aim at a small retailer for a threshold that trips twice a
   day. Also aligns with the tablet's JobScheduler floor.
4. **Write state only when it changes.** Currently rewrites 178 KB every 5
   minutes regardless. Saves flash wear on a tablet or SD card.
5. **Clean up a leaked error.** `crawl_fravega` indexes
   `["__APOLLO_STATE__"]["ROOT_QUERY"]` directly, so a transient shape change
   surfaces as a bare `KeyError: 'ROOT_QUERY'` instead of a useful message.
   Seen for real 2026-09-10 03:26 to 03:40 during what looked like a fravega
   deploy; it self-recovered and electrooutlet was unaffected.

## Commands

    python watch.py                # full scan
    python watch.py --quick        # newest electrooutlet page only
    python watch.py --dry-run      # scan and print, send nothing, save nothing
    python watch.py --test-email
    python watch.py --bootstrap    # reset baseline to current stock, no alerts

    launchctl list | grep electrooutlet
    launchctl unload ~/Library/LaunchAgents/com.sebasdrewes.electrooutlet-watch-quick.plist
    tail -f ~/electrooutlet-watch/watch.log

Cadence lives in `StartInterval` in the two plists under
`~/Library/LaunchAgents/`. Threshold and sources live in `config.json`.
