import json
import re
import sys
import urllib.error
import urllib.request

LISTING = ("https://electrooutlet.com.ar/Item/Result?id=0&order=CustomDate&sort=False"
           "&itemtype=Product&term=&getFilterData=True&fields=Name&recsPerPage=50"
           "&filters=&page=1")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

try:
    with urllib.request.urlopen("https://ipinfo.io/json", timeout=20) as r:
        info = json.load(r)
    print(f"exit IP: {info.get('ip')} {info.get('city')}/{info.get('country')}\n")
except Exception as exc:
    print(f"ip lookup failed: {exc}\n")

print("--- 1. plain urllib, static paths ---")
for name, url in (("robots.txt", "https://electrooutlet.com.ar/robots.txt"),
                  ("listing", LISTING)):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40) as resp:
            body = resp.read()
            print(f"  {name:<12} {resp.status} ({len(body)} bytes)")
    except urllib.error.HTTPError as exc:
        print(f"  {name:<12} HTTP {exc.code} cf-mitigated={exc.headers.get('cf-mitigated')}")

print("\n--- 2. headless chromium via playwright ---")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("  playwright not installed")
    sys.exit(0)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(user_agent=UA, locale="es-AR",
                              viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        resp = page.goto(LISTING, wait_until="domcontentloaded", timeout=60000)
        print(f"  initial status: {resp.status if resp else '?'}")
        for attempt in range(6):
            html = page.content()
            cards = len(re.findall(r"PRODUCT_BOX product-id-(\d+)", html))
            challenged = "Just a moment" in html or "challenge" in html.lower()
            print(f"  t+{attempt * 5}s  cards={cards}  challenge_page={challenged}  len={len(html)}")
            if cards:
                print(f"\n  SUCCESS: {cards} product cards rendered")
                ids = re.findall(r"PRODUCT_BOX product-id-(\d+)", html)[:5]
                print(f"  sample ids: {ids}")
                cookies = [c["name"] for c in ctx.cookies()]
                print(f"  cookies: {cookies}")
                break
            page.wait_for_timeout(5000)
        else:
            print("\n  FAILED: challenge never cleared")
            print("  body excerpt:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.content()))[:200])
    except Exception as exc:
        print(f"  navigation error: {type(exc).__name__}: {str(exc)[:120]}")
    browser.close()
