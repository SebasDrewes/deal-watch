import json
import urllib.error
import urllib.request

BROWSER = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
               "image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="124", "Not:A-Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}
MINIMAL = {"User-Agent": BROWSER["User-Agent"], "Accept-Language": "es-AR,es;q=0.9"}
BARE = {"User-Agent": "python-urllib/3"}

TARGETS = {
    "electrooutlet-root": "https://electrooutlet.com.ar/",
    "electrooutlet-listing": ("https://electrooutlet.com.ar/Item/Result?id=0&order=CustomDate"
                              "&sort=False&itemtype=Product&term=&getFilterData=True"
                              "&fields=Name&recsPerPage=50&filters=&page=1"),
    "fravega-root": "https://www.fravega.com/",
    "fravega-listing": "https://www.fravega.com/l/?promociones=electrofans&sorting=HIGHEST_DISCOUNT&page=1",
}

try:
    with urllib.request.urlopen("https://ipinfo.io/json", timeout=20) as r:
        info = json.load(r)
    print(f"runner IP: {info.get('ip')} {info.get('city')}/{info.get('country')} {info.get('org')}\n")
except Exception as exc:
    print(f"ip lookup failed: {exc}\n")

for profile_name, headers in (("bare", BARE), ("minimal", MINIMAL), ("browser", BROWSER)):
    for target, url in TARGETS.items():
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as resp:
                body = resp.read()
                result = f"{resp.status} ({len(body)} bytes)"
        except urllib.error.HTTPError as exc:
            server = exc.headers.get("server", "?")
            ray = exc.headers.get("cf-ray") or exc.headers.get("x-amz-cf-id") or ""
            result = f"HTTP {exc.code} via {server} {ray[:24]}"
        except Exception as exc:
            result = f"{type(exc).__name__}: {str(exc)[:60]}"
        print(f"{profile_name:<9} {target:<24} {result}")
