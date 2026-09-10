#!/usr/bin/env python3
"""Watch electrooutlet.com.ar and fravega.com and alert on listings at or above a discount threshold."""

import argparse
import fcntl
import gzip
import html
import json
import os
import re
import smtplib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

HOME = Path(__file__).resolve().parent
STATE_PATH = Path(os.environ.get("DEAL_WATCH_STATE") or HOME / "state.json")
STATE_FIELDS = ("source", "name", "pct", "alerted_pct", "first_seen", "last_seen")
CONFIG_PATH = HOME / "config.json"
LOG_PATH = HOME / "watch.log"
LOCK_PATH = HOME / ".watch.lock"

ORIGIN = "https://electrooutlet.com.ar"
FRAVEGA = "https://www.fravega.com"
FRAVEGA_LISTING = FRAVEGA + "/l/?promociones={collection}&descuento=desde-{bucket}-off&page={page}"
FRAVEGA_BUCKETS = (10, 20, 30, 40, 50, 60, 70, 80, 90)
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
LISTING = (
    ORIGIN + "/Item/Result?id=0&order=CustomDate&sort=False&itemtype=Product"
    "&term=&getFilterData=True&fields=Name,%20Code,%20Keywords"
    "&recsPerPage=50&filters={filters}&page={page}"
)
FILTER_OUTLET = "5,|41|,"
FILTER_ALL = ""

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

DEFAULTS = {
    "threshold_pct": 70,
    "email_to": "",
    "smtp_user": "",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "keychain_service": "electrooutlet-watch-smtp",
    "min_expected_items": 300,
    "max_pages": 40,
    "request_delay_sec": 0.4,
    "prune_after_days": 60,
    "stale_run_hours": 24,
    "fravega_collection": "electrofans",
    "fravega_max_pages": 15,
    "sources": {"electrooutlet": True, "fravega": True},
}

CARD_RE = re.compile(r'<article class="PRODUCT_BOX product-id-(\d+)(.*?)</article>', re.S)


def log(msg):
    line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    for key, env in (("email_to", "DEAL_WATCH_EMAIL_TO"),
                     ("smtp_user", "DEAL_WATCH_SMTP_USER"),
                     ("fravega_collection", "DEAL_WATCH_FRAVEGA_COLLECTION")):
        value = os.environ.get(env)
        if value:
            cfg[key] = value
    threshold = os.environ.get("DEAL_WATCH_THRESHOLD")
    if threshold:
        cfg["threshold_pct"] = int(threshold)
    return cfg


def load_state():
    if not STATE_PATH.exists():
        return {"items": {}, "last_success": None}
    opener = gzip.open if STATE_PATH.suffix == ".gz" else open
    with opener(STATE_PATH, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state):
    slim = {
        "items": {
            key: {f: rec.get(f) for f in STATE_FIELDS if rec.get(f) is not None}
            for key, rec in state["items"].items()
        },
        "last_success": state.get("last_success"),
    }
    payload = json.dumps(slim, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    if STATE_PATH.suffix == ".gz":
        with open(tmp, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                gz.write(payload.encode("utf-8"))
    else:
        tmp.write_text(payload, encoding="utf-8")
    tmp.replace(STATE_PATH)


def fetch(url, attempts=4):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es-AR,es;q=0.9"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(2 ** i)
    raise RuntimeError(f"fetch failed after {attempts} attempts: {url}: {last}")


def _num(raw):
    if not raw:
        return None
    try:
        return float(raw.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def parse_items(page_html):
    items = []
    for pid, body in CARD_RE.findall(page_html):
        def find(pattern):
            m = re.search(pattern, body, re.S)
            return html.unescape(m.group(1).strip()) if m else None

        url = find(r'<a class="onbody" href="([^"]+)"') or find(r'<a class="onbody showSecondImage" href="([^"]+)"')
        if url:
            url = ORIGIN + quote(url, safe="/:?=&%")
        price = _num(find(r'class="precio-final"[^>]*>([\d.,]+)'))
        list_price = _num(find(r'class="tachado">\s*\$\s*<span>([\d.,]+)'))
        badge = find(r'class="badge-dto percent">\s*<strong>(\d+)<small>%')

        pct = int(badge) if badge else None
        if pct is None and price and list_price and list_price > 0:
            pct = round((1 - price / list_price) * 100)

        items.append({
            "id": pid,
            "name": find(r'<h3 itemprop="name" class="hidden-xs hidden-ms">(.*?)</h3>'),
            "sku": find(r'data-sku="([^"]*)"'),
            "brand": find(r'data-brand="([^"]*)"'),
            "category": find(r'data-category="([^"]*)"'),
            "url": url,
            "price": price,
            "list_price": list_price,
            "discount_pct": pct,
            "in_stock": 'price_wrapper nonavailable' not in body,
        })
    return items


def crawl(filters, cfg, ids_only=False, max_pages=None):
    seen = {}
    complete = False
    for page in range(1, (max_pages or cfg["max_pages"]) + 1):
        page_html = fetch(LISTING.format(filters=filters, page=page))
        items = parse_items(page_html)
        if not items:
            complete = True
            break
        fresh = [i for i in items if i["id"] not in seen]
        if not fresh:
            complete = True
            break
        for item in items:
            seen.setdefault(item["id"], item["id"] if ids_only else item)
        time.sleep(cfg["request_delay_sec"])
    return seen, complete


def _fravega_page(collection, bucket, page):
    raw = fetch(FRAVEGA_LISTING.format(collection=collection, bucket=bucket, page=page))
    match = NEXT_DATA.search(raw)
    if not match:
        raise RuntimeError("fravega: no __NEXT_DATA__ in the page, layout changed")
    state = json.loads(match.group(1))["props"]["pageProps"].get("__APOLLO_STATE__")
    if not isinstance(state, dict) or "ROOT_QUERY" not in state:
        raise RuntimeError("fravega: __APOLLO_STATE__ has no ROOT_QUERY, page shape "
                           "changed or the site is mid-deploy")
    root = state["ROOT_QUERY"]
    items_key = next((k for k in root if k.startswith("items(")), None)
    if items_key is None:
        raise RuntimeError("fravega: no items() in the Apollo state, query shape changed")
    node = root[items_key]
    results_key = next((k for k in node if k.startswith("results(")), None)
    return node.get("total"), (node[results_key] if results_key else []), raw


def _fravega_parse(batch, raw, collection, threshold):
    hrefs = {}
    for href in set(re.findall(r'href="(/p/[^"]+)"', raw)):
        code = re.search(r"-(\d+)/$", href)
        if code:
            hrefs[code.group(1)] = href

    out = []
    for entry in batch:
        skus = entry.get("skus", {}).get("results") or []
        if not skus:
            continue
        sku = skus[0]
        pricing_key = next(
            (k for k in sku if k.startswith("pricing(") and "fravega-ecommerce" in k), None)
        prices = (sku.get(pricing_key) or []) if pricing_key else []
        if not prices:
            continue
        info = prices[0]
        list_price, sale_price = info.get("listPrice"), info.get("salePrice")
        pct = info.get("discount")
        if pct is None and list_price and sale_price:
            pct = round((1 - sale_price / list_price) * 100)
        if pct is None or pct < threshold:
            continue

        code = sku.get("code")
        href = hrefs.get(code)
        category = None
        cat_key = next((k for k in sku if k.startswith("categorization(")), None)
        if cat_key and sku.get(cat_key):
            chain = sku[cat_key][0]
            if chain:
                category = chain[0].get("name")

        out.append({
            "source": "fravega",
            "id": entry.get("id"),
            "name": entry.get("title"),
            "sku": code,
            "brand": (entry.get("brand") or {}).get("name"),
            "category": category,
            "url": FRAVEGA + href if href else FRAVEGA + f"/l/?promociones={collection}",
            "price": float(sale_price) if sale_price else None,
            "list_price": float(list_price) if list_price else None,
            "discount_pct": pct,
            "tag": collection,
            "in_stock": True,
        })
    return out


def crawl_fravega(cfg):
    collection = cfg["fravega_collection"]
    threshold = cfg["threshold_pct"]
    usable = [b for b in FRAVEGA_BUCKETS if b <= threshold]
    if not usable:
        raise RuntimeError(
            f"fravega: threshold {threshold}% is below its smallest discount filter "
            f"({FRAVEGA_BUCKETS[0]}%); a higher filter would silently miss items. "
            "Raise threshold_pct or set sources.fravega false")
    bucket = max(usable)

    total, batch, raw = _fravega_page(collection, bucket, 1)
    if not batch:
        return [], 1, bucket

    found = _fravega_parse(batch, raw, collection, threshold)
    seen = {e.get("id") for e in batch}
    page_size = len(batch)
    pages = 1

    expected = 1
    if isinstance(total, int) and total > 0 and page_size:
        expected = min(-(-total // page_size), cfg["fravega_max_pages"])

    for page in range(2, expected + 1):
        time.sleep(cfg["request_delay_sec"])
        page_total, batch, raw = _fravega_page(collection, bucket, page)
        pages += 1
        if page_total != total:
            log(f"fravega: page {page} reported total {page_total} not {total}, "
                "filter was dropped; stopping")
            break
        fresh = [e for e in batch if e.get("id") not in seen]
        if not fresh:
            break
        seen.update(e.get("id") for e in fresh)
        found += _fravega_parse(fresh, raw, collection, threshold)

    return found, pages, bucket


def crawl_electrooutlet(cfg, quick=False):
    if quick:
        all_items, _ = crawl(FILTER_ALL, cfg, max_pages=1)
        outlet_ids, _ = crawl(FILTER_OUTLET, cfg, ids_only=True, max_pages=1)
        if len(all_items) < 40:
            raise RuntimeError(
                f"electrooutlet quick scan returned only {len(all_items)} items on page 1")
    else:
        all_items, all_complete = crawl(FILTER_ALL, cfg)
        outlet_ids, _ = crawl(FILTER_OUTLET, cfg, ids_only=True)
        if not all_complete or len(all_items) < cfg["min_expected_items"]:
            raise RuntimeError(
                f"electrooutlet crawl looks wrong: {len(all_items)} items, "
                f"complete={all_complete} (expected >= {cfg['min_expected_items']})")

    priced = [i for i in all_items.values() if i["discount_pct"] is not None]
    if len(priced) < len(all_items) // 3:
        raise RuntimeError(
            f"electrooutlet: only {len(priced)}/{len(all_items)} items had a parseable "
            "discount, the site markup probably changed")

    out = []
    for item in all_items.values():
        item["source"] = "electrooutlet"
        item["tag"] = "Productos Outlet" if item["id"] in outlet_ids else "Primera"
        out.append(item)
    return out


def notify_macos(title, subtitle, message, url=None):
    if sys.platform != "darwin":
        return
    try:
        tn = subprocess.run(["which", "terminal-notifier"], capture_output=True, text=True)
    except OSError:
        return
    if tn.returncode == 0:
        cmd = [tn.stdout.strip(), "-title", title, "-subtitle", subtitle,
               "-message", message, "-sound", "Glass"]
        if url:
            cmd += ["-open", url]
        subprocess.run(cmd, capture_output=True)
        return
    script = (
        f'display notification {json.dumps(message)} with title {json.dumps(title)} '
        f'subtitle {json.dumps(subtitle)} sound name "Glass"'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True)
    except OSError:
        pass


def smtp_password(cfg):
    env = os.environ.get("ELECTROOUTLET_SMTP_PASSWORD")
    if env:
        return env
    res = subprocess.run(
        ["security", "find-generic-password", "-a", cfg["smtp_user"],
         "-s", cfg["keychain_service"], "-w"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(
            "no SMTP password: add one with\n"
            f'  security add-generic-password -a {cfg["smtp_user"]} '
            f'-s {cfg["keychain_service"]} -w "<gmail app password>"'
        )
    return res.stdout.strip()


def send_email(cfg, subject, text_body, html_body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["smtp_user"]
    msg["To"] = cfg["email_to"]
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=45) as smtp:
        smtp.starttls()
        smtp.login(cfg["smtp_user"], smtp_password(cfg))
        smtp.send_message(msg)


def money(value):
    return f"${value:,.0f}".replace(",", ".") if value else "?"


def render(hits, cfg):
    by_source = {}
    for hit in hits:
        by_source.setdefault(hit["item"]["source"], []).append(hit)

    rows_txt, rows_html = [], []
    for source, group in by_source.items():
        label = SOURCE_LABEL.get(source, source)
        rows_txt.append(f"== {label} ({len(group)}) ==\n")
        rows_html.append(
            f'<tr><td colspan="2" style="padding:16px 12px 6px;font-size:12px;'
            f'font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#666;'
            f'border-bottom:1px solid #e5e5e5">{html.escape(label)} ({len(group)})</td></tr>')
        for hit in group:
            item, reason = hit["item"], hit["reason"]
            stock = "" if item.get("in_stock", True) else "  [SIN STOCK]"
            rows_txt.append(
                f"{item['discount_pct']}% OFF  {money(item['price'])} "
                f"(antes {money(item['list_price'])})  [{item.get('tag') or ''}]{stock}\n"
                f"  {item['name']}\n  {reason}\n  {item['url']}\n")
            rows_html.append(
                f'<tr><td style="padding:8px 12px;font-size:22px;font-weight:700;color:#b00020;'
                f'vertical-align:top">{item["discount_pct"]}%</td>'
                f'<td style="padding:8px 12px">'
                f'<a href="{item["url"]}" style="font-size:15px;font-weight:600;color:#0b57d0;'
                f'text-decoration:none">{html.escape(item["name"] or "?")}</a><br>'
                f'<span style="font-size:15px"><strong>{money(item["price"])}</strong> '
                f'<span style="color:#666;text-decoration:line-through">'
                f'{money(item["list_price"])}</span></span><br>'
                f'<span style="font-size:12px;color:#666">{html.escape(item.get("tag") or "")}'
                f' &middot; {html.escape(item.get("brand") or "?")}'
                f' &middot; {html.escape(item.get("category") or "?")}'
                f' &middot; {html.escape(reason)}'
                f'{"" if item.get("in_stock", True) else " &middot; SIN STOCK"}</span>'
                f'</td></tr>')

    n = len(hits)
    top = hits[0]["item"]
    where = SOURCE_LABEL.get(top["source"], top["source"])
    subject = (f"{n} item{'s' if n != 1 else ''} at {cfg['threshold_pct']}%+ off "
               f"- {top['discount_pct']}% {(top['name'] or '')[:45]} ({where})")
    text = f"{n} item(s) at {cfg['threshold_pct']}%+ off\n\n" + "\n".join(rows_txt)
    html_doc = (
        '<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:640px">'
        f'<h2 style="font-size:17px">{n} item(s) at {cfg["threshold_pct"]}%+ off</h2>'
        '<table style="border-collapse:collapse;width:100%">' + "".join(rows_html) + "</table>"
        '<p style="font-size:11px;color:#999;margin-top:18px">deal-watch &middot; '
        f'{datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")}</p></div>')
    return subject, text, html_doc


SOURCE_LABEL = {"electrooutlet": "ElectroOutlet", "fravega": "Fravega"}


def collect(cfg, args):
    gathered, failures = [], []
    quick = getattr(args, "quick", False)
    if cfg["sources"].get("electrooutlet", True):
        try:
            items = crawl_electrooutlet(cfg, quick=quick)
            gathered += items
            log(f"electrooutlet: {len(items)} items scanned"
                + (" (quick: newest page only)" if quick else ""))
        except Exception as exc:
            failures.append(f"electrooutlet: {exc}")
            log(f"electrooutlet FAILED: {exc}")
    if cfg["sources"].get("fravega", True):
        try:
            items, pages, bucket = crawl_fravega(cfg)
            gathered += items
            log(f"fravega: {len(items)} items at >={cfg['threshold_pct']}% "
                f"({pages} page(s) of '{cfg['fravega_collection']}', "
                f"filter desde-{bucket}-off)")
        except Exception as exc:
            failures.append(f"fravega: {exc}")
            log(f"fravega FAILED: {exc}")
    return gathered, failures


def run(cfg, args):
    state = load_state()
    migrate_state(state)
    now = datetime.now(timezone.utc)
    threshold = cfg["threshold_pct"]

    items, failures = collect(cfg, args)
    if failures and not items:
        raise RuntimeError("; ".join(failures))

    scanned_sources = {i["source"] for i in items}
    first_run = not state["items"]
    hits = []

    for item in items:
        key = f"{item['source']}:{item['id']}"
        prev = state["items"].get(key, {})
        pct = item["discount_pct"]
        record = {
            "source": item["source"], "name": item["name"], "url": item["url"],
            "tag": item.get("tag"), "price": item["price"],
            "list_price": item["list_price"], "pct": pct,
            "first_seen": prev.get("first_seen") or now.date().isoformat(),
            "last_seen": now.date().isoformat(),
            "alerted_pct": prev.get("alerted_pct"),
        }

        if pct is not None and pct >= threshold:
            alerted = prev.get("alerted_pct")
            if not prev:
                reason = "initial scan" if first_run else "newly listed"
            elif alerted is None:
                was = prev.get("pct")
                if was is not None and was >= threshold:
                    reason = "pending from an earlier run"
                else:
                    reason = f"price drop, was {was}% off"
            elif pct > alerted:
                reason = f"discount deepened from {alerted}%"
            else:
                reason = None
            if reason:
                item["reason"] = reason
                hits.append({"item": item, "reason": reason})
        else:
            record["alerted_pct"] = None

        state["items"][key] = record

    seen_keys = {f"{i['source']}:{i['id']}" for i in items}
    for key, record in state["items"].items():
        source = record.get("source")
        if source in scanned_sources and source != "electrooutlet" and key not in seen_keys:
            record["alerted_pct"] = None

    cutoff = (now - timedelta(days=cfg["prune_after_days"])).date().isoformat()
    state["items"] = {k: v for k, v in state["items"].items() if v["last_seen"] >= cutoff}
    state["last_success"] = now.isoformat(timespec="seconds")

    hits.sort(key=lambda h: -h["item"]["discount_pct"])
    log(f"{len(hits)} new hit(s) at >={threshold}%"
        + (" (first run)" if first_run else ""))

    emailed = False
    if hits and not args.dry_run:
        subject, text, html_doc = render(hits, cfg)
        top = hits[0]["item"]
        try:
            send_email(cfg, subject, text, html_doc)
            emailed = True
            log(f"emailed {cfg['email_to']}: {subject}")
        except Exception as exc:
            log(f"EMAIL FAILED, hits stay pending for the next run: {exc}")
            notify_macos("Deal watch: email failed", str(exc)[:80],
                         f"{len(hits)} hit(s) found, will retry next run")
        notify_macos(
            f"{top['discount_pct']}% off - {money(top['price'])}",
            (top["name"] or "")[:70],
            f"{len(hits)} item(s) at {threshold}%+ off" if len(hits) > 1
            else f"{hits[0]['reason']} - was {money(top['list_price'])}",
            top["url"],
        )
    elif hits:
        subject, text, _ = render(hits, cfg)
        print("\n--- DRY RUN, would send ---\n" + subject + "\n\n" + text)

    if emailed:
        for hit in hits:
            key = f"{hit['item']['source']}:{hit['item']['id']}"
            state["items"][key]["alerted_pct"] = hit["item"]["discount_pct"]

    if not args.dry_run:
        save_state(state)

    if failures:
        log("partial run, some sources failed: " + "; ".join(failures))
    return len(hits)


def migrate_state(state):
    if any(":" in k for k in state["items"]):
        return
    if not state["items"]:
        return
    state["items"] = {
        f"electrooutlet:{k}": dict(v, source="electrooutlet", tag=v.get("slice"))
        for k, v in state["items"].items()
    }
    log(f"migrated {len(state['items'])} state entries to source-prefixed keys")


def acquire_lock():
    handle = open(LOCK_PATH, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="scan and print, send nothing, save nothing")
    ap.add_argument("--threshold", type=int, help="override discount threshold percent")
    ap.add_argument("--bootstrap", action="store_true",
                    help="record current catalog as the baseline without alerting")
    ap.add_argument("--test-email", action="store_true", help="send one test email and exit")
    ap.add_argument("--quick", action="store_true",
                    help="electrooutlet: scan only the newest page instead of the full "
                         "catalogue; catches new listings, misses price drops on old stock")
    args = ap.parse_args()

    cfg = load_config()
    if args.threshold:
        cfg["threshold_pct"] = args.threshold

    lock = acquire_lock()
    if lock is None:
        log("another run holds the lock, skipping this tick")
        return 0

    if args.test_email:
        send_email(cfg, "deal-watch test",
                   "Plain text test.", "<p>HTML test. Setup works.</p>")
        notify_macos("Deal watch", "Test", "Email and notification both work.")
        log(f"test email sent to {cfg['email_to']}")
        return 0

    try:
        if args.bootstrap:
            state = load_state()
            migrate_state(state)
            items, failures = collect(cfg, args)
            if failures:
                raise RuntimeError("; ".join(failures))
            now = datetime.now(timezone.utc).date().isoformat()
            state["items"] = {
                f"{i['source']}:{i['id']}": {
                    "source": i["source"], "name": i["name"], "url": i["url"],
                    "tag": i.get("tag"), "price": i["price"],
                    "list_price": i["list_price"], "pct": i["discount_pct"],
                    "first_seen": now, "last_seen": now,
                    "alerted_pct": i["discount_pct"]
                    if (i["discount_pct"] or 0) >= cfg["threshold_pct"] else None,
                }
                for i in items
            }
            state["last_success"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            save_state(state)
            log(f"bootstrapped {len(state['items'])} items as baseline, no alerts sent")
            return 0

        run(cfg, args)
        return 0
    except Exception as exc:
        log(f"ERROR: {exc}")
        state = load_state()
        last = state.get("last_success")
        stale = True
        if last:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
            stale = age > timedelta(hours=cfg["stale_run_hours"])
        if stale:
            notify_macos("Deal watch is broken",
                         f"no successful run since {last or 'ever'}", str(exc)[:120])
        return 1


if __name__ == "__main__":
    sys.exit(main())
